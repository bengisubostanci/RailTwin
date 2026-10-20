"""TimescaleDB erişimi: şema, toplu telemetri yazımı, alarmlar ve iş emirleri."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import asyncpg

log = logging.getLogger(__name__)

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS assets (
    id    TEXT PRIMARY KEY,
    type  TEXT NOT NULL,
    name  TEXT NOT NULL,
    meta  JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS telemetry (
    time      TIMESTAMPTZ      NOT NULL,
    asset_id  TEXT             NOT NULL,
    metric    TEXT             NOT NULL,
    value     DOUBLE PRECISION NOT NULL
);
SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS telemetry_asset_metric_time ON telemetry (asset_id, metric, time DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ,
    acknowledged BOOLEAN     NOT NULL DEFAULT FALSE,
    asset_id     TEXT        NOT NULL,
    key          TEXT        NOT NULL,
    severity     TEXT        NOT NULL,
    source       TEXT        NOT NULL,
    message      TEXT        NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts (resolved_at) WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS work_orders (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    asset_id     TEXT        NOT NULL,
    action       TEXT        NOT NULL,
    cause        TEXT,
    health       DOUBLE PRECISION,
    status       TEXT        NOT NULL DEFAULT 'dispatched'
);
"""


class Database:
    def __init__(self, dsn: str, retention: str):
        self.dsn = dsn
        self.retention = retention
        self.pool: asyncpg.Pool | None = None
        self._buffer: list[tuple] = []

    async def connect(self, attempts: int = 30) -> None:
        for attempt in range(1, attempts + 1):
            try:
                self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8)
                break
            except (OSError, asyncpg.PostgresError) as exc:
                log.warning("Veritabanı hazır değil (%s), deneme %d/%d", exc, attempt, attempts)
                await asyncio.sleep(2)
        else:
            raise RuntimeError("Veritabanına bağlanılamadı")
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)
            await conn.execute(
                "SELECT add_retention_policy('telemetry', $1::text::interval, if_not_exists => TRUE)", self.retention
            )
            # Önceki çalışmadan açık kalan alarmlar artık izlenemez; bunları kapat.
            await conn.execute("UPDATE alerts SET resolved_at = now() WHERE resolved_at IS NULL")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    # ---------------------------------------------------------------- telemetri
    def buffer_reading(self, ts: datetime, reading: dict) -> None:
        for metric, value in reading["metrics"].items():
            self._buffer.append((ts, reading["asset_id"], metric, float(value)))

    async def flush(self) -> int:
        if not self._buffer or not self.pool:
            return 0
        rows, self._buffer = self._buffer, []
        async with self.pool.acquire() as conn:
            await conn.copy_records_to_table("telemetry", records=rows, columns=["time", "asset_id", "metric", "value"])
        return len(rows)

    async def telemetry(self, asset_id: str, minutes: int, bucket_s: int) -> dict[str, list[dict]]:
        query = """
            SELECT time_bucket(make_interval(secs => $3), time) AS bucket, metric, avg(value) AS value
            FROM telemetry
            WHERE asset_id = $1 AND time > now() - make_interval(mins => $2)
            GROUP BY bucket, metric
            ORDER BY bucket
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, asset_id, minutes, float(bucket_s))
        series: dict[str, list[dict]] = {}
        for r in rows:
            series.setdefault(r["metric"], []).append({"t": r["bucket"].isoformat(), "v": round(r["value"], 3)})
        return series

    async def recent(self, asset_ids: list[str], metrics: list[str], minutes: int) -> list[asyncpg.Record]:
        """Yeniden başlatma sonrası hareketli ortalamaları yeniden kurmak için son ham okumalar (en eskiden yeniye)."""
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT time, asset_id, metric, value FROM telemetry
                   WHERE asset_id = ANY($1) AND metric = ANY($2) AND time > now() - make_interval(mins => $3)
                   ORDER BY time""",
                asset_ids, metrics, minutes,
            )

    # ---------------------------------------------------------------- varlıklar
    async def upsert_assets(self, assets: list[tuple[str, str, str, dict]]) -> None:
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO assets (id, type, name, meta) VALUES ($1, $2, $3, $4)
                   ON CONFLICT (id) DO UPDATE SET type = EXCLUDED.type, name = EXCLUDED.name, meta = EXCLUDED.meta""",
                [(i, t, n, json.dumps(m)) for i, t, n, m in assets],
            )

    # ----------------------------------------------------------------- alarmlar
    async def open_alert(self, asset_id: str, key: str, severity: str, source: str, message: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO alerts (asset_id, key, severity, source, message)
                   VALUES ($1, $2, $3, $4, $5) RETURNING *""",
                asset_id, key, severity, source, message,
            )
        return _row(row)

    async def update_alert(self, alert_id: int, severity: str, message: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE alerts SET severity = $2, message = $3 WHERE id = $1", alert_id, severity, message)

    async def resolve_alert(self, alert_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE alerts SET resolved_at = now() WHERE id = $1", alert_id)

    async def acknowledge_alert(self, alert_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("UPDATE alerts SET acknowledged = TRUE WHERE id = $1", alert_id)
        return result.endswith("1")

    async def alerts(self, active_only: bool, limit: int) -> list[dict]:
        where = "WHERE resolved_at IS NULL" if active_only else ""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT * FROM alerts {where} ORDER BY created_at DESC LIMIT $1", limit)
        return [_row(r) for r in rows]

    # -------------------------------------------------------------- iş emirleri
    async def create_work_order(self, asset_id: str, action: str, cause: str | None, health: float) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO work_orders (asset_id, action, cause, health, status, completed_at)
                   VALUES ($1, $2, $3, $4, 'completed', now()) RETURNING *""",
                asset_id, action, cause, health,
            )
        return _row(row)

    async def work_orders(self, limit: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM work_orders ORDER BY created_at DESC LIMIT $1", limit)
        return [_row(r) for r in rows]


def _row(row: asyncpg.Record) -> dict:
    out = {}
    for key, value in dict(row).items():
        out[key] = value.isoformat() if isinstance(value, datetime) else value
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
