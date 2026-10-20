export type AssetType = "train" | "segment" | "switch";
export type HealthStatus = "healthy" | "degraded" | "critical";
export type Severity = "warning" | "critical";

export interface Station { id: string; name: string; position_m: number; x: number; y: number }
export interface Segment { id: string; name: string; from_station: string; to_station: string; start_m: number; end_m: number }
export interface Switch { id: string; name: string; station_id: string; position_m: number }
export interface TrainSpec { id: string; name: string; start_station: number; direction: number }

export interface Topology {
  line_id: string;
  line_name: string;
  length_m: number;
  time_scale: number;
  stations: Station[];
  segments: Segment[];
  switches: Switch[];
  trains: TrainSpec[];
}

export interface AssetView {
  id: string;
  type: AssetType;
  name: string;
  health: number;
  status: HealthStatus;
  cause: string | null;
  cause_label: string | null;
  recommended_action: string;
  trend_per_min: number;
  time_to_critical_h: number | null;
  z: Record<string, number>;
  metrics: Record<string, number>;
  extra: Record<string, string | number | null>;
  online: boolean;
  /** Hareketli ortalamalar hâlâ geçmiş veri topluyor; varlık henüz puanlanmadı. */
  warming: boolean;
  fault_kinds?: string[];
}

export interface Alert {
  id: number;
  created_at: string;
  resolved_at: string | null;
  acknowledged: boolean;
  asset_id: string;
  key: string;
  severity: Severity;
  source: "rule" | "ml";
  message: string;
}

export interface Kpis {
  trains_total: number;
  trains_running: number;
  fleet_health: number | null;
  network_health: number | null;
  traction_power_mw: number;
  passengers_on_board: number;
  alerts_critical: number;
  alerts_warning: number;
  assets_degraded: number;
  ambient_c: number;
  load_factor: number;
}

export interface Snapshot {
  ts: number;
  model_ready: boolean;
  mqtt_connected: boolean;
  time_scale: number;
  kpis: Kpis;
  assets: Record<string, AssetView>;
  alerts: Alert[];
}

export interface WorkOrder {
  id: number;
  created_at: string;
  completed_at: string | null;
  asset_id: string;
  action: string;
  cause: string | null;
  health: number;
  status: string;
}

export interface ScenarioParams {
  headway_min: number;
  service_hours: number;
  load_factor: number;
  ambient_c: number;
  fleet_size: number;
  max_speed_kmh: number;
  maintenance_strategy: "reactive" | "preventive" | "predictive";
  horizon_days: number;
}

export interface ScenarioSummary {
  trains_required: number;
  trains_available: number;
  effective_headway_min: number;
  cycle_time_min: number;
  trips_per_day: number;
  train_km_per_day: number;
  passengers_per_day: number;
  unserved_per_day: number;
  energy_mwh_per_day: number;
  co2_t_per_day: number;
  failures_mean: number;
  failures_p90: number;
  otp_pct: number;
  cost_total_eur: number;
  cost_per_train_km_eur: number;
  track_maintenance_day: number | null;
  failures_by_component: Record<string, number>;
}

export interface ScenarioResult {
  params: ScenarioParams;
  baseline_params: ScenarioParams;
  baseline: ScenarioSummary;
  scenario: ScenarioSummary;
  daily: {
    day: number;
    baseline_otp: number;
    scenario_otp: number;
    baseline_failures: number;
    scenario_failures: number;
    baseline_wear: number;
    scenario_wear: number;
  }[];
  warnings: string[];
}

export interface ScenarioConfig {
  baseline: ScenarioParams;
  presets: Record<string, { label: string; description: string; params: Partial<ScenarioParams> }>;
  strategies: string[];
}

