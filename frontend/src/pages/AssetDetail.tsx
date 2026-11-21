import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Area, AreaChart, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, FAULT_LABELS, TYPE_LABELS, metricLabel, metricUnit, statusLabel, SWITCH_POSITION_LABELS, type AssetType, type ExplanationResponse, type Series } from "../api";
import { Empty, HealthBadge, Panel, fmtTime, num, pct } from "../components/common";
import ExplainPanel from "../components/ExplainPanel";
import LineMap from "../components/LineMap";

const EXPLAIN_MINUTES = 30;
import { useLive } from "../live";

const CHART_METRICS: Record<AssetType, string[]> = {
  train: ["bearing_temp_c", "bogie_vibration_mm_s", "brake_pressure_bar", "motor_current_a", "speed_kmh",
    "traction_power_kw", "passenger_load_pct"],
  segment: ["vibration_rms", "geometry_dev_mm", "passing_speed_kmh", "rail_temp_c"],
  switch: ["throw_time_s", "motor_current_peak_a"],
};

const LIMITS: Record<string, { warning: number; critical: number }> = {
  bearing_temp_c: { warning: 85, critical: 100 },
  bogie_vibration_mm_s: { warning: 5, critical: 7 },
  brake_pressure_bar: { warning: 4.3, critical: 3.8 },
  vibration_rms: { warning: 5, critical: 6.5 },
  geometry_dev_mm: { warning: 4, critical: 6 },
  throw_time_s: { warning: 4.5, critical: 6 },
};

type Row = { t: number } & Record<string, number>;

