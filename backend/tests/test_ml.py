"""Dedektörün uçtan uca kontrolü: sağlıklı bir hat sağlıklı kalır, her arıza
türü yakalanır ve doğru sensöre atfedilir."""

from datetime import datetime, timedelta, timezone

import pytest

from app.explain import bad_direction, explain
from app.ml import AssetHealth, FeatureBuilder, TwinModels
from twin_core import TwinEngine

TIME_SCALE = 5.0

EXPECTED_CAUSE = {
    ("T-103", "bearing_overheat"): "bearing_temp_c",
    ("T-104", "wheel_flat"): "bogie_vibration_mm_s",
    ("T-105", "brake_leak"): "brake_pressure_bar",
    ("T-106", "motor_degradation"): "motor_current_a",
    ("SEG-04", "rail_defect"): "vibration_rms",
    ("SEG-07", "geometry_drift"): "geometry_dev_mm",
    ("SW-02", "switch_wear"): "throw_time_s",
}


@pytest.fixture(scope="module")
def models():
    m = TwinModels()
    m.train(steps=36000, seed=11)
    return m


def run_live(models, engine, ticks, builder, health, only_warm=False):
    for t in range(ticks):
        readings = engine.step(1.0)
        by_type = {}
        for r in readings:
            features = builder.update(r, 1.0)
            if only_warm and not builder.is_warm(r["asset_id"], r["type"]):
                continue
            by_type.setdefault(r["type"], []).append((r["asset_id"], features))
        for asset_type, items in by_type.items():
            scores = models.score(asset_type, [f for _, f in items])
            for (asset_id, _), score in zip(items, scores):
                health.setdefault(asset_id, AssetHealth()).update(score, float(t), 1.0)


def test_healthy_line_raises_no_false_alarms(models):
    engine = TwinEngine(seed=99, time_scale=TIME_SCALE)
    engine.set_env(ambient_c=26.0, load_factor=0.8)
    builder, health = FeatureBuilder(TIME_SCALE), {}
    run_live(models, engine, 400, builder, health)
    worst = min(health.items(), key=lambda kv: kv[1].health)
    assert worst[1].health > 75, f"{worst[0]} hiçbir arıza olmadan {worst[1].health:.1f} değerine düştü"


def test_backend_joining_a_running_line_raises_no_false_alarms(models):
    """Arka uç hattan çok sonra başlayabilir: hareketli ortalamaları akışın ortasında
    başlar, bu yüzden varlıklar ancak yeterli geçmiş toplandıktan sonra puanlanır."""
    engine = TwinEngine(seed=31, time_scale=TIME_SCALE)
    for _ in range(600):
        engine.step(1.0)
    builder, health = FeatureBuilder(TIME_SCALE), {}
    run_live(models, engine, 400, builder, health, only_warm=True)
    assert set(health) == set(engine.assets), "bazı varlıkların ısınması hiç tamamlanmadı"
    worst = min(health.items(), key=lambda kv: kv[1].health)
    assert worst[1].health > 75, f"{worst[0]} soğuk başlangıç sonrası {worst[1].health:.1f} değerine düştü"


def test_abrupt_weather_and_demand_changes_are_not_faults(models):
    engine = TwinEngine(seed=21, time_scale=TIME_SCALE)
    builder, health = FeatureBuilder(TIME_SCALE), {}
    run_live(models, engine, 200, builder, health)
    for ambient, load in ((42, 1.15), (-12, 0.2), (18, 0.6)):
        engine.set_env(ambient_c=ambient, load_factor=load)
        run_live(models, engine, 150, builder, health)
        worst = min(health.items(), key=lambda kv: kv[1].health)
        assert worst[1].health > 70, f"{worst[0]}, {ambient} °C ayarlandıktan sonra {worst[1].health:.1f} değerine düştü"


def test_every_fault_is_detected_with_the_right_cause(models):
    engine = TwinEngine(seed=5, time_scale=TIME_SCALE)
    builder, health = FeatureBuilder(TIME_SCALE), {}
    run_live(models, engine, 120, builder, health)
    for asset_id, kind in EXPECTED_CAUSE:
        engine.inject_fault(asset_id, kind)
    run_live(models, engine, 240, builder, health)

    for (asset_id, kind), cause in EXPECTED_CAUSE.items():
        h = health[asset_id]
        assert h.health < 65, f"{asset_id} üzerindeki {kind} tespit edilmedi (sağlık {h.health:.1f})"
        assert h.cause == cause, f"{asset_id} üzerindeki {kind}, {h.cause} sebebine atfedildi; beklenen {cause}"

    untouched = [a for a in health if a not in {k[0] for k in EXPECTED_CAUSE}]
    assert all(health[a].health > 70 for a in untouched)


def test_repair_restores_health(models):
    engine = TwinEngine(seed=8, time_scale=TIME_SCALE)
    builder, health = FeatureBuilder(TIME_SCALE), {}
    run_live(models, engine, 60, builder, health)
    engine.inject_fault("SW-01", "switch_wear")
    run_live(models, engine, 300, builder, health)
    assert health["SW-01"].health < 65
    engine.repair("SW-01")
    run_live(models, engine, 400, builder, health)
    assert health["SW-01"].health > 80


def test_explanation_names_the_failing_bearing_before_health_turns_red(models):
    """Gerçek motor ve modelle: kayıtlı geçmişten üretilen açıklama, arızalı sensörü
    ve sapmanın sağlık eşiğinden önce başladığını göstermeli."""
    engine = TwinEngine(seed=21, time_scale=TIME_SCALE)
    builder = FeatureBuilder(TIME_SCALE)
    health = AssetHealth()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h_series, z_series = [], {}
    repaired = False
    for t in range(1200):
        if t == 400:
            engine.inject_fault("T-103", "bearing_overheat")
        for r in engine.step(1.0):
            features = builder.update(r, 1.0)
            if r["asset_id"] == "T-103" and builder.is_warm("T-103", "train"):
                health.update(models.score("train", [features])[0], float(t), 1.0)
        if not repaired and health.health < 30:
            engine.repair("T-103")
            repaired = True
        if t % 5 == 0 and health.z:  # canlı ikiz gibi 5 saniyede bir kaydet
            ts = t0 + timedelta(seconds=t)
            h_series.append((ts, health.health))
            for metric, z in health.z.items():
                z_series.setdefault(metric, []).append((ts, z))

    assert repaired, "arıza kritik seviyeye ulaşmadı"
    e = explain(h_series, bad_direction("train", z_series))
    assert e is not None
    assert e["cause"] == "bearing_temp_c"
    assert e["onset"]["metric"] == "bearing_temp_c"
    assert e["onset"]["t"] >= (t0 + timedelta(seconds=400)).isoformat(), "sapma arızadan önce başlayamaz"
    assert e["lead_s"] is not None and e["lead_s"] > 0
    assert e["recovered_at"] is not None
