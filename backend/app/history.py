"""Geçmiş verisinin CSV olarak dışa aktarımı (Türkçe Excel ile uyumlu)."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence

EXPORT_COLUMNS = {
    "alerts": [("id", "No"), ("created", "Açılış"), ("resolved", "Kapanış"), ("asset_id", "Varlık"),
               ("severity", "Şiddet"), ("source", "Kaynak"), ("key", "Sensör"), ("message", "Mesaj"),
               ("acknowledged", "Onaylandı")],
    "work_orders": [("id", "No"), ("created", "Zaman"), ("asset_id", "Varlık"), ("action", "İşlem"),
                    ("cause", "Sebep"), ("health", "Gönderimdeki sağlık"), ("status", "Durum")],
}
EXPORT_VALUES = {
    "severity": {"critical": "kritik", "warning": "uyarı"},
    "source": {"ml": "ML", "rule": "kural"},
    "acknowledged": {True: "evet", False: "hayır"},
    "status": {"completed": "tamamlandı", "dispatched": "gönderildi"},
}


def to_csv(kind: str, rows: Sequence[Mapping]) -> str:
    """Türkçe Excel ondalık virgül kullandığı için ayırıcı noktalı virgül; baştaki BOM,
    Excel'in dosyayı UTF-8 olarak açıp Türkçe karakterleri doğru göstermesi için."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    columns = EXPORT_COLUMNS[kind]
    writer.writerow([title for _, title in columns])
    for r in rows:
        values = []
        for key, _ in columns:
            v = r[key]
            v = EXPORT_VALUES.get(key, {}).get(v, v)
            if key == "health" and v is not None:
                v = str(v).replace(".", ",")
            values.append("" if v is None else v)
        writer.writerow(values)
    return "\ufeff" + out.getvalue()
