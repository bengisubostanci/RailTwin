import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type DemoState, type DemoStep } from "../api";
import { Panel } from "./common";

/** Saniyeyi "1:05" biçiminde yazar. */
export function clock(s: number | null | undefined): string {
  if (s == null) return "";
  const t = Math.max(0, Math.floor(s));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

/** Çalışan demonun kaçıncı adımda olduğu (1'den başlar). */
export function demoStepNumber(demo: DemoState): number {
  const active = demo.steps.findIndex((s) => s.status === "active");
  if (active >= 0) return active + 1;
  return Math.min(demo.steps.length, demo.steps.filter((s) => s.status === "done").length + 1);
}

export default function DemoPanel({ demo: live }: { demo: DemoState | undefined }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  // API yanıtını, canlı görüntü onu yakalayana kadar (~1 sn) göster; böylece buton hemen tepki verir.
  const [pending, setPending] = useState<DemoState | null>(null);
  const demo = pending ?? live;

  const state = demo?.state ?? "idle";
  const running = state === "running";
  useEffect(() => {
    if (running) setDismissed(false);
  }, [running]);
  useEffect(() => {
    if (!pending) return;
    if (live?.state === pending.state) {
      setPending(null);
      return;
    }
    const t = setTimeout(() => setPending(null), 3000);
    return () => clearTimeout(t);
  }, [live?.state, pending]);

  const act = (fn: () => Promise<DemoState>) => async () => {
    setBusy(true);
    setError(null);
    try {
      setPending(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const start = act(api.startDemo);
  const stop = act(api.stopDemo);

  if (!demo || state === "idle" || dismissed) {
    return (
      <Panel className="demo-panel demo-idle">
        <div className="demo-intro">
          <div>
            <h2>Canlı demo</h2>
            <p>
              Sağlıklı bir trene rulman arızası enjekte edilir. Modelin arızayı fark etmesi, kritik alarm, bakım ekibinin
              gönderilmesi ve trenin toparlanması adım adım gösterilir. Yaklaşık 2 dakika sürer.
            </p>
            {error && <p className="txt-critical">{error}</p>}
          </div>
          <button className="primary" onClick={start} disabled={busy}>
            {busy ? "Başlatılıyor…" : "Demoyu başlat"}
          </button>
        </div>
      </Panel>
    );
  }

  const title = (
    <>
      Canlı demo ·{" "}
      {demo.asset_id ? <Link to={`/assets/${demo.asset_id}`}>{demo.asset_name ?? demo.asset_id}</Link> : null}
    </>
  );
  const actions = (
    <div className="demo-actions">
      <span className="muted">{clock(demo.elapsed_s)}</span>
      {running ? (
        <button className="small" onClick={stop} disabled={busy}>Durdur</button>
      ) : (
        <>
          <button className="small primary" onClick={start} disabled={busy}>Tekrar oynat</button>
          <button className="small" onClick={() => setDismissed(true)}>Kapat</button>
        </>
      )}
    </div>
  );

  return (
    <Panel title={title} actions={actions} className={`demo-panel demo-${state}`}>
      <ol className="demo-steps">
        {demo.steps.map((s, i) => (
          <Step key={s.key} step={s} n={i + 1} />
        ))}
      </ol>
      {demo.message && !running && (
        <p className={`demo-message ${state === "done" ? "txt-healthy" : "txt-critical"}`}>{demo.message}</p>
      )}
      {error && <p className="txt-critical">{error}</p>}
    </Panel>
  );
}

function Step({ step, n }: { step: DemoStep; n: number }) {
  const icon = { done: "✓", failed: "✕", active: "", pending: String(n) }[step.status];
  return (
    <li className={`demo-step ${step.status}`}>
      <span className="demo-icon" aria-hidden>{icon}</span>
      <div>
        <div className="demo-step-title">
          {step.title}
          {step.at_s != null && step.status !== "pending" && <span className="muted"> · {clock(step.at_s)}</span>}
        </div>
        {step.detail && step.status !== "pending" && <div className="demo-step-detail">{step.detail}</div>}
      </div>
    </li>
  );
}
