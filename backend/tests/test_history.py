"""Geçmişin CSV dışa aktarımı."""

import csv
import io

from app.history import to_csv


def parse(text):
    assert text.startswith("﻿"), "Excel'in UTF-8 algılaması için BOM gerekli"
    return list(csv.reader(io.StringIO(text[1:]), delimiter=";"))


def test_alerts_csv_uses_turkish_headers_and_values():
    rows = parse(to_csv("alerts", [{
        "id": 7, "created": "2026-11-21 23:30:40", "resolved": None, "asset_id": "T-103",
        "severity": "critical", "source": "ml", "key": "ml",
        "message": "Tren 103: anomali tespit edildi; sağlık %12", "acknowledged": False,
    }]))
    assert rows[0][:3] == ["No", "Açılış", "Kapanış"]
    assert rows[1] == ["7", "2026-11-21 23:30:40", "", "T-103", "kritik", "ML", "ml",
                       "Tren 103: anomali tespit edildi; sağlık %12", "hayır"]


def test_work_orders_csv_writes_decimal_comma():
    rows = parse(to_csv("work_orders", [{
        "id": 39, "created": "2026-11-21 23:19:31", "asset_id": "T-104", "action": "Aks rulmanını değiştir",
        "cause": "bearing_temp_c", "health": 12.4, "status": "completed",
    }]))
    assert rows[1][5] == "12,4"
    assert rows[1][6] == "tamamlandı"
