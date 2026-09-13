export const TEMPLATE_VERSION = 'forged-monoblock-v3';
export type Spec = {
  rim_diameter_in: number; rim_width_in: number; offset_et_mm: number; rim_wall_mm: number;
  hub_diameter_mm: number; hub_thickness_mm: number; center_bore_mm: number; bolt_count: number;
  bolt_circle_mm: number; bolt_diameter_mm: number; spoke_count: number;
  spoke_width_hub_mm: number; spoke_width_rim_mm: number; spoke_thickness_mm: number;
  spoke_crown_mm: number; spoke_fillet_mm: number; face_curve: number; sweep_deg: number;
  pocket_depth_mm: number; junction_fillet_mm: number;
  valve_diameter_mm: number; valve_angle_deg: number; valve_tilt_deg: number;
};
// Snapshots from older templates keep their own spec shape.
export type AnySpec = Spec | Record<string, number>;
export type Source = { kind: 'template' | 'manual' | 'drawing' | 'measurement'; note: string };
export type Sources = Record<keyof Spec, Source>;
export type Caliper = { inner_radius_mm: number; outer_radius_mm: number; z_min_mm: number; z_max_mm: number; required_clearance_mm: number; source: Source };
export type Stock = { outer_diameter_mm: number; height_mm: number; center_z_mm: number; cavity_diameter_mm: number; front_web_mm: number; required_allowance_mm: number; source: Source };
export type Material = { name: string; density_kg_m3: number; source: Source };
export type Preparation = { caliper?: Caliper | null; stock?: Stock | null; material?: Material | null };
export type PreparationReport = {
  caliper: { status: string; minimum_clearance_mm?: number; required_clearance_mm?: number; overlap_mm3?: number; input?: Caliper };
  stock: { status: string; stock_volume_mm3?: number; missing_volume_mm3?: number; removal_percent?: number | null; minimum_allowance_mm?: number | null; required_allowance_mm?: number; input?: Stock };
  weight: { status: string; finished_kg?: number; stock_kg?: number | null; removed_kg?: number | null; input?: Material };
};
export type Reference = { id: string; name: string; created_at: string };
export type Report = {
  checks: Record<string, boolean>; solid_count: number; volume_mm3: number;
  bbox_mm: number[]; face_count: number; template_version: string; limitations: string[];
  artifacts: Record<string, { sha256: string; bytes: number }>;
  derived?: Record<string, number>;
  junction_fillet_requested_mm?: number; junction_fillet_applied_mm?: number;
  preparation?: PreparationReport;
  handoff?: { status: string; feature_count: number; operation_count: number };
};
export type Job = {
  id: string; status: 'queued' | 'running' | 'succeeded' | 'failed';
  snapshot: { name: string; spec: AnySpec; sources: Sources; preparation?: Preparation; draft_revision: number; template_version: string };
  report: Report | null; error: string | null; created_at: string; finished_at: string | null;
};
export type Project = {
  id: string; name: string; spec: Spec; sources: Sources; revision: number;
  primary_image_id: string | null; images: Reference[]; jobs: Job[];
  preparation: Preparation;
};
export type Summary = Pick<Project, 'id' | 'name' | 'revision'>;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      message = Array.isArray(body.detail)
        ? body.detail.map((d: {msg: string}) => d.msg.replace('Value error, ', '')).join('；')
        : body.detail || message;
    } catch { /* HTTP status remains useful when response is not JSON. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
