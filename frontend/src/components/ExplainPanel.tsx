import { useMemo } from "react";
import { Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { metricLabel, type Explanation, type ExplanationResponse } from "../api";
import { Empty, Panel, fmtTime, num, pct } from "./common";

const OTHER_COLORS = ["#3fb7ff", "#a78bfa", "#94a3b8", "#5eead4"];
const ms = (iso: string | null | undefined) => (iso ? new Date(iso).getTime() : null);

/** Saniyeyi "45 sn" veya "2 dk 10 sn" biçiminde yazar. */
function duration(s: number): string {
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return m ? (r ? `${m} dk ${r} sn` : `${m} dk`) : `${r} sn`;
}

const lower = (s: string) => s.toLocaleLowerCase("tr-TR");
const sigma = (z: number) => `${z >= 0 ? "+" : ""}${num(z, 1)}σ`;

type Row = { t: number } & Record<string, number>;

export default function ExplainPanel({ data, minutes }: { data: ExplanationResponse | null; minutes: number }) {
  const e = data?.explanation ?? null;
  const metrics = useMemo(() => Object.keys(data?.z ?? {}), [data]);

  const rows = useMemo<Row[]>(() => {
    const byTime = new Map<number, Row>();
    for (const [metric, points] of Object.entries(data?.z ?? {})) {
      for (const p of points) {
        const t = new Date(p.t).getTime();
        const row = byTime.get(t) ?? ({ t } as Row);
        row[metric] = p.v;
        byTime.set(t, row);
      }
    }
    return [...byTime.values()].sort((a, b) => a.t - b.t);
  }, [data]);

  const color = (metric: string, i: number) => (e?.cause === metric ? "#f87171" : OTHER_COLORS[i % OTHER_COLORS.length]);
  const threshold = e?.z_onset ?? 3;

  return (
    <Panel title="Sağlık neden düştü?" className="explain-panel">
      {!data ? (
        <Empty>Geçmiş yükleniyor…</Empty>
      ) : rows.length === 0 ? (
        <Empty>Henüz kayıtlı geçmiş yok. Model her 5 saniyede bir kayıt alıyor; birkaç saniye içinde dolacak.</Empty>
      ) : (
        <div className="explain">
          <div className="explain-text">
            {e ? <Story e={e} /> : (
              <p className="muted">
                Son {minutes} dakikada sağlık düşüşü yok. İzlenen tüm sensörler, o anki koşullarda beklenen aralıkta.
              </p>
            )}
          </div>
          <div className="explain-chart">
            <div className="explain-chart-title">Sensör sapması <span className="sigma">(kötü yönde, σ)</span></div>
            <ResponsiveContainer width="100%" height={230}>
              <LineChart data={rows} margin={{ top: 8, right: 12, left: -14, bottom: 0 }}>
                <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={fmtTime} minTickGap={50} />
                <YAxis domain={[(min: number) => Math.max(-4, Math.min(-1, Math.floor(min))), (max: number) => Math.max(threshold + 2, Math.ceil(max))]}
                  allowDataOverflow width={44} tickFormatter={(v: number) => v.toLocaleString("tr-TR")} />
                <ReferenceLine y={threshold} stroke="#7d8b99" strokeDasharray="4 4"
                  label={{ value: `eşik ${threshold}σ`, position: "insideTopLeft", fill: "#7d8b99", fontSize: 11 }} />
                {/* Recharts yalnızca doğrudan ReferenceLine çocuklarını çizer; bu yüzden bileşen değil fonksiyon. */}
                {e?.onset && marker(e.onset.t, "#3fb7ff", "sapma başladı")}
                {e?.degraded_at && marker(e.degraded_at, "#fbbf24", "%75")}
                {e?.critical_at && marker(e.critical_at, "#f87171", "%45", "insideBottomRight")}
                {e?.recovered_at && marker(e.recovered_at, "#34d399", "toparlandı")}
                <Tooltip labelFormatter={(t) => fmtTime(Number(t))}
                  formatter={(v: number, name: string) => [sigma(v), metricLabel(name)]} />
                <Legend formatter={(name: string) => metricLabel(name)} wrapperStyle={{ fontSize: 12 }} />
                {metrics.map((m, i) => (
                  <Line key={m} dataKey={m} dot={false} stroke={color(m, i)} strokeWidth={e?.cause === m ? 2.2 : 1.4}
                    isAnimationActive={false} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </Panel>
  );
}

type LabelPosition = "insideTopRight" | "insideBottomRight";

function marker(t: string, color: string, label: string, position: LabelPosition = "insideTopRight") {
  return (
    <ReferenceLine key={label} x={ms(t)!} stroke={color} strokeDasharray="2 3"
      label={{ value: label, position, fill: color, fontSize: 11 }} />
  );
}

function Story({ e }: { e: Explanation }) {
  const onsetMs = ms(e.onset?.t);
  const after = (iso: string | null) => {
    const t = ms(iso);
    return t != null && onsetMs != null && t > onsetMs ? ` · sapmadan ${duration((t - onsetMs) / 1000)} sonra` : "";
  };
  const list = (metrics: string[]) =>
    metrics.map((m) => `${lower(metricLabel(m))} (${sigma(e.peak_z[m])})`).join(", ");

  return (
    <>
      <ol className="explain-timeline">
        {e.onset && (
          <li className="onset">
            <b>Sapma başladı</b> <span>{fmtTime(ms(e.onset.t)!)}</span>
            <div>{metricLabel(e.onset.metric)}, beklenen değerden {e.z_onset}σ'dan fazla ayrıldı.</div>
          </li>
        )}
        {e.degraded_at && (
          <li className="degraded">
            <b>Bozulmuş eşiği (%75) aşıldı</b> <span>{fmtTime(ms(e.degraded_at)!)}{after(e.degraded_at)}</span>
          </li>
        )}
        {e.critical_at && (
          <li className="critical">
            <b>Kritik eşik (%45) aşıldı</b> <span>{fmtTime(ms(e.critical_at)!)}{after(e.critical_at)}</span>
          </li>
        )}
        <li className="min">
          <b>En düşük sağlık: {pct(e.min_health)}</b> <span>{fmtTime(ms(e.min_at)!)}</span>
        </li>
        {e.recovered_at ? (
          <li className="recovered">
            <b>Toparlandı</b> <span>{fmtTime(ms(e.recovered_at)!)}</span>
          </li>
        ) : e.ongoing ? (
          <li className="ongoing"><b>Devam ediyor</b> <span>sağlık hâlâ %75'in altında</span></li>
        ) : null}
      </ol>

      {e.cause ? (
        <p>
          <b>Olası sebep: {lower(metricLabel(e.cause))}</b> (en yüksek sapma {sigma(e.peak_z[e.cause])}).{" "}
          {e.others_normal && "Diğer sensörler normal aralıkta kaldı; sorun tek bir bileşende görünüyor."}
        </p>
      ) : null}
      {e.cause && e.secondary.length > 0 && (
        <p>
          Ana sapmanın ardından şu sensörlerde de daha küçük bir yükseliş görüldü: {list(e.secondary)}. Büyük
          olasılıkla aynı arızanın yan etkisi.
        </p>
      )}
      {e.cause && e.independent.length > 0 && (
        <p className="txt-degraded">
          Ana sapmadan bağımsız görünen başka sapmalar da var: {list(e.independent)}. Birden fazla sorun olabilir.
        </p>
      )}
      {!e.cause && (
        <p>Hiçbir sensör tek başına {e.z_onset}σ eşiğini aşmadı; düşüşü sensörlerin birlikte alışılmadık davranışı tetikledi.</p>
      )}
      {e.lead_s != null && e.lead_s > 0 && (
        <p className="explain-lead">
          Model sapmayı, sağlık sarı bölgeye inmeden <b>{duration(e.lead_s)}</b> önce gördü.
        </p>
      )}
      {e.start_truncated && <p className="muted">Düşüş, gösterilen zaman penceresinden önce başlamış.</p>}
    </>
  );
}
