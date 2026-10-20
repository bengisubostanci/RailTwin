import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, STRATEGY_LABELS, type ScenarioConfig, type ScenarioParams, type ScenarioResult, type ScenarioSummary } from "../api";
import { Empty, Panel, num, pct } from "../components/common";

const BASE_COLOR = "#7d8b99";
const SCEN_COLOR = "#3fb7ff";
const BASE_NAME = "Mevcut durum";
const SCEN_NAME = "Senaryo";
const dayLabel = (d: unknown) => `${d}. gün`;

type NumericKey = Exclude<keyof ScenarioParams, "maintenance_strategy">;

const FIELDS: { key: NumericKey; label: string; min: number; max: number; step: number; unit: string }[] = [
  { key: "headway_min", label: "Sefer aralığı", min: 2, max: 15, step: 0.5, unit: "dk" },
  { key: "service_hours", label: "Hizmet saati", min: 8, max: 24, step: 1, unit: "sa/gün" },
  { key: "load_factor", label: "Yolcu talebi", min: 0.2, max: 1.4, step: 0.05, unit: "× kapasite" },
  { key: "ambient_c", label: "Ortam sıcaklığı", min: -15, max: 45, step: 1, unit: "°C" },
  { key: "fleet_size", label: "Filo büyüklüğü", min: 4, max: 24, step: 1, unit: "tren" },
  { key: "max_speed_kmh", label: "Hat hızı", min: 60, max: 120, step: 5, unit: "km/h" },
  { key: "horizon_days", label: "Süre (ufuk)", min: 7, max: 90, step: 1, unit: "gün" },
];

const COMPARE: { key: keyof ScenarioSummary; label: string; unit?: string; better: "up" | "down" | null; fmt?: (v: number) => string }[] = [
  { key: "effective_headway_min", label: "Efektif sefer aralığı", unit: "dk", better: "down" },
  { key: "trains_required", label: "Gereken / mevcut tren", better: null },
  { key: "passengers_per_day", label: "Günlük yolcu", better: "up", fmt: (v) => num(v) },
  { key: "unserved_per_day", label: "Perona kalan yolcu / gün", better: "down", fmt: (v) => num(v) },
  { key: "otp_pct", label: "Dakiklik oranı", unit: "%", better: "up" },
  { key: "failures_mean", label: "Seferde arıza (ortalama)", better: "down" },
  { key: "energy_mwh_per_day", label: "Enerji", unit: "MWh/gün", better: "down" },
  { key: "co2_t_per_day", label: "CO₂", unit: "t/gün", better: "down" },
  { key: "cost_total_eur", label: "İşletme maliyeti", unit: "€", better: "down", fmt: (v) => num(v) },
  { key: "cost_per_train_km_eur", label: "Tren-km başına maliyet", unit: "€", better: "down" },
];

const COMPONENT_LABELS: Record<string, string> = {
  axle_bearing: "Aks rulmanları",
  brakes: "Frenler",
  traction_motor: "Cer motorları",
  wheelset: "Tekerlek takımları",
  doors: "Kapılar",
};

