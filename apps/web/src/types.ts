export const TEMPLATE_VERSION = 'forged-monoblock-v2';
export type Spec = {
  rim_diameter_in: number; rim_width_in: number; offset_et_mm: number; rim_wall_mm: number;
  hub_diameter_mm: number; hub_thickness_mm: number; center_bore_mm: number; bolt_count: number;
  bolt_circle_mm: number; bolt_diameter_mm: number; spoke_count: number;
  spoke_width_hub_mm: number; spoke_width_rim_mm: number; spoke_thickness_mm: number;
  spoke_crown_mm: number; spoke_fillet_mm: number; face_curve: number; sweep_deg: number;
  pocket_depth_mm: number; junction_fillet_mm: number;
};
// Snapshots from older templates keep their own spec shape.
export type AnySpec = Spec | Record<string, number>;
export type Source = { kind: 'template' | 'manual' | 'drawing' | 'measurement'; note: string };
export type Sources = Record<keyof Spec, Source>;
export type Reference = { id: string; name: string; created_at: string };
export type Report = {
  checks: Record<string, boolean>; solid_count: number; volume_mm3: number;
  bbox_mm: number[]; face_count: number; template_version: string; limitations: string[];
  artifacts: Record<string, { sha256: string; bytes: number }>;
  derived?: Record<string, number>;
  junction_fillet_requested_mm?: number; junction_fillet_applied_mm?: number;
};
export type Job = {
  id: string; status: 'queued' | 'running' | 'succeeded' | 'failed';
  snapshot: { name: string; spec: AnySpec; sources: Sources; draft_revision: number; template_version: string };
  report: Report | null; error: string | null; created_at: string; finished_at: string | null;
};
export type Project = {
  id: string; name: string; spec: Spec; sources: Sources; revision: number;
  primary_image_id: string | null; images: Reference[]; jobs: Job[];
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
