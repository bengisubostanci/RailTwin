import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, TYPE_LABELS, metricLabel, statusLabel, type Alert, type History as HistoryData, type WorkOrder } from "../api";
import { Empty, Kpi, Panel, num } from "../components/common";
import { useLive } from "../live";

const RANGES = [
  { days: 1, label: "Son 24 saat" },
  { days: 7, label: "7 gün" },
  { days: 30, label: "30 gün" },
  { days: 90, label: "90 gün" },
];
const PAGE = 50;
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

/** Saniyeyi "45 sn", "12 dk", "3 sa 5 dk" veya "2 gün" biçiminde yazar. */
function duration(s: number | null | undefined): string {
  if (s == null) return "–";
  if (s < 60) return `${Math.round(s)} sn`;
  if (s < 3600) return `${Math.round(s / 60)} dk`;
  if (s < 86400) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return m ? `${h} sa ${m} dk` : `${h} sa`;
  }
  return `${num(s / 86400, 1)} gün`;
}

const dateTime = (iso: string) =>
  new Date(iso).toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

type Bucket = { t: number; critical: number; warning: number; work_orders: number };

/** Boş saat/günleri sıfırla doldurur ki eksen kesintisiz olsun. */
function fillTimeline(h: HistoryData): Bucket[] {
  const step = h.unit === "hour" ? 3600_000 : 86400_000;
  const byT = new Map(h.timeline.map((b) => [new Date(b.bucket).getTime(), b])); // yerel saat olarak okunur
  const end = new Date();
  if (h.unit === "hour") end.setMinutes(0, 0, 0);
  else end.setHours(0, 0, 0, 0);
  const out: Bucket[] = [];
  const count = h.unit === "hour" ? h.days * 24 : h.days;
  for (let i = count - 1; i >= 0; i--) {
    const t = end.getTime() - i * step;
    const b = byT.get(t);
    out.push({ t, critical: b?.critical ?? 0, warning: b?.warning ?? 0, work_orders: b?.work_orders ?? 0 });
  }
  return out;
}