export type Series = Record<string, { t: string; v: number }[]>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* gövde JSON değildi */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  topology: () => request<Topology>("/api/topology"),
  asset: (id: string) => request<AssetView>(`/api/assets/${id}`),
  telemetry: (id: string, minutes = 15, bucket = 5) =>
    request<Series>(`/api/assets/${id}/telemetry?minutes=${minutes}&bucket_s=${bucket}`),
  injectFault: (id: string, kind: string | null) => post(`/api/assets/${id}/faults`, { kind }),
  dispatch: (id: string) => post<WorkOrder>(`/api/assets/${id}/maintenance`),
  setEnvironment: (body: { ambient_c?: number; load_factor?: number }) => post("/api/environment", body),
  alerts: (active = false) => request<Alert[]>(`/api/alerts?active=${active}&limit=100`),
  ack: (id: number) => post(`/api/alerts/${id}/ack`),
  maintenance: () => request<{ assets: AssetView[]; work_orders: WorkOrder[] }>("/api/maintenance"),
  scenarioConfig: () => request<ScenarioConfig>("/api/scenarios/config"),
  runScenario: (params: ScenarioParams) => post<ScenarioResult>("/api/scenarios/run", params),
};

export const METRIC_META: Record<string, { label: string; unit: string }> = {
  speed_kmh: { label: "Hız", unit: "km/h" },
  traction_power_kw: { label: "Cer gücü", unit: "kW" },
  motor_current_a: { label: "Cer motoru akımı", unit: "A" },
  bearing_temp_c: { label: "Aks rulmanı sıcaklığı", unit: "°C" },
  bogie_vibration_mm_s: { label: "Boji titreşimi", unit: "mm/s" },
  brake_pressure_bar: { label: "Fren borusu basıncı", unit: "bar" },
  passenger_load_pct: { label: "Yolcu doluluğu", unit: "%" },
  ambient_c: { label: "Ortam sıcaklığı", unit: "°C" },
  passing_speed_kmh: { label: "Geçiş hızı", unit: "km/h" },
  vibration_rms: { label: "Ray titreşimi", unit: "g" },
  geometry_dev_mm: { label: "Hat geometrisi sapması", unit: "mm" },
  rail_temp_c: { label: "Ray sıcaklığı", unit: "°C" },
  throw_time_s: { label: "Makas atma süresi", unit: "s" },
  motor_current_peak_a: { label: "Makas motoru tepe akımı", unit: "A" },
  throw_ambient_c: { label: "Son atmadaki sıcaklık", unit: "°C" },
};

export const metricLabel = (m: string) => METRIC_META[m]?.label ?? m;
export const metricUnit = (m: string) => METRIC_META[m]?.unit ?? "";

export const FAULT_LABELS: Record<string, string> = {
  bearing_overheat: "Rulman aşırı ısınması",
  wheel_flat: "Tekerlek düzlüğü",
  brake_leak: "Fren borusu kaçağı",
  motor_degradation: "Cer motoru bozulması",
  rail_defect: "Ray mantarı kusuru",
  geometry_drift: "Hat geometrisi kayması",
  switch_wear: "Makas mekanizması aşınması",
};

export const TYPE_LABELS: Record<AssetType, string> = { train: "Tren", segment: "Ray segmenti", switch: "Makas" };

/** Filtre düğmeleri için çoğul tür adları. */
export const TYPE_LABELS_PLURAL: Record<AssetType, string> = { train: "Trenler", segment: "Ray segmentleri", switch: "Makaslar" };

/** API'den gelen durum değerlerinin (değişmeden kalır) Türkçe görünen etiketleri. */
export const STATUS_LABELS: Record<string, string> = {
  healthy: "sağlıklı",
  degraded: "bozulmuş",
  critical: "kritik",
  warning: "uyarı",
  running: "seferde",
  dwell: "istasyonda",
};
export const statusLabel = (s: string) => STATUS_LABELS[s] ?? s;

/** Makas konumu değerlerinin Türkçe etiketleri. */
export const SWITCH_POSITION_LABELS: Record<string, string> = { normal: "normal", reverse: "ters" };

/** Bakım stratejisi değerlerinin Türkçe etiketleri. */
export const STRATEGY_LABELS: Record<ScenarioParams["maintenance_strategy"], string> = {
  reactive: "Reaktif – arızada onar",
  preventive: "Önleyici – sabit aralıklarla",
  predictive: "Kestirimci – ikiz sağlığına göre",
};
