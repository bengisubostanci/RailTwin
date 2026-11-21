import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, FAULT_LABELS, TYPE_LABELS, metricLabel, type BenchmarkResult, type FaultTrial, type Performance } from "../api";
import { Empty, Kpi, Panel, num, pct, timeAgo } from "../components/common";

const lower = (s: string) => s.toLocaleLowerCase("tr-TR");
const rate = (v: number | null | undefined) => (v == null ? "–" : pct(v * 100));
/** Gerçek saniyeyi ve simüle hattaki karşılığını birlikte yazar. */
const detectTime = (s: number | null, scale: number) =>
  s == null ? "–" : `${num(s)} sn · hatta ${num((s * scale) / 60, 1)} dk`;

export default function Model() {
  const [perf, setPerf] = useState<Performance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(() => api.performance().then(setPerf, (e) => setError((e as Error).message)), []);
  const running = perf?.benchmark.status === "running";

  useEffect(() => {
    load();
    const timer = setInterval(load, running ? 2000 : 10000);
    return () => clearInterval(timer);
  }, [load, running]);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      await api.runBenchmark();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  if (!perf) return <Empty>{error ?? "Yükleniyor…"}</Empty>;
  const b = perf.benchmark;
  const result = b.result;

  const action = running ? (
    <div className="bench-progress">
      <div className="hbar"><div className="healthy" style={{ width: `${Math.max(2, b.progress * 100)}%` }} /></div>
      <span className="muted">{pct(b.progress * 100)}</span>
    </div>
  ) : (
    <button className={result ? "small" : "small primary"} onClick={start} disabled={starting}>
      {result ? "Yeniden çalıştır" : "Değerlendirmeyi çalıştır"}
    </button>
  );

  return (
    <div className="page">
      <Panel title="Model performansı · kıyaslama" actions={action}>
        {error && <p className="txt-critical">{error}</p>}
        {b.status === "failed" && <p className="txt-critical">Değerlendirme başarısız oldu: {b.error}</p>}
        {result ? (
          <Benchmark r={result} />
        ) : (
          <Empty>
            {running
              ? "Değerlendirme çalışıyor. Model, simüle hatlarda her arıza türüne karşı sınanıyor (yaklaşık 2–3 dakika)."
              : "Henüz değerlendirme yapılmadı. Model, her arıza türü birkaç farklı simüle hatta enjekte edilerek ve " +
                "arızasız hatlarda sert hava değişimleri altında sınanır. Sonuç kaydedilir; model değişmedikçe yeniden " +
                "çalıştırmaya gerek yoktur."}
          </Empty>
        )}
      </Panel>

      <LiveRecord perf={perf} scale={result?.time_scale ?? 5} />
    </div>
  );
}