export default function History() {
  const { snapshot } = useLive();
  const [days, setDays] = useState(7);
  const [data, setData] = useState<HistoryData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    const load = () =>
      api.history(days, TZ).then((d) => !cancelled && setData(d), (e) => !cancelled && setError((e as Error).message));
    load();
    const timer = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [days]);

  const timeline = useMemo(() => (data ? fillTimeline(data) : []), [data]);
  const names = (id: string) => snapshot?.assets[id]?.name ?? id;

  const range = (
    <div className="history-actions">
      <div className="segmented">
        {RANGES.map((r) => (
          <button key={r.days} className={r.days === days ? "active" : ""} onClick={() => setDays(r.days)}>
            {r.label}
          </button>
        ))}
      </div>
      <a className="button small" href={api.exportUrl("alerts", days, TZ)} download>Alarmlar (CSV)</a>
      <a className="button small" href={api.exportUrl("work_orders", days, TZ)} download>İş emirleri (CSV)</a>
    </div>
  );

  if (!data || data.days !== days) {
    return (
      <div className="page">
        <Panel title="Geçmiş ve analiz" actions={range}><Empty>{error ?? "Yükleniyor…"}</Empty></Panel>
      </div>
    );
  }
  const t = data.totals;
  const fmtBucket = (v: number) =>
    data.unit === "hour"
      ? new Date(v).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })
      : new Date(v).toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
  const maxAlerts = Math.max(1, ...data.top_assets.map((a) => a.alerts));
  const maxCause = Math.max(1, ...data.causes.map((c) => c.work_orders));

  return (
    <div className="page">
      <Panel title="Geçmiş ve analiz" actions={range}>
        <div className="kpis">
          <Kpi label="Alarmlar" value={num(t.alerts)} tone={t.critical ? "critical" : undefined}
            hint={`${t.critical} kritik · ${t.warning} uyarı`} />
          <Kpi label="Kaynak" value={`${t.ml} / ${t.rule}`} hint="ML / kural" />
          <Kpi label="Etkilenen varlık" value={num(t.assets_affected)} />
          <Kpi label="İş emirleri" value={num(t.work_orders)} />
          <Kpi label="Medyan alarm süresi" value={duration(t.median_alert_s)} hint="açılıştan kapanışa" />
          <Kpi label="Ort. tepki süresi" value={duration(t.mean_response_s)} hint="ilk alarmdan bakım ekibine" />
        </div>

        <h3>Zaman içinde</h3>
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={timeline} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            <XAxis dataKey="t" tickFormatter={fmtBucket} minTickGap={24} />
            <YAxis allowDecimals={false} />
            <Tooltip labelFormatter={(v) => fmtBucket(Number(v))}
              formatter={(v: number, name: string) => [v, { critical: "Kritik alarm", warning: "Uyarı", work_orders: "İş emri" }[name] ?? name]} />
            <Legend formatter={(name: string) => ({ critical: "Kritik alarm", warning: "Uyarı", work_orders: "İş emri" }[name] ?? name)}
              wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="critical" stackId="a" fill="#f87171" maxBarSize={36} isAnimationActive={false} />
            <Bar dataKey="warning" stackId="a" fill="#fbbf24" maxBarSize={36} isAnimationActive={false} />
            <Line dataKey="work_orders" stroke="#3fb7ff" strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </Panel>

      <div className="grid-2">
        <Panel title="En çok alarm veren varlıklar">
          {data.top_assets.length === 0 ? <Empty>Bu aralıkta alarm yok.</Empty> : (
            <table className="table">
              <thead><tr><th>Varlık</th><th>Alarm</th><th>Kritik</th><th>İş emri</th></tr></thead>
              <tbody>
                {data.top_assets.map((a) => (
                  <tr key={a.asset_id}>
                    <td>
                      <Link to={`/assets/${a.asset_id}`}>{a.name}</Link>
                      <div className="muted small">{a.asset_id}{a.type ? ` · ${TYPE_LABELS[a.type]}` : ""}</div>
                    </td>
                    <td className="bar-cell">
                      <span className="inline-bar" style={{ width: `${(a.alerts / maxAlerts) * 100}%` }} />
                      <b>{a.alerts}</b>
                    </td>
                    <td className={a.critical ? "txt-critical" : "muted"}>{a.critical}</td>
                    <td className={a.work_orders ? "" : "txt-degraded"} title={a.work_orders ? "" : "Bu aralıkta bakım yapılmadı"}>
                      {a.work_orders}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="İş emirleri sebebe göre">
          {data.causes.length === 0 ? <Empty>Bu aralıkta iş emri yok.</Empty> : (
            <ul className="cause-list">
              {data.causes.map((c) => (
                <li key={c.cause ?? "none"}>
                  <span>{c.cause ? metricLabel(c.cause) : "Belirtilmemiş (rutin kontrol)"}</span>
                  <span className="bar-cell">
                    <span className="inline-bar" style={{ width: `${(c.work_orders / maxCause) * 100}%` }} />
                    <b>{c.work_orders}</b>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <AlertTable alerts={data.alerts} names={names} />
      <OrderTable orders={data.work_orders} names={names} />
    </div>
  );
}

function AlertTable({ alerts, names }: { alerts: Alert[]; names: (id: string) => string }) {
  const [shown, setShown] = useState(PAGE);
  return (
    <Panel title={`Alarm geçmişi (${alerts.length}${alerts.length >= 200 ? "+" : ""})`}>
      {alerts.length === 0 ? <Empty>Bu aralıkta alarm yok.</Empty> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Açılış</th><th>Varlık</th><th>Şiddet</th><th>Kaynak</th><th>Mesaj</th><th>Süre</th></tr></thead>
            <tbody>
              {alerts.slice(0, shown).map((a) => (
                <tr key={a.id}>
                  <td className="muted nowrap">{dateTime(a.created_at)}</td>
                  <td className="nowrap"><Link to={`/assets/${a.asset_id}`}>{names(a.asset_id)}</Link></td>
                  <td><span className={`badge ${a.severity}`}>{statusLabel(a.severity)}</span></td>
                  <td><span className="alert-tag">{a.source === "ml" ? "ML" : "KURAL"}</span></td>
                  <td>{a.message.replace(/^[^:]+: /, "")}</td>
                  <td className="muted nowrap" title={a.closed_on_restart ? "Sistem yeniden başlatılırken kapatıldı; süre, sistemin kapandığı ana kadar" : undefined}>
                    {a.resolved_at ? duration((new Date(a.resolved_at).getTime() - new Date(a.created_at).getTime()) / 1000) : "açık"}
                    {a.closed_on_restart && <span className="small"> · yeniden başlatma</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {shown < alerts.length && (
            <button className="link more" onClick={() => setShown(shown + PAGE)}>Daha fazla göster</button>
          )}
        </div>
      )}
    </Panel>
  );
}

function OrderTable({ orders, names }: { orders: WorkOrder[]; names: (id: string) => string }) {
  return (
    <Panel title={`İş emri geçmişi (${orders.length})`}>
      {orders.length === 0 ? <Empty>Bu aralıkta iş emri yok.</Empty> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Zaman</th><th>No</th><th>Varlık</th><th>İşlem</th><th>Sebep</th><th>Gönderimdeki sağlık</th></tr></thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td className="muted nowrap">{dateTime(o.created_at)}</td>
                  <td className="muted">#{o.id}</td>
                  <td className="nowrap"><Link to={`/assets/${o.asset_id}`}>{names(o.asset_id)}</Link></td>
                  <td>{o.action}</td>
                  <td className="muted">{o.cause ? metricLabel(o.cause) : "–"}</td>
                  <td>%{num(o.health)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
