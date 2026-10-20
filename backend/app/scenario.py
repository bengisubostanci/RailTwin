"""Senaryo analizi (Ya olursa?): işletmedeki bir değişikliğin haftalar içinde nasıl sonuçlanacağını öngörür.

Bu, hattın indirgenmiş mertebeli bir modelidir (seyir süreleri, filo çevrimi,
enerji, aşınma ve Monte Carlo çekilişleriyle Weibull tarzı bileşen arızaları);
etkileşimli yanıt verecek kadar hızlıdır. Canlı ikizin simüle ettiği aynı hatta
kalibre edilmiştir, bu yüzden referans durumu panoda görünenle örtüşür.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

import numpy as np

from twin_core import default_topology

ACCEL = 1.0
DWELL_S = 30.0
TURNAROUND_S = 120.0
TRAIN_CAPACITY = 1200
ENERGY_PRICE_EUR_KWH = 0.14
GRID_CO2_KG_KWH = 0.38
FAILURE_COST_EUR = 35_000
FAILURE_DISRUPTION_OTP = 2.5  # seferde arıza başına kaybedilen dakiklik puanı
TRACK_WEAR_LIMIT = 100.0
TRACK_MAINTENANCE_EUR = 60_000
MONTE_CARLO_RUNS = 300

# Referans zorlanmada, seferde arızalar arası ortalama tren-km.
COMPONENTS = {
    "axle_bearing": 450_000,
    "brakes": 300_000,
    "traction_motor": 600_000,
    "wheelset": 200_000,
    "doors": 120_000,
}

STRATEGIES = {
    "reactive": {"failure_reduction": 0.0, "downtime": 0.02, "cost_per_train_day": 120.0},
    "preventive": {"failure_reduction": 0.5, "downtime": 0.08, "cost_per_train_day": 380.0},
    "predictive": {"failure_reduction": 0.8, "downtime": 0.03, "cost_per_train_day": 260.0},
}


@dataclass(frozen=True)
class ScenarioParams:
    headway_min: float = 6.0
    service_hours: float = 18.0
    load_factor: float = 0.6
    ambient_c: float = 18.0
    fleet_size: int = 12
    max_speed_kmh: float = 80.0
    maintenance_strategy: str = "reactive"
    horizon_days: int = 30


BASELINE = ScenarioParams()

PRESETS = {
    "rush_hour": {"label": "Yoğun saat artışı", "description": "Her 3 dakikada bir tren çalıştır",
                  "params": {"headway_min": 3.0, "load_factor": 0.85}},
    "heatwave": {"label": "Sıcak hava dalgası", "description": "Tüm ay boyunca 38 °C",
                 "params": {"ambient_c": 38.0}},
    "fleet_shortage": {"label": "Filo eksikliği", "description": "Dört tren revizyonda",
                       "params": {"fleet_size": 8}},
    "predictive": {"label": "Kestirimci bakım", "description": "İkizin sağlık puanlarını kullan",
                   "params": {"maintenance_strategy": "predictive"}},
    "faster": {"label": "Hat hızı artırımı", "description": "Hat hızını 100 km/h'ye çıkar",
               "params": {"max_speed_kmh": 100.0}},
}


def _interstation_time(distance_m: float, vmax: float) -> float:
    if distance_m >= vmax * vmax / ACCEL:
        return distance_m / vmax + vmax / ACCEL
    return 2.0 * math.sqrt(distance_m / ACCEL)


def _simulate(p: ScenarioParams, rng: np.random.Generator) -> dict:
    topo = default_topology()
    stops = [s.position_m for s in topo.stations]
    gaps = [b - a for a, b in zip(stops, stops[1:])]
    vmax = p.max_speed_kmh / 3.6
    line_km = topo.length_m / 1000.0
    strategy = STRATEGIES[p.maintenance_strategy]

    one_way_s = sum(_interstation_time(d, vmax) for d in gaps) + DWELL_S * (len(stops) - 2)
    cycle_s = 2 * one_way_s + 2 * TURNAROUND_S
    required = math.ceil(cycle_s / (p.headway_min * 60.0))
    available = max(1, math.floor(p.fleet_size * (1.0 - strategy["downtime"])))
    effective_headway = max(p.headway_min, cycle_s / available / 60.0)
    utilisation = required / available

    trips = 2 * p.service_hours * 60.0 / effective_headway
    train_km = trips * line_km
    capacity = trips * TRAIN_CAPACITY
    passengers = capacity * min(p.load_factor, 1.0)
    unserved = capacity * max(0.0, p.load_factor - 1.0)

    load_mass = 1.0 + 0.33 * (p.load_factor - 0.6)
    speed_factor = p.max_speed_kmh / 80.0
    kwh_per_km = 11.5 * load_mass * speed_factor ** 1.4 * (1.0 + 0.012 * abs(p.ambient_c - 20.0))
    energy_kwh = train_km * kwh_per_km

    stress = load_mass * math.exp(0.03 * (p.ambient_c - 20.0)) * speed_factor
    expected = {
        name: train_km * stress / mtbf * (1.0 - strategy["failure_reduction"])
        for name, mtbf in COMPONENTS.items()
    }
    lam = sum(expected.values())
    draws = rng.poisson(lam, size=(MONTE_CARLO_RUNS, p.horizon_days))

    otp_base = 99.0 - 25.0 * max(0.0, utilisation - 0.85)
    otp_base -= 0.4 * max(0.0, p.ambient_c - 32.0)
    otp_base -= 20.0 * max(0.0, p.load_factor - 0.9)
    otp = np.clip(otp_base - FAILURE_DISRUPTION_OTP * draws, 50.0, 99.5)

    wear_per_day = train_km * load_mass ** 3 * speed_factor ** 1.5 / 4000.0
    wear = np.cumsum(np.full(p.horizon_days, wear_per_day))
    track_day = int(np.argmax(wear >= TRACK_WEAR_LIMIT)) + 1 if wear[-1] >= TRACK_WEAR_LIMIT else None
    track_jobs = int(wear[-1] // TRACK_WEAR_LIMIT)

    failures_total = draws.sum(axis=1)
    days = p.horizon_days
    cost = (
        energy_kwh * days * ENERGY_PRICE_EUR_KWH
        + strategy["cost_per_train_day"] * p.fleet_size * days
        + float(failures_total.mean()) * FAILURE_COST_EUR
        + track_jobs * TRACK_MAINTENANCE_EUR
    )

    return {
        "summary": {
            "trains_required": required,
            "trains_available": available,
            "effective_headway_min": round(effective_headway, 2),
            "cycle_time_min": round(cycle_s / 60.0, 1),
            "trips_per_day": round(trips),
            "train_km_per_day": round(train_km),
            "passengers_per_day": round(passengers),
            "unserved_per_day": round(unserved),
            "energy_mwh_per_day": round(energy_kwh / 1000.0, 2),
            "co2_t_per_day": round(energy_kwh * GRID_CO2_KG_KWH / 1000.0, 2),
            "failures_mean": round(float(failures_total.mean()), 1),
            "failures_p90": float(np.percentile(failures_total, 90)),
            "otp_pct": round(float(otp.mean()), 2),
            "cost_total_eur": round(cost),
            "cost_per_train_km_eur": round(cost / (train_km * days), 2),
            "track_maintenance_day": track_day,
            "failures_by_component": {k: round(v * days, 2) for k, v in expected.items()},
        },
        "otp_daily": otp.mean(axis=0),
        "failures_cumulative": np.cumsum(draws.mean(axis=0)),
        "wear": wear,
        "utilisation": utilisation,
    }


def run_scenario(params: ScenarioParams, seed: int = 42) -> dict:
    if params.maintenance_strategy not in STRATEGIES:
        raise ValueError(f"Bilinmeyen bakım stratejisi {params.maintenance_strategy!r}")
    baseline_params = replace(BASELINE, horizon_days=params.horizon_days)
    base = _simulate(baseline_params, np.random.default_rng(seed))
    scen = _simulate(params, np.random.default_rng(seed))

    warnings = []
    s = scen["summary"]
    if s["trains_required"] > s["trains_available"]:
        warnings.append(
            f"{s['trains_required']} tren gerekiyor ancak yalnızca {s['trains_available']} tren kullanılabilir; "
            f"sefer aralığı {s['effective_headway_min']} dk'ya uzuyor."
        )
    if s["unserved_per_day"] > 0:
        warnings.append(f"Talep kapasiteyi aşıyor: günde ~{s['unserved_per_day']:,} yolcu perona kalıyor.")
    if params.ambient_c >= 35:
        warnings.append("Süregelen sıcak hava rulman ve klima (HVAC) yükünü artırır; daha fazla arıza ve daha yüksek enerji tüketimi beklenir.")
    if s["track_maintenance_day"] is not None:
        warnings.append(f"Hat aşınması {s['track_maintenance_day']}. günde buraj eşiğine ulaşıyor.")

    daily = [
        {
            "day": d + 1,
            "baseline_otp": round(float(base["otp_daily"][d]), 2),
            "scenario_otp": round(float(scen["otp_daily"][d]), 2),
            "baseline_failures": round(float(base["failures_cumulative"][d]), 2),
            "scenario_failures": round(float(scen["failures_cumulative"][d]), 2),
            "baseline_wear": round(float(base["wear"][d]), 1),
            "scenario_wear": round(float(scen["wear"][d]), 1),
        }
        for d in range(params.horizon_days)
    ]
    return {
        "params": asdict(params),
        "baseline_params": asdict(baseline_params),
        "baseline": base["summary"],
        "scenario": s,
        "daily": daily,
        "warnings": warnings,
    }
