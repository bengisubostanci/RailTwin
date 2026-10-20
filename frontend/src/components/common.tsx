import type { ReactNode } from "react";
import type { Alert, HealthStatus } from "../api";

/** Sayıyı Türkçe biçimde (ondalık virgül, binlik nokta) sabit basamakla yazar. */
export function num(v: number | null | undefined, digits = 0, fallback = "–"): string {
  if (v == null || !Number.isFinite(v)) return fallback;
  return v.toLocaleString("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Yüzdeyi Türkçe yazımla ("%75") döndürür. */
export function pct(v: number | null | undefined, digits = 0): string {
  return v == null || !Number.isFinite(v) ? "–" : `%${num(v, digits)}`;
}

/** Saat:dakika:saniye biçiminde yerel (tr-TR) saat. */
export const fmtTime = (t: number) =>
  new Date(t).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function statusOf(health: number): HealthStatus {
  return health >= 75 ? "healthy" : health >= 45 ? "degraded" : "critical";
}

export function HealthBadge({ health, status }: { health: number; status?: HealthStatus }) {
  const s = status ?? statusOf(health);
  return <span className={`badge ${s}`}>{pct(health)}</span>;
}

export function HealthBar({ health }: { health: number }) {
  return (
    <div className="hbar" title={pct(health, 1)}>
      <div className={statusOf(health)} style={{ width: `${Math.max(2, health)}%` }} />
    </div>
  );
}

export function Kpi({ label, value, unit, tone, hint }: {
  label: string; value: ReactNode; unit?: string; tone?: string; hint?: string;
}) {
  // Türkçede yüzde işareti sayıdan önce gelir ("%75").
  const prefix = unit === "%";
  return (
    <div className={`kpi ${tone ?? ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">
        {prefix && <small>{unit}</small>}
        {value}
        {unit && !prefix && <small>{unit}</small>}
      </span>
      {hint && <span className="kpi-hint">{hint}</span>}
    </div>
  );
}

export function Panel({ title, actions, children, className }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`panel ${className ?? ""}`}>
      {(title || actions) && (
        <header>
          <h2>{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)} sn önce`;
  if (s < 3600) return `${Math.floor(s / 60)} dk önce`;
  if (s < 86400) return `${Math.floor(s / 3600)} sa önce`;
  return new Date(iso).toLocaleDateString("tr-TR");
}

export function AlertRow({ alert, onAck, onOpen }: {
  alert: Alert; onAck?: (id: number) => void; onOpen?: (assetId: string) => void;
}) {
  return (
    <li className={`alert ${alert.severity} ${alert.acknowledged ? "acked" : ""} ${alert.resolved_at ? "resolved" : ""}`}>
      <div className="alert-main" onClick={() => onOpen?.(alert.asset_id)} role={onOpen ? "button" : undefined}>
        <span className="alert-tag">{alert.source === "ml" ? "ML" : "KURAL"}</span>
        <span className="alert-msg">{alert.message}</span>
      </div>
      <div className="alert-meta">
        <span>{timeAgo(alert.created_at)}</span>
        {alert.resolved_at ? (
          <span className="muted">çözüldü</span>
        ) : onAck && !alert.acknowledged ? (
          <button className="link" onClick={() => onAck(alert.id)}>Onayla</button>
        ) : alert.acknowledged ? (
          <span className="muted">onaylandı</span>
        ) : null}
      </div>
    </li>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
