import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { TYPE_LABELS, TYPE_LABELS_PLURAL, metricLabel, metricUnit, type AssetType, type AssetView } from "../api";
import { Empty, HealthBadge, HealthBar, Panel, num } from "../components/common";
import { useLive } from "../live";

const KEY_METRICS: Record<AssetType, string[]> = {
  train: ["speed_kmh", "bearing_temp_c", "bogie_vibration_mm_s", "brake_pressure_bar"],
  segment: ["passing_speed_kmh", "vibration_rms", "geometry_dev_mm", "rail_temp_c"],
  switch: ["throw_time_s", "motor_current_peak_a"],
};

export default function Assets() {
  const { snapshot } = useLive();
  const [filter, setFilter] = useState<AssetType | "all">("all");
  const navigate = useNavigate();

  if (!snapshot) return <Empty>Bağlanıyor…</Empty>;
  const assets = Object.values(snapshot.assets)
    .filter((a) => filter === "all" || a.type === filter)
    .sort((a, b) => a.id.localeCompare(b.id));

  return (
    <div className="page">
      <Panel
        title="Varlıklar"
        actions={
          <div className="segmented">
            {(["all", "train", "segment", "switch"] as const).map((f) => (
              <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
                {f === "all" ? "Tümü" : TYPE_LABELS_PLURAL[f]}
              </button>
            ))}
          </div>
        }
      >
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Varlık</th>
                <th>Tür</th>
                <th>Sağlık</th>
                <th>Canlı ölçümler</th>
                <th>Olası sebep</th>
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => (
                <tr key={a.id} onClick={() => navigate(`/assets/${a.id}`)} className="clickable">
                  <td>
                    <b>{a.id}</b>
                    <div className="muted small">{a.name}</div>
                  </td>
                  <td>{TYPE_LABELS[a.type]}</td>
                  <td className="health-cell">
                    <HealthBadge health={a.health} status={a.status} />
                    <HealthBar health={a.health} />
                  </td>
                  <td><Readings asset={a} /></td>
                  <td>{a.cause_label ?? <span className="muted">–</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function Readings({ asset }: { asset: AssetView }) {
  return (
    <div className="readings">
      {KEY_METRICS[asset.type].map((m) => (
        <span key={m} className={asset.cause === m ? "flag" : ""}>
          <small>{metricLabel(m)}</small>
          {num(asset.metrics[m], 1)} {metricUnit(m)}
        </span>
      ))}
    </div>
  );
}
