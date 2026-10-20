import pytest

from twin_core import FAULT_KINDS, TwinEngine


def test_trains_run_the_whole_line_and_turn_back():
    engine = TwinEngine(seed=3, time_scale=10.0)
    seen_dirs = {t.id: set() for t in engine.trains}
    max_speed = 0.0
    for _ in range(600):
        for r in engine.step(1.0):
            if r["type"] == "train":
                seen_dirs[r["asset_id"]].add(r["extra"]["direction"])
                max_speed = max(max_speed, r["metrics"]["speed_kmh"])
                assert 0 <= r["extra"]["position_m"] <= engine.topo.length_m
    assert all(d == {1, -1} for d in seen_dirs.values())
    assert 79.0 <= max_speed <= 80.01


def test_fault_ramps_up_and_repair_clears_it():
    engine = TwinEngine(seed=1, time_scale=5.0)
    kind = engine.inject_fault("T-101", "brake_leak", initial=0.3, ramp_real_s=60)
    assert kind == "brake_leak"
    for _ in range(30):
        engine.step(1.0)
    assert 0.6 < engine.active_faults()["T-101"]["brake_leak"] < 0.7
    assert engine.repair("T-101") == ["brake_leak"]
    assert engine.active_faults() == {}


def test_invalid_fault_kind_is_rejected():
    engine = TwinEngine()
    with pytest.raises(ValueError):
        engine.inject_fault("SW-01", "wheel_flat")


def test_random_fault_kind_matches_asset_type():
    engine = TwinEngine(seed=2)
    assert engine.inject_fault("SEG-01") in FAULT_KINDS["segment"]


def test_spontaneous_faults_wait_for_warmup():
    engine = TwinEngine(seed=4, time_scale=5.0, spontaneous_faults=True, fault_interval_s=5, warmup_s=60)
    for _ in range(59):
        engine.step(1.0)
    assert engine.active_faults() == {}
    for _ in range(120):
        engine.step(1.0)
    assert 1 <= len(engine.active_faults()) <= 2
