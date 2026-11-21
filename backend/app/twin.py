"""Canlı ikiz: her varlığın son durumu, sağlık puanları, alarmlar ve KPI'lar."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from .db import Database, utcnow
from .ml import EWMA_TAU_S, SPECS, WARMUP_TAUS, AssetHealth, FeatureBuilder, Score, TwinModels

log = logging.getLogger(__name__)

TRAIN_CAPACITY = 1200


@dataclass(frozen=True)
class Threshold:
    label: str
    unit: str
    direction: int
    warning: float
    critical: float


THRESHOLDS: dict[tuple[str, str], Threshold] = {
    ("train", "bearing_temp_c"): Threshold("Aks rulmanı sıcaklığı", "°C", +1, 85.0, 100.0),
    ("train", "bogie_vibration_mm_s"): Threshold("Boji titreşimi", "mm/s", +1, 5.0, 7.0),
    ("train", "brake_pressure_bar"): Threshold("Fren borusu basıncı", "bar", -1, 4.3, 3.8),
    ("segment", "vibration_rms"): Threshold("Ray titreşimi", "g", +1, 5.0, 6.5),
    ("segment", "geometry_dev_mm"): Threshold("Hat geometrisi sapması", "mm", +1, 4.0, 6.0),
    ("switch", "throw_time_s"): Threshold("Makas atma süresi", "s", +1, 4.5, 6.0),
}

METRIC_LABELS = {
    "bearing_temp_c": "aks rulmanı sıcaklığı",
    "bogie_vibration_mm_s": "boji titreşimi",
    "brake_pressure_bar": "fren borusu basıncı",
    "motor_current_a": "cer motoru akımı",
    "vibration_rms": "ray titreşimi",
    "geometry_dev_mm": "hat geometrisi",
    "throw_time_s": "makas atma süresi",
    "motor_current_peak_a": "makas motoru akımı",
}

ACTIONS = {
    "bearing_temp_c": "Aks rulmanını kontrol et ve değiştir",
    "bogie_vibration_mm_s": "Tekerlek profili kontrolü ve torna",
    "brake_pressure_bar": "Fren sistemi kaçak kontrolü",
    "motor_current_a": "Cer motoru ve invertör kontrolü",
    "vibration_rms": "Ultrasonik ray muayenesi ve taşlama",
    "geometry_dev_mm": "Hat buraj ve hizalama",
    "throw_time_s": "Makas yağlama ve ayarı",
    "motor_current_peak_a": "Makas motoru ve tahrik çubuğu kontrolü",
}
DEFAULT_ACTION = {"train": "Genel depo muayenesi", "segment": "Yürüyerek hat kontrolü",
                  "switch": "Makas kontrolü"}

ML_WARNING = 65.0
ML_CRITICAL = 45.0
ALERT_CLEAR_S = 20.0
SCORE_EVERY_S = 5.0  # sağlık ve sapma geçmişinin veritabanına yazılma aralığı
SEVERITY_RANK = {None: 0, "warning": 1, "critical": 2}


class LiveTwin:
    def __init__(self, db: Database, models: TwinModels):
        self.db = db
        self.models = models
        self.topology: dict | None = None
        self.time_scale = 1.0
        self.names: dict[str, str] = {}
        self.types: dict[str, str] = {}
        self.readings: dict[str, dict] = {}
        self.features: dict[str, dict] = {}
        self.last_seen: dict[str, float] = {}
        self.health: dict[str, AssetHealth] = defaultdict(AssetHealth)
        self.builder = FeatureBuilder()
        self.active_alerts: dict[tuple[str, str], dict] = {}
        self._clear_since: dict[tuple[str, str], float] = {}
        self._last_eval = time.monotonic()
        self._last_score_write = 0.0
        self.events: list[dict] = []
        self.mqtt_connected = False
        self.environment = {"ambient_c": 18.0, "load_factor": 0.6}
        self._bootstrapped = False
        self.trials: dict[str, dict] = {}  # varlık → açık arıza denemesi

    # ------------------------------------------------------------ veri alımı
    async def on_topology(self, topo: dict) -> None:
        self.topology = topo
        self.time_scale = float(topo.get("time_scale", 1.0))
        self.builder.time_scale = self.time_scale
        assets = []
        for kind, key in (("train", "trains"), ("segment", "segments"), ("switch", "switches")):
            for item in topo[key]:
                self.names[item["id"]] = item["name"]
                self.types[item["id"]] = kind
                assets.append((item["id"], kind, item["name"], item))
        await self.db.upsert_assets(assets)
        log.info("Topoloji alındı: %d varlık, zaman ölçeği x%s", len(assets), self.time_scale)
        if not self._bootstrapped:
            self._bootstrapped = True
            await self._bootstrap_running_averages()

    async def _bootstrap_running_averages(self) -> None:
        """Yeniden başlatmanın hemen ardından hareketli ortalamaların güvenilir olması için son telemetriyi yeniden oynatır."""
        by_type: dict[str, list[str]] = defaultdict(list)
        for asset_id, kind in self.types.items():
            if SPECS[kind].ewma:
                by_type[kind].append(asset_id)
        minutes = int(WARMUP_TAUS * EWMA_TAU_S / self.time_scale / 60) + 2
        for kind, ids in by_type.items():
            names = list(SPECS[kind].ewma)
            rows = await self.db.recent(ids, names, minutes)
            samples: dict[tuple, dict[str, float]] = defaultdict(dict)
            for r in rows:
                samples[(r["time"], r["asset_id"])][r["metric"]] = r["value"]
            last: dict[str, float] = {}
            for (ts, asset_id), metrics in samples.items():
                if len(metrics) < len(names):
                    continue
                t = ts.timestamp()
                dt = min(10.0, t - last.get(asset_id, t - 1.0))
                last[asset_id] = t
                self.builder.update({"asset_id": asset_id, "type": kind, "metrics": metrics}, dt)
            warm = sum(self.builder.is_warm(i, kind) for i in ids)
            log.info("Kayıtlı telemetriden %d/%d %s varlığı için hareketli ortalamalar yeniden oluşturuldu", warm, len(ids), kind)

    def on_reading(self, reading: dict) -> None:
        asset_id = reading["asset_id"]
        now = time.monotonic()
        dt = now - self.last_seen.get(asset_id, now - 1.0)
        self.last_seen[asset_id] = now
        self.readings[asset_id] = reading
        self.features[asset_id] = self.builder.update(reading, min(dt, 10.0))
        self.types.setdefault(asset_id, reading["type"])
        if "ambient_c" in reading["metrics"]:
            self.environment["ambient_c"] = reading["metrics"]["ambient_c"]

    # ------------------------------------------------------------ değerlendirme
    async def evaluate(self) -> None:
        now = time.monotonic()
        dt = max(0.1, now - self._last_eval)
        self._last_eval = now
        if not self.models.ready:
            return
        by_type: dict[str, list[str]] = defaultdict(list)
        for asset_id in self.features:
            if self.builder.is_warm(asset_id, self.types[asset_id]):
                by_type[self.types[asset_id]].append(asset_id)
        for asset_type, ids in by_type.items():
            scores: list[Score] = self.models.score(asset_type, [self.features[i] for i in ids])
            for asset_id, score in zip(ids, scores):
                self.health[asset_id].update(score, now, dt)
        if now - self._last_score_write >= SCORE_EVERY_S:
            self._last_score_write = now
            ts = utcnow()
            for ids in by_type.values():
                for asset_id in ids:
                    h = self.health[asset_id]
                    self.db.buffer_scores(ts, asset_id, {"health": h.health, **h.z})
        await self._evaluate_alerts(now)

    async def _evaluate_alerts(self, now: float) -> None:
        wanted: dict[tuple[str, str], tuple[str, str, str]] = {}
        for asset_id, reading in self.readings.items():
            asset_type = reading["type"]
            name = self.names.get(asset_id, asset_id)
            for metric, value in reading["metrics"].items():
                th = THRESHOLDS.get((asset_type, metric))
                if th is None:
                    continue
                severity = _threshold_severity(th, value)
                if severity:
                    limit = th.critical if severity == "critical" else th.warning
                    message = f"{name}: {th.label} {value:.1f} {th.unit} (sınır {limit:g} {th.unit})"
                    wanted[(asset_id, metric)] = (severity, "rule", message)

            health = self.health.get(asset_id)
            if health and health.health < ML_WARNING:
                severity = "critical" if health.health < ML_CRITICAL else "warning"
                cause = METRIC_LABELS.get(health.cause or "", "birden fazla sensör")
                message = f"{name}: anomali tespit edildi, sağlık %{health.health:.0f} – olası sebep: {cause}"
                wanted[(asset_id, "ml")] = (severity, "ml", message)

        for key, (severity, source, message) in wanted.items():
            self._clear_since.pop(key, None)
            alert = self.active_alerts.get(key)
            if alert is None:
                alert = await self.db.open_alert(key[0], key[1], severity, source, message)
                self.active_alerts[key] = alert
                self.events.append({"type": "alert_opened", "alert": alert})
                if source == "ml":
                    await self._trial_detected(key[0], now)
            elif SEVERITY_RANK[severity] > SEVERITY_RANK[alert["severity"]]:
                alert.update(severity=severity, message=message)
                await self.db.update_alert(alert["id"], severity, message)
                self.events.append({"type": "alert_escalated", "alert": alert})

        for key in list(self.active_alerts):
            if key in wanted:
                continue
            since = self._clear_since.setdefault(key, now)
            if now - since >= ALERT_CLEAR_S:
                alert = self.active_alerts.pop(key)
                self._clear_since.pop(key, None)
                await self.db.resolve_alert(alert["id"])
                self.events.append({"type": "alert_resolved", "alert": alert})

    # ------------------------------------------------------- arıza denemeleri
    async def start_trial(self, asset_id: str, kind: str, expected_cause: str | None) -> None:
        await self.close_trial(asset_id)
        row = await self.db.create_trial(asset_id, kind, expected_cause)
        self.trials[asset_id] = {"id": row["id"], "t0": time.monotonic(), "detected": False}

    async def _trial_detected(self, asset_id: str, now: float) -> None:
        trial = self.trials.get(asset_id)
        if trial is None or trial["detected"]:
            return
        trial["detected"] = True
        health = self.health.get(asset_id)
        await self.db.mark_trial_detected(trial["id"], round(now - trial["t0"], 1), health.cause if health else None)

    async def close_trial(self, asset_id: str) -> None:
        trial = self.trials.pop(asset_id, None)
        if trial is not None:
            await self.db.close_trial(trial["id"])

    def acknowledge(self, alert_id: int) -> None:
        for alert in self.active_alerts.values():
            if alert["id"] == alert_id:
                alert["acknowledged"] = True

    def drain_events(self) -> list[dict]:
        events, self.events = self.events, []
        return events

    # ------------------------------------------------------------ görünümler
    def asset_view(self, asset_id: str) -> dict:
        reading = self.readings.get(asset_id, {})
        asset_type = self.types.get(asset_id, reading.get("type", ""))
        health = self.health.get(asset_id) or AssetHealth()
        # Bunun üzerinde puandaki küçük dalgalanmalar gürültüdür: sebep yok, geri sayım yok.
        concerning = health.health < 90.0
        ttc = health.seconds_to_critical() if concerning else None
        cause = health.cause if concerning else None
        return {
            "id": asset_id,
            "type": asset_type,
            "name": self.names.get(asset_id, asset_id),
            "health": round(health.health, 1),
            "status": health.status,
            "cause": cause,
            "cause_label": METRIC_LABELS.get(cause or ""),
            "recommended_action": ACTIONS.get(cause or "", DEFAULT_ACTION.get(asset_type, "Kontrol")),
            "trend_per_min": round(health.trend_per_min(), 2),
            # Simülatör gerçek zamandan hızlı çalışır; tahmini işletme saati cinsinden ifade et.
            "time_to_critical_h": None if ttc is None else round(ttc * self.time_scale / 3600.0, 2),
            "z": health.z,
            "metrics": reading.get("metrics", {}),
            "extra": reading.get("extra", {}),
            "online": time.monotonic() - self.last_seen.get(asset_id, 0) < 10,
            "warming": bool(asset_type) and not self.builder.is_warm(asset_id, asset_type),
        }

    def kpis(self) -> dict:
        trains = [r for r in self.readings.values() if r["type"] == "train"]
        healths = {i: h.health for i, h in self.health.items()}
        fleet = [healths[t["asset_id"]] for t in trains if t["asset_id"] in healths]
        alerts = list(self.active_alerts.values())
        return {
            "trains_total": len(trains),
            "trains_running": sum(1 for t in trains if t["extra"].get("status") == "running"),
            "fleet_health": round(sum(fleet) / len(fleet), 1) if fleet else None,
            "network_health": round(sum(healths.values()) / len(healths), 1) if healths else None,
            "traction_power_mw": round(
                sum(max(0.0, t["metrics"]["traction_power_kw"]) for t in trains) / 1000.0, 2
            ),
            "passengers_on_board": round(
                sum(t["metrics"]["passenger_load_pct"] / 100.0 * TRAIN_CAPACITY for t in trains)
            ),
            "alerts_critical": sum(1 for a in alerts if a["severity"] == "critical"),
            "alerts_warning": sum(1 for a in alerts if a["severity"] == "warning"),
            "assets_degraded": sum(1 for h in self.health.values() if h.status != "healthy"),
            "ambient_c": self.environment["ambient_c"],
            "load_factor": self.environment["load_factor"],
        }

    def snapshot(self) -> dict:
        return {
            "ts": time.time(),
            "model_ready": self.models.ready,
            "mqtt_connected": self.mqtt_connected,
            "time_scale": self.time_scale,
            "kpis": self.kpis(),
            "assets": {asset_id: self.asset_view(asset_id) for asset_id in self.readings},
            "alerts": sorted(self.active_alerts.values(), key=lambda a: a["created_at"], reverse=True),
        }


def _threshold_severity(th: Threshold, value: float) -> str | None:
    if th.direction > 0:
        if value >= th.critical:
            return "critical"
        if value >= th.warning:
            return "warning"
    else:
        if value <= th.critical:
            return "critical"
        if value <= th.warning:
            return "warning"
    return None
