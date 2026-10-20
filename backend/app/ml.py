"""Anomali tespiti ve sağlık puanlaması.

Her varlık türü için, fizik motorunun arızasız çalıştırmalarından, izlenen
her sensörün mevcut işletme koşullarında (hız, yük, ortam sıcaklığı, ...)
ne okuması *gerektiğini* öğreniriz. Canlı okumalar ardından iki sinyalle
puanlanır:

* sensör başına artık z-puanları, "daha kötü" anlamına gelen yönde
  (daha sıcak rulman, daha düşük fren basıncı, ...) – açıklanabilir, sebebi verir;
* tüm artık vektörü üzerinde bir IsolationForest – tek bir sensörün
  işaretlemediği kombinasyonları yakalar.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor, IsolationForest
from sklearn.linear_model import Ridge

from twin_core import TwinEngine

log = logging.getLogger(__name__)

EWMA_TAU_S = 600.0  # simüle saniye; rulmanın ısıl zaman sabitiyle eşleşir
WARMUP_TAUS = 2.0  # hareketli ortalamaların güvenilir olması için bu kadar zaman sabiti geçmiş gerekir


@dataclass(frozen=True)
class Monitored:
    metric: str
    direction: int  # +1: yüksek değer daha kötü, -1: düşük değer daha kötü
    # Bu sensörü fiziksel olarak belirleyen işletme koşulları. Kümeyi küçük
    # tutmak, verinin seyrek olduğu yerlerde öğrenilen "beklenen değeri" isabetli kılar.
    conditions: tuple[str, ...]
    # Ham değer yerine log(value + log_offset) modellenir, böylece artıklar
    # görelidir. Ofset, sıfıra yakın okumaların baskın olmasını önler.
    log_offset: float | None = None


@dataclass(frozen=True)
class TypeSpec:
    monitored: tuple[Monitored, ...]
    ewma: tuple[str, ...] = ()


SPECS: dict[str, TypeSpec] = {
    "train": TypeSpec(
        monitored=(
            Monitored("bearing_temp_c", +1,
                      ("speed_kmh_ewma", "passenger_load_pct_ewma", "ambient_c_ewma")),
            Monitored("bogie_vibration_mm_s", +1, ("speed_kmh",)),
            Monitored("brake_pressure_bar", -1, ("ambient_c",)),
            Monitored("motor_current_a", +1, ("traction_power_kw",), log_offset=300.0),
        ),
        ewma=("speed_kmh", "passenger_load_pct", "ambient_c"),
    ),
    "segment": TypeSpec(
        monitored=(
            Monitored("vibration_rms", +1, ("passing_speed_kmh",)),
            Monitored("geometry_dev_mm", +1, ("ambient_c",)),
        ),
    ),
    "switch": TypeSpec(
        monitored=(
            Monitored("throw_time_s", +1, ("throw_ambient_c",)),
            Monitored("motor_current_peak_a", +1, ("throw_ambient_c",)),
        ),
    ),
}

Z_ONSET = 3.0  # bu kadar sigmanın altındaki artıklar gürültü sayılır
Z_SPAN = 12.0  # varlığın tamamen anormal sayıldığı, başlangıç eşiğinin üzerindeki sigma


class FeatureBuilder:
    """Ham okumaları model girdilerine dönüştürür; varlık başına hareketli ortalamaları tutar."""

    def __init__(self, time_scale: float = 1.0):
        self.time_scale = time_scale
        self._ewma: dict[tuple[str, str], float] = {}
        self._history_s: dict[str, float] = {}

    def is_warm(self, asset_id: str, asset_type: str) -> bool:
        """Bir varlığın hareketli ortalamaları hâlâ çok az geçmişe dayanıyorsa False döner."""
        if not SPECS[asset_type].ewma:
            return True
        return self._history_s.get(asset_id, 0.0) >= WARMUP_TAUS * EWMA_TAU_S

    def update(self, reading: dict, dt_real: float) -> dict[str, float]:
        spec = SPECS[reading["type"]]
        features = dict(reading["metrics"])
        dt_sim = dt_real * self.time_scale
        alpha = min(1.0, dt_sim / EWMA_TAU_S)
        if spec.ewma:
            asset_id = reading["asset_id"]
            history = self._history_s.get(asset_id, 0.0) + dt_sim
            self._history_s[asset_id] = history
            # Tam bir zaman sabiti kadar geçmiş birikene dek düz hareketli ortalamayı kullan:
            # EWMA'yı ilk örnekle başlatmak onu uzun süre yanlı kılar.
            alpha = max(alpha, min(1.0, dt_sim / history))
        for name in spec.ewma:
            key = (reading["asset_id"], name)
            value = features[name]
            prev = self._ewma.get(key, value)
            self._ewma[key] = prev + alpha * (value - prev)
            features[f"{name}_ewma"] = self._ewma[key]
        return features


@dataclass
class Score:
    anomaly: float
    cause: str | None
    z: dict[str, float] = field(default_factory=dict)


class TrendPlusTrees:
    """Doğrusal eğilim + eğilimin kaçırdığı kısım üzerinde gradyan artırmalı ağaçlar.

    Ağaçlar tek başına, eğitildikleri aralığın dışında düz bir çizgi tahmin eder;
    doğrusal kısım fiziği (daha sıcak ortam -> daha sıcak rulman) referans
    verinin nadiren uğradığı köşelere taşır."""

    def __init__(self, seed: int, max_iter: int = 150, max_leaf_nodes: int = 31):
        self.trend = Ridge(alpha=1e-3)
        self.trees = HistGradientBoostingRegressor(max_iter=max_iter, max_leaf_nodes=max_leaf_nodes,
                                                   random_state=seed)

    def fit(self, x: np.ndarray, y: np.ndarray) -> "TrendPlusTrees":
        self.trend.fit(x, y)
        self.trees.fit(x, y - self.trend.predict(x))
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return self.trend.predict(x) + self.trees.predict(x)


class TypeModel:
    def __init__(self, spec: TypeSpec):
        self.spec = spec
        self.regressors: list[TrendPlusTrees] = []
        # Sensör gürültüsü sabit değildir (motor akımı gürültüsü akımla artar),
        # bu yüzden ikinci bir model koşul başına beklenen artık büyüklüğünü öğrenir.
        self.scale_models: list[HistGradientBoostingRegressor] = []
        self.sigma = np.ones(len(spec.monitored))  # raporlama ve alt sınır için genel, dayanıklı sigma
        self.forest: IsolationForest | None = None
        self.if_threshold = 0.0
        self.if_span = 1.0

    def _xy(self, rows: list[dict[str, float]]) -> tuple[list[np.ndarray], np.ndarray]:
        xs = [np.array([[r[c] for c in m.conditions] for r in rows], dtype=float) for m in self.spec.monitored]
        y = np.array(
            [[math.log(max(0.0, r[m.metric]) + m.log_offset) if m.log_offset else r[m.metric]
              for m in self.spec.monitored]
             for r in rows],
            dtype=float,
        )
        return xs, y

    def _residuals(self, xs: list[np.ndarray], y: np.ndarray) -> np.ndarray:
        return y - np.column_stack([reg.predict(x) for reg, x in zip(self.regressors, xs)])

    def _z(self, xs: list[np.ndarray], y: np.ndarray) -> np.ndarray:
        # Gauss gürültüsü için sigma = E|r| * sqrt(pi/2).
        local = np.column_stack([m.predict(x) for m, x in zip(self.scale_models, xs)]) * 1.2533
        sigma = np.maximum(local, 0.5 * self.sigma)
        return self._residuals(xs, y) / sigma

    def fit(self, rows: list[dict[str, float]], seed: int = 0) -> None:
        xs, y = self._xy(rows)
        self.regressors, self.scale_models = [], []
        for j, x in enumerate(xs):
            self.regressors.append(TrendPlusTrees(seed).fit(x, y[:, j]))
        resid = self._residuals(xs, y)
        mad = np.median(np.abs(resid - np.median(resid, axis=0)), axis=0)
        self.sigma = np.maximum(1.4826 * mad, 1e-3)
        for j, x in enumerate(xs):
            scale = HistGradientBoostingRegressor(max_iter=60, max_leaf_nodes=15, random_state=seed)
            scale.fit(x, np.abs(resid[:, j]))
            self.scale_models.append(scale)

        z = self._z(xs, y)
        self.forest = IsolationForest(n_estimators=150, random_state=seed).fit(z)
        scores = self.forest.score_samples(z)
        self.if_threshold = float(np.percentile(scores, 1))
        median = float(np.median(scores))
        self.if_span = max(3.0 * (median - self.if_threshold), 1e-3)

    def score(self, rows: list[dict[str, float]]) -> list[Score]:
        if not rows:
            return []
        xs, y = self._xy(rows)
        z = self._z(xs, y)
        directions = np.array([m.direction for m in self.spec.monitored])
        zdir = z * directions
        a_z = np.clip((zdir.max(axis=1) - Z_ONSET) / Z_SPAN, 0.0, 1.0)
        a_if = np.clip((self.if_threshold - self.forest.score_samples(z)) / self.if_span, 0.0, 1.0)
        anomaly = np.maximum(a_z, 0.6 * a_if)

        out = []
        names = [m.metric for m in self.spec.monitored]
        for i in range(len(rows)):
            j = int(np.argmax(zdir[i]))
            cause = names[j] if zdir[i, j] > 2.0 else None
            if cause is None and a_if[i] > 0.2:
                cause = names[int(np.argmax(np.abs(z[i])))]
            out.append(Score(float(anomaly[i]), cause, {n: round(float(v), 2) for n, v in zip(names, z[i])}))
        return out


def generate_baseline(steps: int, seed: int = 7) -> dict[str, list[dict[str, float]]]:
    """Motoru değişen hava ve talep koşullarında arızasız çalıştırır; öznitelikleri toplar."""
    rng = random.Random(seed)
    engine = TwinEngine(seed=seed, time_scale=1.0)
    builder = FeatureBuilder(time_scale=1.0)
    rows: dict[str, list[dict[str, float]]] = {t: [] for t in SPECS}
    # Sıcaklık x yük aralıklarına göre katmanlı (karıştırılmış), her biri 1–2,5
    # ısıl zaman sabiti boyunca tutulur; böylece ortalama öznitelikler (ambient_c_ewma, ...)
    # panonun izin verdiği her sıcak/soğuk/boş/dolu karışımına, hatta daha fazlasına ulaşır.
    temp_edges = np.linspace(-25.0, 52.0, 12)
    load_edges = np.linspace(0.05, 1.3, 5)
    cells = [(t, l) for t in range(len(temp_edges) - 1) for l in range(len(load_edges) - 1)]
    rng.shuffle(cells)
    warmup = 900
    next_change, cell = 0, 0
    for i in range(steps + warmup):
        if i >= next_change:
            t, l = cells[cell % len(cells)]
            cell += 1
            engine.set_env(ambient_c=rng.uniform(temp_edges[t], temp_edges[t + 1]),
                           load_factor=rng.uniform(load_edges[l], load_edges[l + 1]))
            next_change = i + rng.randint(600, 1500)
        for reading in engine.step(1.0):
            features = builder.update(reading, 1.0)
            if i >= warmup and i % 3 == 0:
                rows[reading["type"]].append(features)
    return rows


MODEL_VERSION = 5  # SPECS, öznitelikler veya motorun fiziği değiştiğinde artır


class TwinModels:
    def __init__(self):
        self.models: dict[str, TypeModel] = {}
        self.ready = False

    def load_or_train(self, steps: int, cache_dir: str | None, seed: int = 7) -> None:
        path = Path(cache_dir) / f"models-v{MODEL_VERSION}-{steps}-{seed}.joblib" if cache_dir else None
        if path and path.exists():
            try:
                self.models = joblib.load(path)
                self.ready = True
                log.info("Eğitilmiş modeller %s konumundan yüklendi", path)
                return
            except Exception:  # noqa: BLE001 - eski veya bozuk önbellek yalnızca yeniden eğitim demektir
                log.warning("%s yüklenemedi; yeniden eğitiliyor", path, exc_info=True)
        self.train(steps, seed)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(self.models, path)
            log.info("Eğitilmiş modeller %s konumuna kaydedildi", path)

    def train(self, steps: int = 7200, seed: int = 7) -> None:
        log.info("Sağlıklı referans veri üretiliyor (%d simüle saniye)...", steps)
        rows = generate_baseline(steps, seed)
        for asset_type, spec in SPECS.items():
            model = TypeModel(spec)
            model.fit(rows[asset_type], seed=seed)
            self.models[asset_type] = model
            log.info("%s modeli %d örnekle eğitildi, sigma=%s", asset_type, len(rows[asset_type]),
                     np.round(model.sigma, 3).tolist())
        self.ready = True

    def score(self, asset_type: str, rows: list[dict[str, float]]) -> list[Score]:
        return self.models[asset_type].score(rows)


@dataclass
class AssetHealth:
    """Bir varlık için yumuşatılmış sağlık; aralıklı kanıtların (ray kusuru yalnızca
    bir tren geçerken görünür) hemen unutulmaması için tepe değer tutma içerir."""

    health: float = 100.0
    hold: float = 0.0
    cause: str | None = None
    z: dict[str, float] = field(default_factory=dict)
    history: list[tuple[float, float]] = field(default_factory=list)

    HOLD_DECAY = 0.97  # saniye başına
    SMOOTHING = 0.15  # saniye başına
    HISTORY_S = 300.0

    def update(self, score: Score, now: float, dt: float) -> None:
        self.hold = max(score.anomaly, self.hold * self.HOLD_DECAY ** dt)
        target = 100.0 * (1.0 - self.hold)
        self.health += (target - self.health) * min(1.0, self.SMOOTHING * dt)
        self.z = score.z
        if score.anomaly > 0.05 and score.cause:
            self.cause = score.cause
        elif self.hold < 0.05:
            self.cause = None
        self.history.append((now, self.health))
        cutoff = now - self.HISTORY_S
        while self.history and self.history[0][0] < cutoff:
            self.history.pop(0)

    @property
    def status(self) -> str:
        if self.health >= 75:
            return "healthy"
        if self.health >= 45:
            return "degraded"
        return "critical"

    def trend_per_min(self) -> float:
        """Yakın geçmişe göre gerçek dakika başına sağlık değişimi."""
        if len(self.history) < 20:
            return 0.0
        t = np.array([h[0] for h in self.history])
        v = np.array([h[1] for h in self.history])
        slope = np.polyfit(t - t[0], v, 1)[0]
        return float(slope * 60.0)

    def seconds_to_critical(self, threshold: float = 45.0) -> float | None:
        """Mevcut eğilimle sağlığın `threshold` değerini geçmesine kalan gerçek saniye."""
        per_s = self.trend_per_min() / 60.0
        if self.health <= threshold:
            return 0.0
        if per_s > -0.01:
            return None
        return (self.health - threshold) / -per_s