export default function Scenarios() {
  const [config, setConfig] = useState<ScenarioConfig | null>(null);
  const [params, setParams] = useState<ScenarioParams | null>(null);
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    api.scenarioConfig().then((c) => {
      setConfig(c);
      setParams(c.baseline);
    }, (e: Error) => setError(e.message));
  }, []);

  const run = async (p: ScenarioParams) => {
    setRunning(true);
    try {
      setResult(await api.runScenario(p));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  if (!config || !params) return <Empty>{error ?? "Senaryo modeli yükleniyor…"}</Empty>;

  const applyPreset = (name: string) => {
    const next = { ...config.baseline, horizon_days: params.horizon_days, ...config.presets[name].params };
    setParams(next);
    run(next);
  };

  return (
    <div className="page grid-scenario">
      <Panel title="Senaryo analizi (Ya olursa?)">
        <p className="muted small">
          Hattın işletilme biçimini değiştirin ve önümüzdeki haftalardaki etkisini bugünkü işletmeyle (6 dakikalık
          sefer aralığı, 12 tren, reaktif bakım) karşılaştırarak görün. Arızalar Monte Carlo çekilişleriyle hesaplanır.
        </p>
        <div className="presets">
          {Object.entries(config.presets).map(([name, p]) => (
            <button key={name} onClick={() => applyPreset(name)} title={p.description}>{p.label}</button>
          ))}
        </div>
        {FIELDS.map((f) => (
          <label key={f.key} className="slider">
            <span>
              {f.label}
              <b>
                {f.key === "load_factor" ? pct(Math.round(params[f.key] * 100)) : num(params[f.key], f.step < 1 ? 1 : 0)} {f.key !== "load_factor" && f.unit}
              </b>
            </span>
            <input type="range" min={f.min} max={f.max} step={f.step} value={params[f.key]}
              onChange={(e) => setParams({ ...params, [f.key]: +e.target.value })} />
          </label>
        ))}
        <label className="field">
          <span>Bakım stratejisi</span>
          <select value={params.maintenance_strategy}
            onChange={(e) => setParams({ ...params, maintenance_strategy: e.target.value as ScenarioParams["maintenance_strategy"] })}>
            {(Object.keys(STRATEGY_LABELS) as ScenarioParams["maintenance_strategy"][]).map((k) => (
              <option key={k} value={k}>{STRATEGY_LABELS[k]}</option>
            ))}
          </select>
        </label>
        <div className="actions row">
          <button className="primary" disabled={running} onClick={() => run(params)}>
            {running ? "Çalışıyor…" : "Senaryoyu çalıştır"}
          </button>
          <button onClick={() => { setParams(config.baseline); setResult(null); }}>Sıfırla</button>
        </div>
        {error && <p className="error">{error}</p>}
      </Panel>

      <div className="scenario-results">
        {!result ? (
          <Panel><Empty>Bir hazır ayar seçin veya parametreleri ayarlayın, ardından senaryoyu çalıştırın.</Empty></Panel>
        ) : (
          <Results result={result} />
        )}
      </div>
    </div>
  );
}

function Results({ result }: { result: ScenarioResult }) {
  const { baseline: b, scenario: s } = result;
  const components = Object.keys(s.failures_by_component).map((k) => ({
    name: COMPONENT_LABELS[k] ?? k,
    baseline: b.failures_by_component[k],
    scenario: s.failures_by_component[k],
  }));

  return (
    <>
      {result.warnings.length > 0 && (
        <div className="warnings">
          {result.warnings.map((w) => <p key={w}>⚠ {w}</p>)}
        </div>
      )}
      <Panel title="Mevcut durum ve senaryo">
        <div className="table-wrap">
          <table className="table compare">
            <thead>
              <tr><th>Metrik</th><th>{BASE_NAME}</th><th>{SCEN_NAME}</th><th>Değişim</th></tr>
            </thead>
            <tbody>
              {COMPARE.map((c) => {
                const bv = b[c.key] as number;
                const sv = s[c.key] as number;
                const fmt = c.fmt ?? ((v: number) => num(v, Number.isInteger(v) ? 0 : 2));
                if (c.key === "trains_required") {
                  return (
                    <tr key={c.key}>
                      <td>{c.label}</td>
                      <td>{b.trains_required} / {b.trains_available}</td>
                      <td className={s.trains_required > s.trains_available ? "txt-critical" : ""}>
                        {s.trains_required} / {s.trains_available}
                      </td>
                      <td />
                    </tr>
                  );
                }
                const delta = bv === 0 ? (sv === 0 ? 0 : Infinity) : ((sv - bv) / Math.abs(bv)) * 100;
                const good = c.better === null || delta === 0 ? "" : (delta > 0) === (c.better === "up") ? "good" : "bad";
                return (
                  <tr key={c.key}>
                    <td>{c.label}{c.unit && <span className="muted"> ({c.unit})</span>}</td>
                    <td>{fmt(bv)}</td>
                    <td><b>{fmt(sv)}</b></td>
                    <td className={`delta ${good}`}>
                      {Math.abs(delta) < 0.05 ? "–" : !isFinite(delta) ? "yeni" : `${delta > 0 ? "+" : "-"}%${num(Math.abs(delta), 1)}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid-2">
        <Panel title="Dakiklik oranı (%)">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={result.daily} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" />
              <YAxis domain={[(min: number) => Math.max(0, Math.floor(min / 5) * 5 - 5), 100]} allowDataOverflow />
              <Tooltip labelFormatter={dayLabel} formatter={(v: number) => num(v, 1)} />
              <Legend />
              <Line dataKey="baseline_otp" name={BASE_NAME} stroke={BASE_COLOR} dot={false} />
              <Line dataKey="scenario_otp" name={SCEN_NAME} stroke={SCEN_COLOR} dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>
        <Panel title="Kümülatif seferde arıza">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={result.daily} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" />
              <YAxis />
              <Tooltip labelFormatter={dayLabel} formatter={(v: number) => num(v, 1)} />
              <Legend />
              <Line dataKey="baseline_failures" name={BASE_NAME} stroke={BASE_COLOR} dot={false} />
              <Line dataKey="scenario_failures" name={SCEN_NAME} stroke={SCEN_COLOR} dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>
        <Panel title="Hat aşınma endeksi (100'de buraj)">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={result.daily} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" />
              <YAxis />
              <Tooltip labelFormatter={dayLabel} formatter={(v: number) => num(v, 1)} />
              <Legend />
              <ReferenceLine y={100} stroke="#f87171" strokeDasharray="4 4" />
              <Line dataKey="baseline_wear" name={BASE_NAME} stroke={BASE_COLOR} dot={false} />
              <Line dataKey="scenario_wear" name={SCEN_NAME} stroke={SCEN_COLOR} dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>
        <Panel title="Bileşene göre beklenen arıza">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={components} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" interval={0} tick={{ fontSize: 11 }} />
              <YAxis />
              <Tooltip formatter={(v: number) => num(v, 2)} />
              <Legend />
              <Bar dataKey="baseline" name={BASE_NAME} fill={BASE_COLOR} />
              <Bar dataKey="scenario" name={SCEN_NAME} fill={SCEN_COLOR} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>
      </div>
    </>
  );
}
