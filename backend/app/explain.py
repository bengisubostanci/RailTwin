"""“Sağlık neden düştü?” – kayıtlı sağlık ve sapma geçmişinden açıklama üretir.

Son sağlık düşüşü (bölüm) bulunur. Bölümde hangi sensörün beklenen değerden
ilk ne zaman ayrıldığı (sapma başlangıcı), sağlığın eşiklerin altına ne zaman
indiği ve diğer sensörlerin normal kalıp kalmadığı çıkarılır. Metin üretimi
arayüze bırakılır; burada yalnızca yapılandırılmış sonuç döner.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .ml import SPECS, Z_ONSET

DROP_BELOW = 90.0  # bunun altına inen sağlık bir "düşüş" sayılır
CALM_ABOVE = 95.0  # düşüş öncesi sakin dönemin sınırı
HEALTHY = 75.0
CRITICAL = 45.0
ONSET_LOOKBACK = timedelta(minutes=2)  # sapma, sağlık tepki vermeden biraz önce başlayabilir

Point = tuple[datetime, float]


def bad_direction(asset_type: str, z: dict[str, list[Point]]) -> dict[str, list[Point]]:
    """Sapmaları, pozitif değer her zaman “daha kötü” anlamına gelecek şekilde çevirir."""
    direction = {m.metric: m.direction for m in SPECS[asset_type].monitored}
    return {metric: [(t, v * direction[metric]) for t, v in points]
            for metric, points in z.items() if metric in direction}


def explain(health: list[Point], bad_z: dict[str, list[Point]]) -> dict | None:
    """Son sağlık düşüşünü açıklar; pencerede düşüş yoksa None döner."""
    if not health:
        return None
    last_low = next((i for i in range(len(health) - 1, -1, -1) if health[i][1] < DROP_BELOW), None)
    if last_low is None:
        return None

    first = last_low
    while first > 0 and health[first - 1][1] < CALM_ABOVE:
        first -= 1
    truncated = first == 0 and health[0][1] < CALM_ABOVE
    start = health[first - 1][0] if first > 0 else health[0][0]
    episode = [p for p in health if p[0] >= start]

    min_t, min_v = min(episode, key=lambda p: p[1])
    degraded_at = next((t for t, v in episode if v < HEALTHY), None)
    critical_at = next((t for t, v in episode if v < CRITICAL), None)
    recovered_at = None
    if degraded_at is not None:
        recovered_at = next((t for t, v in episode if t > min_t and v >= HEALTHY), None)

    window_start = start - ONSET_LOOKBACK
    window_end = recovered_at or health[-1][0]  # toparlanmadan sonraki sıçramalar bu düşüşe ait değil
    peaks: dict[str, float] = {}
    crossings: dict[str, datetime] = {}
    for metric, points in bad_z.items():
        in_window = [(t, v) for t, v in points if window_start <= t <= window_end]
        if not in_window:
            continue
        peaks[metric] = round(max(v for _, v in in_window), 2)
        crossing = next((t for t, v in in_window if v >= Z_ONSET), None)
        if crossing is not None:
            crossings[metric] = crossing

    cause = max(peaks, key=peaks.get) if peaks and max(peaks.values()) >= Z_ONSET else None
    onset_metric = cause if cause in crossings else (min(crossings, key=crossings.get) if crossings else None)
    onset_t = crossings.get(onset_metric) if onset_metric else None

    # Ana sapmadan sonra başlayıp ondan küçük kalanlar büyük olasılıkla aynı arızanın yan
    # etkisidir (aşınan rulman titreşimi de artırır); diğerleri ayrı bir sorun olabilir.
    secondary, independent = [], []
    for metric, crossing in sorted(crossings.items(), key=lambda kv: kv[1]):
        if metric == cause:
            continue
        follows = onset_t is not None and crossing > onset_t and peaks[metric] < peaks.get(cause, 0.0)
        (secondary if follows else independent).append(metric)

    lead_s = None
    if onset_t is not None and degraded_at is not None and degraded_at > onset_t:
        lead_s = round((degraded_at - onset_t).total_seconds())

    return {
        "start": start.isoformat(),
        "start_truncated": truncated,
        "cause": cause,
        "onset": {"t": onset_t.isoformat(), "metric": onset_metric} if onset_t else None,
        "peak_z": peaks,
        "others_normal": not secondary and not independent,
        "secondary": secondary,
        "independent": independent,
        "min_health": round(min_v, 1),
        "min_at": min_t.isoformat(),
        "degraded_at": degraded_at.isoformat() if degraded_at else None,
        "critical_at": critical_at.isoformat() if critical_at else None,
        "recovered_at": recovered_at.isoformat() if recovered_at else None,
        "ongoing": health[-1][1] < HEALTHY,
        "lead_s": lead_s,
        "z_onset": Z_ONSET,
    }
