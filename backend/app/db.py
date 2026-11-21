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

-- Modelin çıktısı: "health" satırları sağlık puanı, diğerleri sensör başına sapma (σ).
CREATE TABLE IF NOT EXISTS scores (
    time      TIMESTAMPTZ      NOT NULL,
    asset_id  TEXT             NOT NULL,
    metric    TEXT             NOT NULL,
    value     DOUBLE PRECISION NOT NULL
);
SELECT create_hypertable('scores', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS scores_asset_metric_time ON scores (asset_id, metric, time DESC);

-- Arayüzden/demodan enjekte edilen arızalar ve modelin bunları ne zaman, hangi sebeple yakaladığı.
CREATE TABLE IF NOT EXISTS fault_trials (
    id             SERIAL PRIMARY KEY,
    asset_id       TEXT NOT NULL,
    kind           TEXT NOT NULL,
    expected_cause TEXT,
    injected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    detected_at    TIMESTAMPTZ,
    detect_s       DOUBLE PRECISION,
    detected_cause TEXT,
    repaired_at    TIMESTAMPTZ
);

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
-- Backend yeniden başlarken kapatılan alarmlar: gerçek süreleri bilinmez, istatistiklere girmez.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS closed_on_restart BOOLEAN NOT NULL DEFAULT FALSE;

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
        self._scores: list[tuple] = []

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
            for table in ("telemetry", "scores"):
                await conn.execute(
                    f"SELECT add_retention_policy('{table}', $1::text::interval, if_not_exists => TRUE)",
                    self.retention,
                )
            # Önceki çalışmadan açık kalan alarmlar artık izlenemez; bunları sistemin kapandığı anla
            # (son kayıtlı telemetri) kapat ve işaretle ki kapalı kalınan süre alarm süresi sayılmasın.
            await conn.execute("""
                UPDATE alerts SET closed_on_restart = TRUE,
                       resolved_at = greatest(created_at, coalesce((SELECT max(time) FROM telemetry), now()))
                WHERE resolved_at IS NULL""")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    # ---------------------------------------------------------------- telemetri
    def buffer_reading(self, ts: datetime, reading: dict) -> None:
        for metric, value in reading["metrics"].items():
            self._buffer.append((ts, reading["asset_id"], metric, float(value)))

    def buffer_scores(self, ts: datetime, asset_id: str, values: dict[str, float]) -> None:
        for metric, value in values.items():
            self._scores.append((ts, asset_id, metric, float(value)))

    async def flush(self) -> int:
        if not self.pool:
            return 0
        written = 0
        for table, attr in (("telemetry", "_buffer"), ("scores", "_scores")):
            rows = getattr(self, attr)
            if not rows:
                continue
            setattr(self, attr, [])
            async with self.pool.acquire() as conn:
                await conn.copy_records_to_table(table, records=rows, columns=["time", "asset_id", "metric", "value"])
            written += len(rows)
        return written

    async def telemetry(self, asset_id: str, minutes: int, bucket_s: int) -> dict[str, list[dict]]:
        return await self._series("telemetry", asset_id, minutes, bucket_s)

    async def scores(self, asset_id: str, minutes: int, bucket_s: int) -> dict[str, list[dict]]:
        return await self._series("scores", asset_id, minutes, bucket_s)

    async def _series(self, table: str, asset_id: str, minutes: int, bucket_s: int) -> dict[str, list[dict]]:
        query = f"""
            SELECT time_bucket(make_interval(secs => $3), time) AS bucket, metric, avg(value) AS value
            FROM {table}
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

    # ------------------------------------------------------- arıza denemeleri
    async def create_trial(self, asset_id: str, kind: str, expected_cause: str | None) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO fault_trials (asset_id, kind, expected_cause) VALUES ($1, $2, $3) RETURNING *",
                asset_id, kind, expected_cause,
            )
        return _row(row)

    async def mark_trial_detected(self, trial_id: int, detect_s: float, cause: str | None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE fault_trials SET detected_at = now(), detect_s = $2, detected_cause = $3 WHERE id = $1",
                trial_id, detect_s, cause,
            )

    async def close_trial(self, trial_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE fault_trials SET repaired_at = now() WHERE id = $1 AND repaired_at IS NULL",
                               trial_id)

    async def trials(self, limit: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM fault_trials ORDER BY injected_at DESC LIMIT $1", limit)
        return [_row(r) for r in rows]

    async def work_orders(self, limit: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM work_orders ORDER BY created_at DESC LIMIT $1", limit)
        return [_row(r) for r in rows]


    # ----------------------------------------------------------------- geçmiş
    async def history(self, days: int, tz: str, limit: int = 200) -> dict:
        """Son `days` gündeki alarm ve iş emirlerinin özeti, zaman dağılımı ve listeleri."""
        unit = "hour" if days <= 2 else "day"
        since = "now() - make_interval(days => $1)"
        async with self.pool.acquire() as conn:
            totals = await conn.fetchrow(f"""
                SELECT count(*) AS alerts,
                       count(*) FILTER (WHERE severity = 'critical') AS critical,
                       count(*) FILTER (WHERE severity = 'warning') AS warning,
                       count(*) FILTER (WHERE source = 'ml') AS ml,
                       count(*) FILTER (WHERE source = 'rule') AS rule,
                       count(DISTINCT asset_id) AS assets_affected,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM resolved_at - created_at))
                           FILTER (WHERE resolved_at IS NOT NULL AND NOT closed_on_restart) AS median_alert_s
                FROM alerts WHERE created_at > {since}""", days)
            orders = await conn.fetchrow(f"""
                SELECT count(*) AS work_orders, avg(extract(epoch FROM w.created_at - first_alert)) AS mean_response_s
                FROM work_orders w
                LEFT JOIN LATERAL (
                    -- Varlıkta iş emri anında hâlâ açık olan alarm bölümünün ilk alarmı.
                    SELECT min(a.created_at) AS first_alert FROM alerts a
                    WHERE a.asset_id = w.asset_id AND a.created_at <= w.created_at
                      AND a.created_at > w.created_at - interval '6 hours'
                      AND (a.resolved_at IS NULL OR a.resolved_at >= w.created_at)
                ) fa ON TRUE
                WHERE w.created_at > {since}""", days)
            timeline = await conn.fetch(f"""
                WITH a AS (
                    SELECT date_trunc('{unit}', created_at AT TIME ZONE $2) AS b,
                           count(*) FILTER (WHERE severity = 'critical') AS critical,
                           count(*) FILTER (WHERE severity = 'warning') AS warning
                    FROM alerts WHERE created_at > {since} GROUP BY 1),
                w AS (
                    SELECT date_trunc('{unit}', created_at AT TIME ZONE $2) AS b, count(*) AS work_orders
                    FROM work_orders WHERE created_at > {since} GROUP BY 1)
                SELECT coalesce(a.b, w.b) AS bucket, coalesce(a.critical, 0) AS critical,
                       coalesce(a.warning, 0) AS warning, coalesce(w.work_orders, 0) AS work_orders
                FROM a FULL JOIN w ON a.b = w.b ORDER BY 1""", days, tz)
            top = await conn.fetch(f"""
                SELECT asset_id, count(*) AS alerts, count(*) FILTER (WHERE severity = 'critical') AS critical,
                       (SELECT count(*) FROM work_orders w WHERE w.asset_id = a.asset_id AND w.created_at > {since})
                           AS work_orders
                FROM alerts a WHERE created_at > {since}
                GROUP BY asset_id ORDER BY alerts DESC, critical DESC LIMIT 8""", days)
            causes = await conn.fetch(f"""
                SELECT cause, count(*) AS work_orders FROM work_orders
                WHERE created_at > {since} GROUP BY cause ORDER BY 2 DESC""", days)
            alerts = await conn.fetch(f"""
                SELECT * FROM alerts WHERE created_at > {since} ORDER BY created_at DESC LIMIT $2""", days, limit)
            work_orders = await conn.fetch(f"""
                SELECT * FROM work_orders WHERE created_at > {since} ORDER BY created_at DESC LIMIT $2""", days, limit)

        def num(v):
            return None if v is None else round(float(v), 1)

        return {
            "unit": unit,
            "totals": {**{k: totals[k] for k in ("alerts", "critical", "warning", "ml", "rule", "assets_affected")},
                       "median_alert_s": num(totals["median_alert_s"]), "work_orders": orders["work_orders"],
                       "mean_response_s": num(orders["mean_response_s"])},
            # Gruplar kullanıcının saat diliminde; saat dilimi bilgisi olmadan döner.
            "timeline": [{**dict(r), "bucket": r["bucket"].isoformat()} for r in timeline],
            "top_assets": [dict(r) for r in top],
            "causes": [dict(r) for r in causes],
            "alerts": [_row(r) for r in alerts],
            "work_orders": [_row(r) for r in work_orders],
        }

    async def export_rows(self, kind: str, days: int, tz: str) -> list[asyncpg.Record]:
        """CSV için satırlar; zamanlar kullanıcının saat diliminde metin olarak."""
        local = "to_char({} AT TIME ZONE $2, 'YYYY-MM-DD HH24:MI:SS')"
        columns = {
            "alerts": f"id, {local.format('created_at')} AS created, {local.format('resolved_at')} AS resolved, "
                      "asset_id, severity, source, key, message, acknowledged",
            "work_orders": f"id, {local.format('created_at')} AS created, asset_id, action, cause, "
                           "round(health::numeric, 1) AS health, status",
        }[kind]
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                f"SELECT {columns} FROM {kind} WHERE created_at > now() - make_interval(days => $1) ORDER BY created_at",
                days, tz,
            )


def _row(row: asyncpg.Record) -> dict:
    out = {}
    for key, value in dict(row).items():
        out[key] = value.isoformat() if isinstance(value, datetime) else value
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