export default function AssetDetail() {
  const { id = "" } = useParams();
  const { snapshot, topology, history } = useLive();
  const [faultKinds, setFaultKinds] = useState<string[]>([]);
  const [fault, setFault] = useState("");
  const [past, setPast] = useState<Series>({});
  const [explanation, setExplanation] = useState<ExplanationResponse | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setPast({});
    setMessage(null);
    api.asset(id).then((a) => {
      setFaultKinds(a.fault_kinds ?? []);
      setFault(a.fault_kinds?.[0] ?? "");
    }, () => setFaultKinds([]));
    api.telemetry(id, 15, 5).then(setPast, () => setPast({}));
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    setExplanation(null);
    const load = () =>
      api.explanation(id, EXPLAIN_MINUTES).then((r) => !cancelled && setExplanation(r), () => undefined);
    load();
    const timer = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [id]);

  const live = history[id] ?? [];
  const rows = useMemo<Row[]>(() => {
    const firstLive = live.length ? live[0].t : Infinity;
    const byTime = new Map<number, Row>();
    for (const [metric, points] of Object.entries(past)) {
      for (const p of points) {
        const t = new Date(p.t).getTime();
        if (t >= firstLive) continue;
        const row = byTime.get(t) ?? ({ t } as Row);
        row[metric] = p.v;
        byTime.set(t, row);
      }
    }
    const merged = [...byTime.values()].sort((a, b) => a.t - b.t);
    for (const p of live) merged.push({ t: p.t, ...p.metrics } as Row);
    return merged;
  }, [past, live]);

  const asset = snapshot?.assets[id];
  if (!snapshot || !topology) return <Empty>Bağlanıyor…</Empty>;
  if (!asset) return <Empty>“{id}” varlığı için henüz veri yok. <Link to="/assets">Varlıklara dön</Link></Empty>;

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      setMessage({ ok: true, text: ok });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const zEntries = Object.entries(asset.z);
  // Kayıtlı geçmiş (5 sn'de bir) + bu oturumun saniyelik canlı noktaları.
  const firstLive = live.length ? live[0].t : Infinity;
  const healthRows = [
    ...(explanation?.health ?? []).map((p) => ({ t: new Date(p.t).getTime(), health: p.v })).filter((p) => p.t < firstLive),
    ...live.map((p) => ({ t: p.t, health: p.health })),
  ];

  return (
    <div className="page">
      <div className="detail-head">
        <div>
          <Link to="/assets" className="muted small">← Tüm varlıklar</Link>
          <h1>{asset.name} <span className="muted">{asset.id}</span></h1>
          <div className="muted">
            {TYPE_LABELS[asset.type]}
            {asset.type === "train" && asset.extra.status && ` · ${statusLabel(asset.extra.status === "dwell" ? "dwell" : "running")}`}
            {asset.type === "switch" && asset.extra.position && ` · konum ${SWITCH_POSITION_LABELS[String(asset.extra.position)] ?? asset.extra.position}`}
            {!asset.online && " · çevrimdışı"}
          </div>
        </div>
        <div className="detail-health">
          <span className="muted small">Sağlık</span>
          <HealthBadge health={asset.health} status={asset.status} />
        </div>
      </div>

      <div className="grid-detail">
        <Panel title="Teşhis">
          <dl className="facts">
            <dt>Durum</dt>
            {asset.warming ? (
              <dd className="muted">Isınıyor – puanlamadan önce birkaç dakikalık geçmiş toplanıyor</dd>
            ) : (
              <dd className={`txt-${asset.status}`}>{statusLabel(asset.status)}</dd>
            )}
            <dt>Olası sebep</dt>
            <dd>{asset.cause_label ?? "Anomali yok"}</dd>
            <dt>Önerilen işlem</dt>
            <dd>{asset.status === "healthy" ? "Yok – rutin bakım planı" : asset.recommended_action}</dd>
            <dt>Eğilim</dt>
            <dd>{asset.trend_per_min > 0 ? "+" : ""}{num(asset.trend_per_min, 1)} puan/dk</dd>
            <dt>Kritiğe kalan süre</dt>
            <dd>
              {asset.status === "critical"
                ? "Şimdi"
                : asset.time_to_critical_h != null
                  ? `~${num(asset.time_to_critical_h, 1)} işletme saati`
                  : "Düşüş eğilimi yok"}
            </dd>
          </dl>

          <h3>Öğrenilmiş referanstan sensör sapması</h3>
          <div className="zbars">
            {zEntries.length === 0 && <span className="muted small">Model ısınıyor…</span>}
            {zEntries.map(([m, z]) => (
              <div key={m} className={`zbar ${asset.cause === m ? "flag" : ""}`}>
                <span>{metricLabel(m)}</span>
                <div className="ztrack">
                  <div className="zmid" />
                  <div
                    className={Math.abs(z) > 3 ? "zfill hot" : "zfill"}
                    style={{
                      left: z >= 0 ? "50%" : `${50 - Math.min(50, Math.abs(z) * 5)}%`,
                      width: `${Math.min(50, Math.abs(z) * 5)}%`,
                    }}
                  />
                </div>
                <b>{z > 0 ? "+" : ""}{num(z, 1)}σ</b>
              </div>
            ))}
          </div>

          <div className="actions">
            <button className="primary" disabled={busy}
              onClick={() => run(() => api.dispatch(id), "İş emri tamamlandı – bakım ekibi arızayı giderdi.")}>
              Bakım ekibi gönder
            </button>
            <div className="inject">
              <select value={fault} onChange={(e) => setFault(e.target.value)} aria-label="Arıza türü">
                {faultKinds.map((k) => <option key={k} value={k}>{FAULT_LABELS[k] ?? k}</option>)}
              </select>
              <button disabled={busy || !fault}
                onClick={() => run(() => api.injectFault(id, fault),
                  `“${FAULT_LABELS[fault] ?? fault}” enjekte edildi. Modelin bunu bir iki dakika içinde fark etmesini izleyin.`)}>
                Arıza enjekte et
              </button>
            </div>
            {message && <p className={message.ok ? "ok" : "error"}>{message.text}</p>}
          </div>
        </Panel>

        <Panel title={`Sağlık (son ${EXPLAIN_MINUTES} dk)`}>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={healthRows} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <defs>
                <linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3fb7ff" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#3fb7ff" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={fmtTime} minTickGap={40} />
              <YAxis domain={[0, 100]} ticks={[0, 45, 75, 100]} />
              <ReferenceLine y={75} stroke="#fbbf24" strokeDasharray="3 3" />
              <ReferenceLine y={45} stroke="#f87171" strokeDasharray="3 3" />
              <Tooltip labelFormatter={(t) => fmtTime(Number(t))} formatter={(v: number) => [pct(v, 1), "Sağlık"]} />
              <Area dataKey="health" stroke="#3fb7ff" fill="url(#hg)" isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
          <div className="minimap">
            <LineMap topology={topology} assets={snapshot.assets} highlight={id} />
          </div>
        </Panel>
      </div>

      <ExplainPanel data={explanation} minutes={EXPLAIN_MINUTES} />

      <div className="charts">
        {CHART_METRICS[asset.type].map((m) => (
          <MetricChart key={m} metric={m} rows={rows} value={asset.metrics[m]} flagged={asset.cause === m} />
        ))}
      </div>
    </div>
  );
}

function MetricChart({ metric, rows, value, flagged }: { metric: string; rows: Row[]; value?: number; flagged: boolean }) {
  const limit = LIMITS[metric];
  return (
    <Panel
      className={flagged ? "chart flagged" : "chart"}
      title={metricLabel(metric)}
      actions={<span className="chart-value">{num(value, 2)} <small>{metricUnit(metric)}</small></span>}
    >
      <ResponsiveContainer width="100%" height={150}>
        <LineChart data={rows} margin={{ top: 6, right: 8, left: -12, bottom: 0 }}>
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={fmtTime} minTickGap={40} />
          <YAxis domain={["auto", "auto"]} width={52} tickFormatter={(v: number) => v.toLocaleString("tr-TR")} />
          {limit && <ReferenceLine y={limit.warning} stroke="#fbbf24" strokeDasharray="3 3" />}
          {limit && <ReferenceLine y={limit.critical} stroke="#f87171" strokeDasharray="3 3" />}
          <Tooltip labelFormatter={(t) => fmtTime(Number(t))}
            formatter={(v: number) => [`${num(v, 2)} ${metricUnit(metric)}`, metricLabel(metric)]} />
          <Line dataKey={metric} dot={false} stroke={flagged ? "#f87171" : "#3fb7ff"} strokeWidth={1.6}
            isAnimationActive={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </Panel>
  );
}