function Benchmark({ r }: { r: BenchmarkResult }) {
  const fa = r.false_alarms;
  return (
    <>
      <div className="kpis">
        <Kpi label="Tespit oranı" value={rate(r.overall.detection_rate)}
          tone={r.overall.detection_rate >= 0.95 ? "healthy" : "degraded"} hint={`${r.overall.trials} arıza denemesi`} />
        <Kpi label="Doğru sebep" value={rate(r.overall.cause_accuracy)}
          tone={(r.overall.cause_accuracy ?? 0) >= 0.9 ? "healthy" : "degraded"} hint="tespit edilenler içinde" />
        <Kpi label="Ort. alarm süresi" value={num(r.overall.mean_detect_s)} unit="sn"
          hint={`hat zamanında ${num(((r.overall.mean_detect_s ?? 0) * r.time_scale) / 60, 1)} dk`} />
        <Kpi label="Yanlış alarm" value={num(fa.per_1000_asset_hours, 1)}
          tone={fa.events === 0 ? "healthy" : "degraded"} hint={`1000 varlık-saatte · ${fa.events} olay / ${num(fa.asset_hours)} saat`} />
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Arıza</th>
              <th>Varlık</th>
              <th>Tespit</th>
              <th>Ort. alarm süresi</th>
              <th>En uzun</th>
              <th>Doğru sebep</th>
              <th>Beklenen sensör</th>
            </tr>
          </thead>
          <tbody>
            {r.faults.map((f) => (
              <tr key={f.kind}>
                <td><b>{FAULT_LABELS[f.kind] ?? f.kind}</b></td>
                <td className="muted">{TYPE_LABELS[f.asset_type]}</td>
                <td className={f.detected === f.trials ? "txt-healthy" : "txt-degraded"}>{f.detected}/{f.trials}</td>
                <td>{detectTime(f.mean_detect_s, r.time_scale)}</td>
                <td className="muted">{f.max_detect_s == null ? "–" : `${num(f.max_detect_s)} sn`}</td>
                <td className={f.cause_correct === f.detected ? "txt-healthy" : "txt-degraded"}>
                  {f.cause_correct}/{f.detected}
                </td>
                <td className="muted">{metricLabel(f.expected_cause)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="muted bench-note">
        {r.seeds.length} farklı simüle hat, model sürümü {r.model_version}, {timeAgo(r.created_at)} çalıştırıldı
        ({num(r.duration_s)} sn sürdü). Alarm süresi, arızanın enjekte edildiği andan sağlığın %65'in altına inip ML
        alarmının açıldığı ana kadar ölçülür; hat ×{r.time_scale} hızla aktığı için hat zamanı da gösterilir.
        Yanlış alarm, arıza enjekte edilmemiş varlıklarda açılan alarmlardır; arızasız hatlar{" "}
        {fa.conditions.map((c) => `${num(c.ambient_c)} °C / %${num(c.load_factor * 100)} talep`).join(", ")} koşulları
        arasında sert geçişlerle sınanır.
      </p>
    </>
  );
}

function LiveRecord({ perf, scale }: { perf: Performance; scale: number }) {
  const { summary, trials } = perf.live;
  return (
    <Panel title="Canlı kayıt" actions={
      summary.detected > 0 ? (
        <span className="muted">
          {summary.detected} tespit · ort. {num(summary.mean_detect_s)} sn · doğru sebep {rate(summary.cause_accuracy)}
          {summary.missed > 0 && ` · ${summary.missed} kaçırıldı`}
        </span>
      ) : undefined
    }>
      {trials.length === 0 ? (
        <Empty>
          Arayüzden veya demodan enjekte edilen arızalar burada listelenir: modelin her birini ne kadar sürede ve hangi
          sebeple yakaladığı. Simülatörün kendiliğinden başlattığı arızalar gizli tutulduğu için burada yer almaz.
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Zaman</th>
                <th>Varlık</th>
                <th>Arıza</th>
                <th>Alarm süresi</th>
                <th>Bulunan sebep</th>
                <th>Durum</th>
              </tr>
            </thead>
            <tbody>
              {trials.map((t) => <TrialRow key={t.id} t={t} scale={scale} />)}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function TrialRow({ t, scale }: { t: FaultTrial; scale: number }) {
  const correct = t.detected_cause != null && t.detected_cause === t.expected_cause;
  const state = t.detected_at
    ? t.repaired_at ? { cls: "txt-healthy", text: "Yakalandı, onarıldı" } : { cls: "txt-degraded", text: "Yakalandı, onarım bekliyor" }
    : t.repaired_at ? { cls: "txt-critical", text: "Yakalanmadan onarıldı" } : { cls: "muted", text: "İzleniyor" };
  return (
    <tr>
      <td className="muted">{timeAgo(t.injected_at)}</td>
      <td><Link to={`/assets/${t.asset_id}`}>{t.asset_id}</Link></td>
      <td>{FAULT_LABELS[t.kind] ?? t.kind}</td>
      <td>{detectTime(t.detect_s, scale)}</td>
      <td>
        {t.detected_cause ? (
          <span className={correct ? "txt-healthy" : "txt-critical"}>
            {correct ? "✓" : "✕"} {lower(metricLabel(t.detected_cause))}
          </span>
        ) : "–"}
      </td>
      <td className={state.cls}>{state.text}</td>
    </tr>
  );
}
