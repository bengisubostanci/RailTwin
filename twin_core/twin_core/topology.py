"""Hattın statik tanımı: istasyonlar, ray segmentleri, makaslar ve trenler."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    position_m: float
    # Şematik koordinatlar (SVG viewBox 1000 x 420), coğrafi değil.
    x: float
    y: float


@dataclass(frozen=True)
class Segment:
    id: str
    name: str
    from_station: str
    to_station: str
    start_m: float
    end_m: float


@dataclass(frozen=True)
class Switch:
    id: str
    name: str
    station_id: str
    position_m: float


@dataclass(frozen=True)
class TrainSpec:
    id: str
    name: str
    start_station: int
    direction: int  # +1 son istasyona doğru, -1 ilk istasyona doğru


@dataclass
class Topology:
    line_id: str
    line_name: str
    stations: list[Station]
    segments: list[Segment] = field(default_factory=list)
    switches: list[Switch] = field(default_factory=list)
    trains: list[TrainSpec] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return self.stations[-1].position_m

    def segment_at(self, position_m: float) -> Segment:
        for seg in self.segments:
            if seg.start_m <= position_m < seg.end_m:
                return seg
        return self.segments[-1]

    def asset_ids(self) -> dict[str, str]:
        """İzlenen her varlık kimliğini türüyle eşler."""
        ids = {t.id: "train" for t in self.trains}
        ids.update({s.id: "segment" for s in self.segments})
        ids.update({s.id: "switch" for s in self.switches})
        return ids

    def to_dict(self) -> dict:
        return {
            "line_id": self.line_id,
            "line_name": self.line_name,
            "length_m": self.length_m,
            "stations": [asdict(s) for s in self.stations],
            "segments": [asdict(s) for s in self.segments],
            "switches": [asdict(s) for s in self.switches],
            "trains": [asdict(t) for t in self.trains],
        }


_STATIONS = [
    ("Merkez", 60, 330),
    ("Nehirkıyı", 140, 290),
    ("Müze", 215, 245),
    ("Eski Şehir", 305, 218),
    ("Üniversite", 400, 200),
    ("Teknopark", 490, 172),
    ("Stadyum", 572, 134),
    ("Hastane", 655, 120),
    ("Kuzeykapı", 742, 140),
    ("Gölkıyı", 822, 182),
    ("Sanayi", 882, 242),
    ("Havalimanı", 940, 312),
]
# Ardışık istasyonlar arasındaki mesafe, metre cinsinden.
_SPACING = [1800, 2100, 1500, 2400, 1900, 2200, 1700, 2600, 2000, 1600, 2400]
_SWITCH_STATIONS = [0, 5, 8, 11]


def default_topology() -> Topology:
    stations: list[Station] = []
    pos = 0.0
    for i, (name, x, y) in enumerate(_STATIONS):
        stations.append(Station(f"ST-{i + 1:02d}", name, pos, x, y))
        if i < len(_SPACING):
            pos += _SPACING[i]

    segments = [
        Segment(
            id=f"SEG-{i + 1:02d}",
            name=f"{a.name} – {b.name}",
            from_station=a.id,
            to_station=b.id,
            start_m=a.position_m,
            end_m=b.position_m,
        )
        for i, (a, b) in enumerate(zip(stations, stations[1:]))
    ]

    switches = [
        Switch(f"SW-{n + 1:02d}", f"{stations[i].name} Makası", stations[i].id, stations[i].position_m)
        for n, i in enumerate(_SWITCH_STATIONS)
    ]

    starts = [(0, 1), (3, 1), (6, 1), (9, 1), (11, -1), (8, -1), (5, -1), (2, -1)]
    trains = [
        TrainSpec(f"T-{101 + n}", f"Tren {101 + n}", station, direction)
        for n, (station, direction) in enumerate(starts)
    ]

    return Topology("R1", "R1 Hattı · Merkez – Havalimanı", stations, segments, switches, trains)
