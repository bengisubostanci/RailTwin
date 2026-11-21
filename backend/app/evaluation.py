"""Model performansı: fizik motorunda tekrarlanabilir bir kıyaslama.

Her arıza türü, farklı tohum değerleriyle simüle edilen hatlarda ayrı bir varlığa
enjekte edilir ve canlı ikizle aynı puanlama zinciri çalıştırılır. Ölçülenler:
tespit oranı, alarma kadar geçen süre ve sebebin doğru sensöre atfedilme oranı.
Ayrıca arızasız hatlarda, sert hava ve talep değişimleri altında yanlış alarm
sıklığı sayılır. Simülatörün gerçek arıza bilgisi yalnızca burada, puanlamadan
sonra karşılaştırma için kullanılır.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from twin_core import FAULT_KINDS, TwinEngine

from .ml import MODEL_VERSION, AssetHealth, FeatureBuilder, TwinModels

log = logging.getLogger(__name__)

ALARM_BELOW = 65.0  # canlı ikizin ML uyarı eşiğiyle aynı
WARMUP_TICKS = 300  # hareketli ortalamaların ısınması için gereken ~240 adımın üzerinde
FAULT_TICKS = 240  # enjeksiyondan sonra en fazla bu kadar gerçek saniye beklenir
HEALTHY_PHASES = ((26.0, 0.8), (42.0, 1.15), (-12.0, 0.2), (18.0, 0.6))
HEALTHY_PHASE_TICKS = 200
DEFAULT_SEEDS = (101, 202, 303)

# Her arıza türünün hangi sensörde görünmesi beklenir (sebep doğruluğu için).
FAULT_SIGNATURE = {
    "bearing_overheat": "bearing_temp_c",
    "wheel_flat": "bogie_vibration_mm_s",
    "brake_leak": "brake_pressure_bar",
    "motor_degradation": "motor_current_a",
    "rail_defect": "vibration_rms",
    "geometry_drift": "geometry_dev_mm",
    "switch_wear": "throw_time_s",
}
FAULT_TYPE = {kind: asset_type for asset_type, kinds in FAULT_KINDS.items() for kind in kinds}


class _Line:
    """Bir simüle hat ve üzerinde canlı ikizdeki gibi çalışan puanlama."""

    def __init__(self, models: TwinModels, seed: int, time_scale: float):
        self.models = models
        self.engine = TwinEngine(seed=seed, time_scale=time_scale)
        self.builder = FeatureBuilder(time_scale)
        self.health: dict[str, AssetHealth] = {}
        self.tick = 0

    def step(self) -> None:
        by_type: dict[str, list[tuple[str, dict]]] = {}
        for r in self.engine.step(1.0):
            features = self.builder.update(r, 1.0)
            if self.builder.is_warm(r["asset_id"], r["type"]):
                by_type.setdefault(r["type"], []).append((r["asset_id"], features))
        for asset_type, items in by_type.items():
            for (asset_id, _), score in zip(items, self.models.score(asset_type, [f for _, f in items])):
                self.health.setdefault(asset_id, AssetHealth()).update(score, float(self.tick), 1.0)
        self.tick += 1

    def alarming(self) -> set[str]:
        return {a for a, h in self.health.items() if h.health < ALARM_BELOW}


def _fault_targets(engine: TwinEngine) -> dict[str, str]:
    """Her arıza türü için ayrı bir varlık seçer (varlık → arıza türü)."""
    by_type: dict[str, list[str]] = {}
    for asset_id, asset in sorted(engine.assets.items()):
        by_type.setdefault(asset.type, []).append(asset_id)
    targets = {}
    for kind, asset_type in FAULT_TYPE.items():
        pool = by_type[asset_type]
        targets[pool.pop(len(pool) // 2)] = kind
    return targets


def run_benchmark(models: TwinModels, time_scale: float = 5.0, seeds: tuple[int, ...] = DEFAULT_SEEDS,
                  progress: Callable[[float], None] | None = None) -> dict:
    started = time.monotonic()
    total = len(seeds) * (WARMUP_TICKS + FAULT_TICKS + WARMUP_TICKS + len(HEALTHY_PHASES) * HEALTHY_PHASE_TICKS)
    done = 0

    def advance(line: _Line, ticks: int) -> None:
        nonlocal done
        for _ in range(ticks):
            line.step()
            done += 1
            if progress and done % 50 == 0:
                progress(done / total)

    trials: dict[str, list[dict]] = {kind: [] for kind in FAULT_SIGNATURE}
    false_alarms = 0
    asset_ticks = 0

    for seed in seeds:
        # 1) Arızalı hat: her tür ayrı bir varlıkta, aynı anda.
        line = _Line(models, seed, time_scale)
        advance(line, WARMUP_TICKS)
        targets = _fault_targets(line.engine)
        for asset_id, kind in targets.items():
            line.engine.inject_fault(asset_id, kind)
        detected: dict[str, tuple[int, str | None]] = {}
        previously: set[str] = set()
        for t in range(FAULT_TICKS):
            advance(line, 1)
            alarming = line.alarming()
            for asset_id in alarming & targets.keys() - detected.keys():
                detected[asset_id] = (t + 1, line.health[asset_id].cause)
            # Arıza enjekte edilmemiş varlıklardaki alarmlar yanlış alarmdır.
            bystanders = alarming - targets.keys()
            false_alarms += len(bystanders - previously)
            previously = bystanders
            asset_ticks += len(line.health) - len(targets)
        for asset_id, kind in targets.items():
            hit = detected.get(asset_id)
            trials[kind].append({
                "seed": seed, "asset_id": asset_id, "detected": hit is not None,
                "detect_s": hit[0] if hit else None, "cause": hit[1] if hit else None,
                "cause_correct": bool(hit and hit[1] == FAULT_SIGNATURE[kind]),
            })

        # 2) Arızasız hat: sert hava ve talep değişimleri; her alarm bir yanlış alarmdır.
        line = _Line(models, seed + 1, time_scale)
        advance(line, WARMUP_TICKS)
        previously: set[str] = set()
        for ambient, load in HEALTHY_PHASES:
            line.engine.set_env(ambient_c=ambient, load_factor=load)
            for _ in range(HEALTHY_PHASE_TICKS):
                advance(line, 1)
                now = line.alarming()
                false_alarms += len(now - previously)  # alarma yeni giren her varlık bir olay
                previously = now
                asset_ticks += len(line.health)

    faults = []
    for kind, rows in trials.items():
        hits = [r for r in rows if r["detected"]]
        times = [r["detect_s"] for r in hits]
        faults.append({
            "kind": kind,
            "asset_type": FAULT_TYPE[kind],
            "expected_cause": FAULT_SIGNATURE[kind],
            "trials": len(rows),
            "detected": len(hits),
            "detection_rate": round(len(hits) / len(rows), 3),
            "cause_correct": sum(r["cause_correct"] for r in rows),
            "cause_accuracy": round(sum(r["cause_correct"] for r in hits) / len(hits), 3) if hits else None,
            "mean_detect_s": round(sum(times) / len(times), 1) if times else None,
            "max_detect_s": max(times) if times else None,
            "runs": rows,
        })

    all_rows = [r for rows in trials.values() for r in rows]
    hits = [r for r in all_rows if r["detected"]]
    asset_hours = asset_ticks * time_scale / 3600.0
    if progress:
        progress(1.0)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "seeds": list(seeds),
        "time_scale": time_scale,
        "duration_s": round(time.monotonic() - started, 1),
        "overall": {
            "trials": len(all_rows),
            "detection_rate": round(len(hits) / len(all_rows), 3),
            "cause_accuracy": round(sum(r["cause_correct"] for r in hits) / len(hits), 3) if hits else None,
            "mean_detect_s": round(sum(r["detect_s"] for r in hits) / len(hits), 1) if hits else None,
        },
        "faults": faults,
        "false_alarms": {
            "events": false_alarms,
            "asset_hours": round(asset_hours, 1),
            "per_1000_asset_hours": round(false_alarms / asset_hours * 1000, 2) if asset_hours else None,
            "conditions": [{"ambient_c": a, "load_factor": l} for a, l in HEALTHY_PHASES],
        },
    }


class BenchmarkManager:
    """Kıyaslamayı arka planda çalıştırır; sonucu, aynı model sürümü için diskte saklar."""

    def __init__(self, models: TwinModels, cache_path: Path | None):
        self.models = models
        self.cache_path = cache_path
        self.status = "idle"  # idle | running | ready | failed
        self.progress = 0.0
        self.result: dict | None = None
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        if cache_path and cache_path.exists():
            try:
                self.result = json.loads(cache_path.read_text(encoding="utf-8"))
                self.status = "ready"
            except Exception:  # noqa: BLE001 - bozuk önbellek yalnızca yeniden çalıştırma demektir
                log.warning("%s okunamadı", cache_path, exc_info=True)

    def view(self) -> dict:
        return {"status": self.status, "progress": round(self.progress, 3), "error": self.error,
                "result": self.result}

    def start(self, time_scale: float) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError("Değerlendirme zaten çalışıyor.")
        if not self.models.ready:
            raise RuntimeError("Modeller henüz hazır değil.")
        self.status, self.progress, self.error = "running", 0.0, None
        self._task = asyncio.create_task(self._run(time_scale))

    def _set_progress(self, value: float) -> None:
        self.progress = value  # iş parçacığından yazılır; tek bir float ataması güvenlidir

    async def _run(self, time_scale: float) -> None:
        try:
            result = await asyncio.to_thread(run_benchmark, self.models, time_scale, DEFAULT_SEEDS, self._set_progress)
        except Exception as exc:  # noqa: BLE001 - hata panelde gösterilir
            log.exception("Model değerlendirmesi başarısız oldu")
            self.status, self.error = "failed", str(exc)
            return
        self.result, self.status = result, "ready"
        log.info("Model değerlendirmesi %s sn'de tamamlandı", result["duration_s"])
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()  # iş parçacığı kendi bitişine kadar çalışır; sonucu yok sayılır


def live_summary(trials: list[dict]) -> dict:
    """Canlı arıza denemelerinin özeti (yalnızca sonuçlananlar)."""
    detected = [t for t in trials if t["detected_at"]]
    known = [t for t in detected if t["expected_cause"]]
    missed = [t for t in trials if not t["detected_at"] and t["repaired_at"]]
    return {
        "trials": len(trials),
        "detected": len(detected),
        "missed": len(missed),
        "mean_detect_s": round(sum(t["detect_s"] for t in detected) / len(detected), 1) if detected else None,
        "cause_accuracy": (round(sum(t["detected_cause"] == t["expected_cause"] for t in known) / len(known), 3)
                           if known else None),
    }
