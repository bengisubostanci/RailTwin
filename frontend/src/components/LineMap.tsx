import { useNavigate } from "react-router-dom";
import type { AssetView, Station, Topology } from "../api";
import { num, pct } from "./common";

const TRACK_GAP = 5;
const TRAIN_OFFSET = 15;

function locate(stations: Station[], position: number) {
  let i = stations.findIndex((_, k) => k < stations.length - 1 && position <= stations[k + 1].position_m);
  if (i < 0) i = stations.length - 2;
  const a = stations[i];
  const b = stations[i + 1];
  const frac = Math.min(1, Math.max(0, (position - a.position_m) / (b.position_m - a.position_m)));
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return { x: a.x + dx * frac, y: a.y + dy * frac, nx: -dy / len, ny: dx / len };
}

function statusClass(asset?: AssetView) {
  return asset ? asset.status : "unknown";
}

export default function LineMap({ topology, assets, highlight }: {
  topology: Topology;
  assets: Record<string, AssetView>;
  highlight?: string;
}) {
  const navigate = useNavigate();
  const byId = Object.fromEntries(topology.stations.map((s) => [s.id, s]));
  const open = (id: string) => navigate(`/assets/${id}`);

  return (
    <svg className="linemap" viewBox="20 70 960 300" role="img" aria-label={`${topology.line_name} şeması`}>
      {topology.segments.map((seg) => {
        const a = byId[seg.from_station];
        const b = byId[seg.to_station];
        const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const nx = (-(b.y - a.y) / len) * TRACK_GAP;
        const ny = ((b.x - a.x) / len) * TRACK_GAP;
        const asset = assets[seg.id];
        return (
          <g
            key={seg.id}
            className={`seg ${statusClass(asset)} ${highlight === seg.id ? "hl" : ""}`}
            onClick={() => open(seg.id)}
          >
            <title>{`${seg.id} · ${seg.name}${asset ? ` · sağlık ${pct(asset.health)}` : ""}`}</title>
            <line className="hit" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
            <line x1={a.x + nx} y1={a.y + ny} x2={b.x + nx} y2={b.y + ny} />
            <line x1={a.x - nx} y1={a.y - ny} x2={b.x - nx} y2={b.y - ny} />
          </g>
        );
      })}

      {topology.stations.map((s, i) => {
        const below = i % 2 === 1;
        return (
          <g key={s.id} className="station">
            <circle cx={s.x} cy={s.y} r={7} />
            <text x={s.x} y={below ? s.y + 34 : s.y - 26} textAnchor="middle">{s.name}</text>
          </g>
        );
      })}

      {topology.switches.map((sw) => {
        const st = byId[sw.station_id];
        const asset = assets[sw.id];
        const idx = topology.stations.indexOf(st);
        const y = idx % 2 === 1 ? st.y - 18 : st.y + 18;
        return (
          <g
            key={sw.id}
            className={`switch ${statusClass(asset)} ${highlight === sw.id ? "hl" : ""}`}
            transform={`translate(${st.x} ${y}) rotate(45)`}
            onClick={() => open(sw.id)}
          >
            <title>{`${sw.id} · ${sw.name}${asset ? ` · sağlık ${pct(asset.health)}` : ""}`}</title>
            <rect x={-5} y={-5} width={10} height={10} rx={1.5} />
          </g>
        );
      })}

      {topology.trains.map((t) => {
        const asset = assets[t.id];
        if (asset?.extra?.position_m == null) return null;
        const dir = Number(asset.extra.direction) || 1;
        const p = locate(topology.stations, Number(asset.extra.position_m));
        const off = dir > 0 ? -TRAIN_OFFSET : TRAIN_OFFSET;
        const x = p.x + p.nx * off;
        const y = p.y + p.ny * off;
        return (
          <g
            key={t.id}
            className={`train ${statusClass(asset)} ${highlight === t.id ? "hl" : ""}`}
            style={{ transform: `translate(${x}px, ${y}px)` }}
            onClick={() => open(t.id)}
          >
            <title>{`${t.name} · ${num(asset.metrics.speed_kmh)} km/h · sağlık ${pct(asset.health)}`}</title>
            <rect x={-15} y={-8} width={30} height={16} rx={8} />
            <text y={4} textAnchor="middle">{t.id.replace("T-", "")}</text>
          </g>
        );
      })}
    </svg>
  );
}
