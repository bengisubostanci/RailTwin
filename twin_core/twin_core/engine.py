"""Hattın fizik motoru: tren kinematiği, sensör modelleri ve bozulma arızaları.

Buradaki her şey verilen bir tohum (seed) için deterministiktir. Birimler içeride SI'dır
(m, m/s, *simülasyon* zamanı saniyesi); metrik adları görüntüleme birimini taşır.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .topology import Topology, TrainSpec, default_topology

VMAX = 80 / 3.6  # m/s
ACCEL = 1.0  # m/s²
BRAKE = 1.0  # m/s²
DWELL_S = 30.0
TURNAROUND_S = 120.0
LINE_VOLTAGE = 750.0  # V DC
AUX_POWER_KW = 60.0
MAX_SUBSTEP_S = 1.0
MAX_TRACTION_KW = 3200.0

FAULT_KINDS: dict[str, list[str]] = {
    "train": ["bearing_overheat", "wheel_flat", "brake_leak", "motor_degradation"],
    "segment": ["rail_defect", "geometry_drift"],
    "switch": ["switch_wear"],
}


@dataclass
class Environment:
    ambient_c: float = 18.0
    load_factor: float = 0.6


@dataclass
class Fault:
    kind: str
    severity: float
    rate: float  # simülasyon saniyesi başına artan şiddet

    def advance(self, dt: float) -> None:
        self.severity = min(1.0, self.severity + self.rate * dt)


class _Asset:
    type = ""

    def __init__(self, asset_id: str, rng: random.Random):
        self.id = asset_id
        self.rng = rng
        self.faults: dict[str, Fault] = {}

    def sev(self, kind: str) -> float:
        fault = self.faults.get(kind)
        return fault.severity if fault else 0.0

    def noise(self, sigma: float) -> float:
        return self.rng.gauss(0.0, sigma)


class TrainSim(_Asset):
    type = "train"

    def __init__(self, spec: TrainSpec, topo: Topology, rng: random.Random, env: Environment):
        super().__init__(spec.id, rng)
        self.topo = topo
        self.stops = [s.position_m for s in topo.stations]
        self.station_idx = spec.start_station
        self.pos = self.stops[self.station_idx]
        self.dir = spec.direction
        self.v = 0.0
        self.accel = 0.0
        self.power_kw = AUX_POWER_KW
        self.state = "dwell"
        self.dwell_left = rng.uniform(5.0, DWELL_S)
        self.load = env.load_factor
        self.bearing_temp = env.ambient_c + 12.0

    @property
    def target_idx(self) -> int:
        return self.station_idx + self.dir

    def _depart(self, env: Environment) -> None:
        self.state = "run"
        self.load = min(1.3, max(0.05, env.load_factor + self.noise(0.12)))

    def _arrive(self) -> None:
        self.station_idx = self.target_idx
        self.pos = self.stops[self.station_idx]
        self.v = 0.0
        self.state = "dwell"
        if self.station_idx in (0, len(self.stops) - 1):
            self.dir = -self.dir
            self.dwell_left = TURNAROUND_S + self.rng.uniform(-10, 10)
        else:
            self.dwell_left = DWELL_S + self.rng.uniform(-5, 5)

    def step(self, dt: float, env: Environment) -> None:
        v0 = self.v
        if self.state == "dwell":
            self.dwell_left -= dt
            if self.dwell_left <= 0:
                self._depart(env)
        else:
            remaining = abs(self.stops[self.target_idx] - self.pos)
            braking_distance = self.v * self.v / (2 * BRAKE)
            if braking_distance >= remaining - 2.0:
                # Trenin durma noktasında durması için ideal fren eğrisini izle.
                self.v = math.sqrt(max(0.0, 2 * BRAKE * max(0.0, remaining - self.v * dt)))
            else:
                # Sabit torkla kalkış, temel hızın üzerinde ise sabit güç.
                mass_kg = (180.0 + 60.0 * self.load) * 1000.0
                power_limited = MAX_TRACTION_KW * 1000.0 / max(self.v, 1.0) / mass_kg
                self.v = min(VMAX, self.v + min(ACCEL, power_limited) * dt)
            travelled = (v0 + self.v) / 2 * dt
            if travelled >= remaining or remaining < 1.0:
                self._arrive()
            else:
                self.pos += self.dir * travelled

        self.accel = (self.v - v0) / dt
        mass_kg = (180.0 + 60.0 * self.load) * 1000.0
        resistance_n = mass_kg * 0.015 + 8.0 * self.v * self.v
        tractive_kw = (mass_kg * self.accel + resistance_n) * self.v / 1000.0
        self.power_kw = AUX_POWER_KW + (tractive_kw / 0.85 if tractive_kw > 0 else tractive_kw * 0.6)

        kmh = self.v * 3.6
        target = env.ambient_c + 12.0 + 0.35 * kmh + 6.0 * self.load + 55.0 * self.sev("bearing_overheat")
        self.bearing_temp += (target - self.bearing_temp) * min(1.0, dt / 600.0)

    def fit_new_bearing(self, env: Environment) -> None:
        """Yeni takılan rulman, sağlıklı bir rulmanın sahip olacağı sıcaklıkta başlar."""
        self.bearing_temp = env.ambient_c + 12.0 + 0.35 * self.v * 3.6 * 0.5 + 6.0 * self.load

    def readings(self, env: Environment) -> dict:
        kmh = self.v * 3.6
        current = max(0.0, self.power_kw) * 1000.0 / LINE_VOLTAGE
        current *= 1.0 + 0.35 * self.sev("motor_degradation")
        vibration = (
            0.6 + 0.035 * kmh
            + self.sev("wheel_flat") * 0.05 * kmh
            + self.sev("bearing_overheat") * 0.8
            + self.noise(0.15)
        )
        seg = self.topo.segment_at(self.pos)
        return {
            "asset_id": self.id,
            "type": self.type,
            "metrics": {
                "speed_kmh": round(kmh, 2),
                "traction_power_kw": round(self.power_kw + self.noise(3.0), 1),
                "motor_current_a": round(current * (1 + self.noise(0.02)), 1),
                "bearing_temp_c": round(self.bearing_temp + self.noise(0.3), 2),
                "bogie_vibration_mm_s": round(max(0.05, vibration), 3),
                "brake_pressure_bar": round(5.0 - 1.8 * self.sev("brake_leak") + self.noise(0.08), 3),
                "passenger_load_pct": round(self.load * 100.0, 1),
                "ambient_c": round(env.ambient_c, 1),
            },
            "extra": {
                "position_m": round(self.pos, 1),
                "direction": self.dir,
                "status": "dwell" if self.state == "dwell" else "running",
                "segment_id": seg.id,
                "station_id": self.topo.stations[self.station_idx].id if self.state == "dwell" else None,
                "next_station_id": self.topo.stations[self.target_idx].id
                if 0 <= self.target_idx < len(self.stops)
                else None,
            },
        }


class SegmentSim(_Asset):
    type = "segment"

    def __init__(self, segment, rng: random.Random, env: Environment):
        super().__init__(segment.id, rng)
        self.start_m = segment.start_m
        self.end_m = segment.end_m
        self.base_geometry = rng.uniform(1.2, 2.0)
        self.rail_temp = env.ambient_c + 6.0
        self.passing_kmh = 0.0

    def step(self, dt: float, env: Environment) -> None:
        self.rail_temp += (env.ambient_c + 6.0 - self.rail_temp) * min(1.0, dt / 900.0)

    def readings(self, env: Environment) -> dict:
        defect = self.sev("rail_defect")
        vibration = 0.25 + 0.022 * self.passing_kmh * (1 + 3.0 * defect) + self.noise(0.05)
        return {
            "asset_id": self.id,
            "type": self.type,
            "metrics": {
                "passing_speed_kmh": round(self.passing_kmh, 2),
                "vibration_rms": round(max(0.05, vibration), 3),
                "geometry_dev_mm": round(self.base_geometry + 6.0 * self.sev("geometry_drift") + self.noise(0.1), 3),
                "rail_temp_c": round(self.rail_temp + self.noise(0.2), 2),
                "ambient_c": round(env.ambient_c, 1),
            },
            "extra": {},
        }


class SwitchSim(_Asset):
    type = "switch"

    def __init__(self, switch, rng: random.Random, env: Environment):
        super().__init__(switch.id, rng)
        self.position = "normal"
        self.next_throw = rng.uniform(5, 60)
        self.throw_time = 3.2
        self.peak_current = 4.0
        self.throw_ambient = env.ambient_c
        self._throw(env)

    def _throw(self, env: Environment) -> None:
        wear = self.sev("switch_wear")
        cold = 0.015 * max(0.0, 10.0 - env.ambient_c)
        self.throw_time = 3.2 + cold + 4.0 * wear + self.noise(0.12)
        self.peak_current = 4.0 + 2.0 * cold + 3.0 * wear + self.noise(0.15)
        self.throw_ambient = env.ambient_c
        self.position = "reverse" if self.position == "normal" else "normal"

    def step(self, dt: float, env: Environment) -> None:
        self.next_throw -= dt
        if self.next_throw <= 0:
            self._throw(env)
            self.next_throw = self.rng.uniform(40, 120)

    def readings(self, env: Environment) -> dict:
        return {
            "asset_id": self.id,
            "type": self.type,
            "metrics": {
                "throw_time_s": round(self.throw_time, 3),
                "motor_current_peak_a": round(self.peak_current, 3),
                # Atma ölçümleri son atmada alınır; bu yüzden ölçümlerin alındığı
                # sıcaklığı da yanlarında raporla.
                "throw_ambient_c": round(self.throw_ambient, 1),
                "ambient_c": round(env.ambient_c, 1),
            },
            "extra": {"position": self.position},
        }


class TwinEngine:
    """Tüm hattı simüle eder. `step()` metodunu geçen gerçek zaman saniyesiyle çağırın."""

    def __init__(
        self,
        topology: Topology | None = None,
        *,
        seed: int | None = None,
        time_scale: float = 1.0,
        spontaneous_faults: bool = False,
        fault_interval_s: float = 300.0,
        warmup_s: float = 120.0,
        max_spontaneous: int = 2,
    ):
        self.topo = topology or default_topology()
        self.rng = random.Random(seed)
        self.env = Environment()
        self.time_scale = time_scale
        self.spontaneous_faults = spontaneous_faults
        self.fault_interval_s = fault_interval_s
        self.warmup_s = warmup_s
        self.max_spontaneous = max_spontaneous
        self.sim_time = 0.0
        self.real_time = 0.0

        self.trains = [TrainSim(t, self.topo, self.rng, self.env) for t in self.topo.trains]
        self.segments = [SegmentSim(s, self.rng, self.env) for s in self.topo.segments]
        self.switches = [SwitchSim(s, self.rng, self.env) for s in self.topo.switches]
        self.assets: dict[str, _Asset] = {a.id: a for a in [*self.trains, *self.segments, *self.switches]}
        self._spontaneous: set[str] = set()

    # ------------------------------------------------------------------ kontrol
    def set_env(self, ambient_c: float | None = None, load_factor: float | None = None) -> None:
        if ambient_c is not None:
            self.env.ambient_c = float(ambient_c)
        if load_factor is not None:
            self.env.load_factor = float(load_factor)

    def inject_fault(self, asset_id: str, kind: str | None = None, initial: float = 0.35,
                     ramp_real_s: float = 180.0) -> str:
        asset = self.assets[asset_id]
        kinds = FAULT_KINDS[asset.type]
        if kind is None:
            kind = self.rng.choice(kinds)
        if kind not in kinds:
            raise ValueError(f"{kind!r}, {asset.type} türü için geçerli bir arıza değil")
        rate = (1.0 - initial) / max(1.0, ramp_real_s * self.time_scale)
        asset.faults[kind] = Fault(kind, initial, rate)
        return kind

    def repair(self, asset_id: str) -> list[str]:
        asset = self.assets[asset_id]
        repaired = list(asset.faults)
        asset.faults.clear()
        if isinstance(asset, TrainSim) and "bearing_overheat" in repaired:
            asset.fit_new_bearing(self.env)
        self._spontaneous.discard(asset_id)
        return repaired

    def active_faults(self) -> dict[str, dict[str, float]]:
        """Gerçek durum; yalnızca testler ve loglar için. Backend'e asla yayınlanmaz."""
        return {
            a.id: {k: round(f.severity, 3) for k, f in a.faults.items()}
            for a in self.assets.values()
            if a.faults
        }

    # --------------------------------------------------------------- simülasyon
    def step(self, dt_real: float) -> list[dict]:
        self.real_time += dt_real
        dt = dt_real * self.time_scale
        n = max(1, math.ceil(dt / MAX_SUBSTEP_S))
        h = dt / n
        for _ in range(n):
            for train in self.trains:
                train.step(h, self.env)
            for asset in [*self.segments, *self.switches]:
                asset.step(h, self.env)
            for asset in self.assets.values():
                for fault in asset.faults.values():
                    fault.advance(h)
        self.sim_time += dt
        self._update_passing_speeds()
        self._maybe_spontaneous_fault(dt)
        return self.readings()

    def readings(self) -> list[dict]:
        return [a.readings(self.env) for a in self.assets.values()]  # type: ignore[attr-defined]

    def _update_passing_speeds(self) -> None:
        for seg in self.segments:
            seg.passing_kmh = sum(
                t.v * 3.6 for t in self.trains if seg.start_m <= t.pos < seg.end_m and t.state == "run"
            )

    def _maybe_spontaneous_fault(self, dt: float) -> None:
        if not self.spontaneous_faults or self.real_time < self.warmup_s:
            return
        self._spontaneous = {a for a in self._spontaneous if self.assets[a].faults}
        if len(self._spontaneous) >= self.max_spontaneous:
            return
        p = dt / (self.fault_interval_s * self.time_scale)
        if self.rng.random() < p:
            healthy = [a for a in self.assets.values() if not a.faults]
            if healthy:
                asset = self.rng.choice(healthy)
                self.inject_fault(asset.id, initial=0.05, ramp_real_s=480.0)
                self._spontaneous.add(asset.id)
