import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type AssetType, type AssetView } from "../api";
import { AlertRow, Empty, Kpi, Panel, num, pct } from "../components/common";
import LineMap from "../components/LineMap";
import { useLive } from "../live";

const GROUPS: { type: AssetType; label: string }[] = [
  { type: "train", label: "Filo" },
  { type: "segment", label: "Hat" },
  { type: "switch", label: "Makaslar" },
];

export default function Overview() {
  const { snapshot, topology } = useLive();
  const navigate = useNavigate();

  if (!snapshot || !topology) {
    return <Empty>İkize bağlanılıyor… backend ve simülatörün çalıştığından emin olun.</Empty>;
  }
  const k = snapshot.kpis;
  const assets = Object.values(snapshot.assets);
  const alertCount = k.alerts_critical + k.alerts_warning;

  return (
    <div className="page">
      <div className="kpis">
        <Kpi label="Seferdeki trenler" value={`${k.trains_running}/${k.trains_total}`} />
        <Kpi label="Filo sağlığı" value={num(k.fleet_health)} unit="%" tone={tone(k.fleet_health)} />
        <Kpi label="Hat sağlığı" value={num(k.network_health)} unit="%" tone={tone(k.network_health)} />
        <Kpi label="Cer gücü" value={num(k.traction_power_mw, 1)} unit="MW" />
        <Kpi label="Trendeki yolcu" value={num(k.passengers_on_board)} />
        <Kpi
          label="Aktif alarmlar"
          value={alertCount}
          tone={k.alerts_critical ? "critical" : k.alerts_warning ? "degraded" : undefined}
          hint={alertCount ? `${k.alerts_critical} kritik · ${k.alerts_warning} uyarı` : "Her şey yolunda"}
        />
      </div>

      <div className="grid-main">
        <Panel title={topology.line_name} actions={<Legend />} className="map-panel">
          <LineMap topology={topology} assets={snapshot.assets} />
        </Panel>

        <Panel title="Aktif alarmlar" className="alerts-panel">
          {snapshot.alerts.length === 0 ? (
            <Empty>Aktif alarm yok. Bir sensör sınırı aştığında veya model bir anomali gördüğünde ikiz alarm üretir.</Empty>
          ) : (
            <ul className="alerts">
              {snapshot.alerts.map((a) => (
                <AlertRow key={a.id} alert={a} onAck={(id) => api.ack(id)} onOpen={(id) => navigate(`/assets/${id}`)} />
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="grid-bottom">
        <Panel title="Varlık sağlığı">
          {GROUPS.map((g) => (
            <div key={g.type} className="tiles-group">
              <h3>{g.label}</h3>
              <div className="tiles">
                {assets
                  .filter((a) => a.type === g.type)
                  .sort((a, b) => a.id.localeCompare(b.id))
                  .map((a) => <AssetTile key={a.id} asset={a} onClick={() => navigate(`/assets/${a.id}`)} />)}
              </div>
            </div>
          ))}
        </Panel>
        <FieldConditions ambient={k.ambient_c} load={k.load_factor} />
      </div>
    </div>
  );
}

function tone(v: number | null) {
  if (v == null) return undefined;
  return v >= 75 ? "healthy" : v >= 45 ? "degraded" : "critical";
}

function AssetTile({ asset, onClick }: { asset: AssetView; onClick: () => void }) {
  return (
    <button className={`tile ${asset.warming ? "warming" : asset.status}`} onClick={onClick}
      title={asset.warming ? "Puanlamadan önce geçmiş veri toplanıyor" : asset.cause_label ?? asset.name}>
      <span className="tile-id">{asset.id}</span>
      <span className="tile-health">{asset.warming ? "ısınıyor" : pct(asset.health)}</span>
    </button>
  );
}

function Legend() {
  return (
    <div className="legend">
      <span><i className="healthy" />Sağlıklı</span>
      <span><i className="degraded" />Bozulmuş</span>
      <span><i className="critical" />Kritik</span>
    </div>
  );
}

function FieldConditions({ ambient, load }: { ambient: number; load: number }) {
  const [temp, setTemp] = useState(ambient);
  const [demand, setDemand] = useState(load);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setTemp(ambient), [ambient]);
  useEffect(() => setDemand(load), [load]);

  const apply = (body: { ambient_c?: number; load_factor?: number }) =>
    api.setEnvironment(body).then(() => setError(null), (e: Error) => setError(e.message));

  return (
    <Panel title="Saha koşulları">
      <p className="muted small">
        Sahadaki hava durumunu ve yolcu talebini değiştirin. Sıcak hava ve dolu trenler rulman sıcaklıklarını ve
        güç tüketimini artırır; model bunu hesaba kattığı için yanlış alarm üretmemelidir.
      </p>
      <label className="slider">
        <span>Ortam sıcaklığı <b>{num(temp)} °C</b></span>
        <input type="range" min={-15} max={45} step={1} value={temp}
          onChange={(e) => setTemp(+e.target.value)} onPointerUp={() => apply({ ambient_c: temp })}
          onKeyUp={() => apply({ ambient_c: temp })} />
      </label>
      <label className="slider">
        <span>Yolcu talebi <b>{pct(Math.round(demand * 100))}</b></span>
        <input type="range" min={0.1} max={1.2} step={0.05} value={demand}
          onChange={(e) => setDemand(+e.target.value)} onPointerUp={() => apply({ load_factor: demand })}
          onKeyUp={() => apply({ load_factor: demand })} />
      </label>
      {error && <p className="error">{error}</p>}
    </Panel>
  );
}
