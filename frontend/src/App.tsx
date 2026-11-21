import { useEffect } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { demoStepNumber } from "./components/DemoPanel";
import { useLive } from "./live";
import Overview from "./pages/Overview";
import Assets from "./pages/Assets";
import AssetDetail from "./pages/AssetDetail";
import Maintenance from "./pages/Maintenance";
import Model from "./pages/Model";
import Scenarios from "./pages/Scenarios";

const NAV = [
  { to: "/", label: "Genel Bakış", end: true },
  { to: "/assets", label: "Varlıklar" },
  { to: "/maintenance", label: "Bakım" },
  { to: "/scenarios", label: "Senaryolar" },
  { to: "/model", label: "Model" },
];

export default function App() {
  const { snapshot, connected, topology } = useLive();
  const { pathname } = useLocation();
  useEffect(() => window.scrollTo(0, 0), [pathname]);
  const status = !connected
    ? { cls: "critical", text: "Bağlantı yok" }
    : !snapshot?.mqtt_connected
      ? { cls: "degraded", text: "Saha verisi bekleniyor" }
      : !snapshot.model_ready
        ? { cls: "degraded", text: "Modeller eğitiliyor" }
        : { cls: "healthy", text: "Canlı" };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden>
            <rect width="32" height="32" rx="7" fill="#132232" />
            <path d="M6 22 L13 12 L19 17 L26 8" stroke="#3fb7ff" strokeWidth="3" fill="none" strokeLinecap="round" />
          </svg>
          <div>
            <strong>RailTwin</strong>
            <span>{topology?.line_name ?? "Dijital ikiz kontrol merkezi"}</span>
          </div>
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="conn">
          {snapshot?.demo?.state === "running" && pathname !== "/" && (
            <Link to="/" className="demo-pill" title="Demoyu Genel Bakış'ta izle">
              <span className="dot pulse" /> Demo · adım {demoStepNumber(snapshot.demo)}/{snapshot.demo.steps.length}
            </Link>
          )}
          {snapshot && snapshot.time_scale > 1 && <span className="muted">sim ×{snapshot.time_scale}</span>}
          <span className={`dot ${status.cls}`} />
          {status.text}
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/assets" element={<Assets />} />
          <Route path="/assets/:id" element={<AssetDetail />} />
          <Route path="/maintenance" element={<Maintenance />} />
          <Route path="/scenarios" element={<Scenarios />} />
          <Route path="/model" element={<Model />} />
        </Routes>
      </main>
    </div>
  );
}
