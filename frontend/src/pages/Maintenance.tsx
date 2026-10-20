import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, TYPE_LABELS, metricLabel, type Alert, type WorkOrder } from "../api";
import { AlertRow, Empty, HealthBadge, HealthBar, Panel, num, pct, timeAgo } from "../components/common";
import { useLive } from "../live";

export default function Maintenance() {
  const { snapshot } = useLive();
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [alertLog, setAlertLog] = useState<Alert[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.maintenance().then((m) => setOrders(m.work_orders), () => undefined);
    api.alerts(false).then(setAlertLog, () => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  if (!snapshot) return <Empty>Bağlanıyor…</Empty>;
  const ranked = Object.values(snapshot.assets).sort((a, b) => a.health - b.health);
  const needsWork = ranked.filter((a) => a.status !== "healthy");

  const dispatch = async (id: string) => {
    setBusy(id);
    try {
      await api.dispatch(id);
      setError(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page">
      <Panel
        title="Kestirimci bakım kuyruğu"
        actions={<span className="muted small">{needsWork.length} varlık ilgi bekliyor</span>}
      >
        <p className="muted small">
          Sağlığa göre sıralanmıştır. "Kritiğe kalan süre", son sağlık eğilimini simüle edilen hattın işletme
          saatleri cinsinden ileriye taşır.
        </p>
        {error && <p className="error">{error}</p>}
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Varlık</th>
                <th>Sağlık</th>
                <th>Olası sebep</th>
                <th>Kritiğe kalan süre</th>
                <th>Önerilen işlem</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {ranked.slice(0, Math.max(8, needsWork.length)).map((a) => (
                <tr key={a.id}>
                  <td>
                    <Link to={`/assets/${a.id}`}><b>{a.id}</b></Link>
                    <div className="muted small">{TYPE_LABELS[a.type]}</div>
                  </td>
                  <td className="health-cell">
                    <HealthBadge health={a.health} status={a.status} />
                    <HealthBar health={a.health} />
                  </td>
                  <td>{a.cause_label ?? <span className="muted">–</span>}</td>
                  <td>
                    {a.status === "critical"
                      ? <span className="txt-critical">Şimdi</span>
                      : a.time_to_critical_h != null
                        ? `~${num(a.time_to_critical_h, 1)} sa`
                        : <span className="muted">kararlı</span>}
                  </td>
                  <td>{a.status === "healthy" ? <span className="muted">Rutin</span> : a.recommended_action}</td>
                  <td>
                    {a.status !== "healthy" && (
                      <button className="primary small" disabled={busy === a.id} onClick={() => dispatch(a.id)}>
                        Gönder
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid-2">
        <Panel title="İş emirleri">
          {orders.length === 0 ? (
            <Empty>Henüz iş emri yok. Yukarıdaki kuyruktan bakım ekibi gönderin.</Empty>
          ) : (
            <ul className="orders">
              {orders.map((o) => (
                <li key={o.id}>
                  <div>
                    <b>#{o.id} · {o.asset_id}</b> — {o.action}
                    <div className="muted small">
                      Gönderimdeki sağlık {pct(o.health)}{o.cause ? ` · sebep: ${metricLabel(o.cause)}` : ""}
                    </div>
                  </div>
                  <span className="muted small">{timeAgo(o.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Alarm kaydı">
          {alertLog.length === 0 ? (
            <Empty>Henüz kayıtlı alarm yok.</Empty>
          ) : (
            <ul className="alerts">
              {alertLog.slice(0, 30).map((a) => <AlertRow key={a.id} alert={a} />)}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
