from dataclasses import replace

import pytest

from app.scenario import BASELINE, run_scenario


def test_baseline_scenario_matches_itself():
    result = run_scenario(BASELINE)
    assert result["baseline"] == result["scenario"]
    assert len(result["daily"]) == BASELINE.horizon_days
    assert result["warnings"] == []


def test_shorter_headway_needs_more_trains_and_energy():
    result = run_scenario(replace(BASELINE, headway_min=3.0))
    base, scen = result["baseline"], result["scenario"]
    assert scen["trains_required"] > base["trains_required"]
    assert scen["energy_mwh_per_day"] > base["energy_mwh_per_day"]


def test_fleet_shortage_stretches_headway_and_warns():
    result = run_scenario(replace(BASELINE, fleet_size=6))
    assert result["scenario"]["effective_headway_min"] > BASELINE.headway_min
    assert any("tren gerekiyor" in w for w in result["warnings"])


def test_predictive_maintenance_cuts_failures():
    result = run_scenario(replace(BASELINE, maintenance_strategy="predictive"))
    assert result["scenario"]["failures_mean"] < result["baseline"]["failures_mean"] * 0.5
    assert result["scenario"]["otp_pct"] >= result["baseline"]["otp_pct"]


def test_heat_increases_failures():
    result = run_scenario(replace(BASELINE, ambient_c=38.0))
    assert result["scenario"]["failures_mean"] > result["baseline"]["failures_mean"]


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        run_scenario(replace(BASELINE, maintenance_strategy="hope"))
