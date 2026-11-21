"""“Sağlık neden düştü?” açıklamasının kenar durumları, sentetik geçmişle."""

from datetime import datetime, timedelta, timezone

from app.explain import bad_direction, explain

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def series(values, step_s=5):
    return [(T0 + timedelta(seconds=i * step_s), float(v)) for i, v in enumerate(values)]


def fault_story():
    """60 sakin nokta, sonra rulman sapması büyür, sağlık geriden gelir, onarım, toparlanma."""
    calm, rise, fall, back = [98] * 60, [97, 96, 94, 90, 85], [78, 70, 60, 50, 40, 30, 25], [40, 60, 76, 88, 95]
    health = series(calm + rise + fall + back)
    bearing = series([0.5] * 58 + [1.5, 2.5, 3.5, 5, 7, 9, 11, 13, 15, 16, 17, 17.5, 18, 18, 3, 1, 0.5, 0.5, 0.5])
    brake = series([0.3] * len(health))
    return health, {"bearing_temp_c": bearing, "brake_pressure_bar": brake}


def test_no_drop_means_no_explanation():
    health = series([97, 98, 96, 99, 97])
    assert explain(health, {"bearing_temp_c": series([0.2] * 5)}) is None
    assert explain([], {}) is None


def test_fault_story_is_explained():
    health, z = fault_story()
    e = explain(health, z)
    assert e["cause"] == "bearing_temp_c"
    assert e["onset"]["metric"] == "bearing_temp_c"
    assert e["onset"]["t"] == health[60][0].isoformat(), "sapma 3σ'yı ilk aştığı noktada başlamalı"
    assert e["others_normal"] is True
    assert e["degraded_at"] is not None and e["critical_at"] is not None
    assert e["lead_s"] > 0, "sapma, sağlık eşiğinin altına inmeden önce görülmeli"
    assert e["recovered_at"] is not None and e["ongoing"] is False
    assert e["min_health"] == 25.0


def test_ongoing_drop_reports_no_recovery():
    health, z = fault_story()
    cut = 70
    e = explain(health[:cut], {m: s[:cut] for m, s in z.items()})
    assert e["ongoing"] is True and e["recovered_at"] is None


def test_smaller_deviation_after_the_main_one_is_secondary():
    health, z = fault_story()
    z["brake_pressure_bar"] = series([0.3] * 62 + [4.0] * (len(health) - 62))
    e = explain(health, z)
    assert e["others_normal"] is False
    assert e["secondary"] == ["brake_pressure_bar"] and e["independent"] == []


def test_deviation_starting_before_the_main_one_is_independent():
    health, z = fault_story()
    z["brake_pressure_bar"] = series([0.3] * 55 + [4.0] * (len(health) - 55))
    e = explain(health, z)
    assert e["independent"] == ["brake_pressure_bar"] and e["secondary"] == []


def test_spike_after_recovery_does_not_belong_to_the_drop():
    health, z = fault_story()
    calm_after = series([98] * (len(health) + 10))[len(health):]  # toparlanmadan sonra 10 sakin nokta
    health += calm_after
    z["bearing_temp_c"] += [(t, 0.5) for t, _ in calm_after]
    z["brake_pressure_bar"] = [(t, 3.5 if i == len(health) - 3 else 0.3) for i, (t, _) in enumerate(health)]
    e = explain(health, z)
    assert e["others_normal"] is True, "toparlanmadan sonraki tek sıçrama düşüşe atfedildi"


def test_bad_direction_flips_low_is_worse_sensors():
    raw = {"brake_pressure_bar": series([-4.0]), "bearing_temp_c": series([2.0]), "speed_kmh": series([9.0])}
    bad = bad_direction("train", raw)
    assert bad["brake_pressure_bar"][0][1] == 4.0, "düşük fren basıncı kötü yönde pozitif olmalı"
    assert bad["bearing_temp_c"][0][1] == 2.0
    assert "speed_kmh" not in bad, "izlenmeyen metrikler açıklamaya girmez"
