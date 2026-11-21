"""Canlı arıza denemelerinin özeti."""

from app.evaluation import FAULT_SIGNATURE, FAULT_TYPE, live_summary
from twin_core import FAULT_KINDS


def trial(detected_cause=None, expected="bearing_temp_c", detect_s=None, repaired=True):
    return {"expected_cause": expected, "detected_at": "x" if detect_s is not None else None,
            "detect_s": detect_s, "detected_cause": detected_cause, "repaired_at": "y" if repaired else None}


def test_live_summary_counts_only_finished_trials():
    s = live_summary([
        trial("bearing_temp_c", detect_s=20.0),
        trial("bogie_vibration_mm_s", detect_s=30.0),
        trial(repaired=True),  # tespit edilmeden onarıldı → kaçırıldı
        trial(repaired=False),  # hâlâ sürüyor → sayılmaz
    ])
    assert s == {"trials": 4, "detected": 2, "missed": 1, "mean_detect_s": 25.0, "cause_accuracy": 0.5}


def test_live_summary_handles_no_trials():
    assert live_summary([])["mean_detect_s"] is None


def test_every_fault_kind_has_an_expected_sensor():
    kinds = {k for ks in FAULT_KINDS.values() for k in ks}
    assert set(FAULT_SIGNATURE) == kinds == set(FAULT_TYPE)
