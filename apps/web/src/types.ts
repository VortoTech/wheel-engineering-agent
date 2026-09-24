export const TEMPLATE_VERSION = 'forged-monoblock-v15';
export type Spec = {
  rim_diameter_in: number; rim_width_in: number; offset_et_mm: number; rim_wall_mm: number;
  hub_diameter_mm: number; hub_thickness_mm: number; center_bore_mm: number; bolt_count: number;
  bolt_circle_mm: number; bolt_diameter_mm: number; spoke_count: number;
  spoke_width_hub_mm: number; spoke_width_rim_mm: number; spoke_thickness_mm: number;
  spoke_crown_mm: number; spoke_fillet_mm: number; face_curve: number; sweep_deg: number;
  pocket_depth_mm: number; junction_fillet_mm: number;
  valve_diameter_mm: number; valve_angle_deg: number; valve_tilt_deg: number;
  spoke_style: 'single' | 'paired'; paired_gap_mm: number; paired_tip_width_mm: number;
  paired_blade_root_mm: number;
  paired_window_root_mm: number; paired_window_blend_mm: number;
  paired_root_round_mm: number; paired_gap_flare_mm: number;
  paired_split_start_mm: number; spoke_phase_deg: number;
  paired_shoulder_mm: number; paired_mid_mm: number; paired_tip_inset_mm: number;
  lip_extension_mm: number; lip_drop_mm: number;
  spoke_method: 'loft' | 'window'; window_outlines_mm: [number, number][][]; window_edge_fillet_mm: number;
  window_face_relief_mm: number; window_spoke_ridge_mm: number; window_side_draft_deg: number;
  rim_pocket_count: number; rim_pocket_phase_deg: number; rim_pocket_radial_mm: number;
  rim_pocket_width_mm: number; rim_pocket_depth_mm: number; rim_pocket_inset_mm: number;
  rim_pocket_corner_mm: number;
};
export type NumericSpecKey = Exclude<keyof Spec, 'spoke_style' | 'spoke_method' | 'window_outlines_mm'>;
// Snapshots from older templates keep their own spec shape.
export type AnySpec = Spec | Record<string, number | string>;
export type Source = {
  kind: 'template' | 'manual' | 'drawing' | 'measurement' | 'observed' | 'inferred' | 'unknown';
  confidence?: number | null;
  note: string;
};
export type Sources = Record<keyof Spec, Source>;
export type Caliper = { inner_radius_mm: number; outer_radius_mm: number; z_min_mm: number; z_max_mm: number; required_clearance_mm: number; source: Source };
export type Stock = { outer_diameter_mm: number; height_mm: number; center_z_mm: number; cavity_diameter_mm: number; front_web_mm: number; required_allowance_mm: number; source: Source };
export type Material = { name: string; density_kg_m3: number; source: Source };
export type Preparation = { caliper?: Caliper | null; stock?: Stock | null; material?: Material | null };
export type PreparationReport = {
  caliper: { status: string; minimum_clearance_mm?: number; required_clearance_mm?: number; overlap_mm3?: number | null; input?: Caliper };
  stock: { status: string; stock_volume_mm3?: number; missing_volume_mm3?: number; removal_percent?: number | null; minimum_allowance_mm?: number | null; required_allowance_mm?: number; input?: Stock };
  weight: { status: string; finished_kg?: number; stock_kg?: number | null; removed_kg?: number | null; input?: Material };
};
export type Reference = { id: string; name: string; created_at: string };
export type BuildResolution = {
  status: 'exact' | 'degraded' | 'unverified'; review_required: boolean;
  feature_results: {feature: string; parameter: string; requested: unknown; applied: unknown;
    status: 'exact' | 'adjusted' | 'partial' | 'unverified'; details: Record<string, unknown>}[];
};
export type VolumeMethod = {
  schema_version: string; method: string; requested_epsilon: number;
  IsUseSpan?: boolean;
};
export type VolumeMeasurement = VolumeMethod & {
  volume_mm3: number; reported_relative_error: number; reported_error_limit: number;
};
export type Report = {
  build_status?: BuildResolution['status']; build_resolution?: BuildResolution;
  volume_measurement?: VolumeMeasurement; step_volume_method?: VolumeMethod;
  checks: Record<string, boolean>; solid_count: number; volume_mm3: number;
  bbox_mm: number[]; face_count: number; template_version: string; limitations: string[];
  artifacts: Record<string, { sha256: string; bytes: number }>;
  derived?: Record<string, number>;
  junction_fillet_requested_mm?: number; junction_fillet_applied_mm?: number;
  skeleton?: {window:unknown;explicit_profile?:unknown; stations:{fraction:number;radius_mm:number;blade_width_mm:number;depth_mm:number;back_blade_width_mm:number}[];note:string};
  preparation?: PreparationReport;
  handoff?: { status: string; feature_count: number; operation_count: number };
  presentation?: { status: 'display_only'; decorative_fastener_count: number; note: string } | null;
  rotational_symmetry?: {status:string;groups:number;period_deg:number;master_window_count:number;scope:string};
  forged?: { stages: { op: string; removed_mm3?: number; status?: string; dropped_chips?: number }[];
    removal_ratio: number; part_mass_kg_6061: number; stock_volume_mm3: number; suspect_operations: string[] };
  cam_operations?: { op: string; size_mm: number; angle_deg: number; edges: string; note: string }[];
};
export const FORGED_TEMPLATE = 'forged-blank-v1';
// Mirrors ForgedWheel in services/wheelcam/forged_blank.py; the API validates every key.
export type ForgedRecipe = { family: 'y_split' | 'single' | 'skeleton'; spokes: number; skeleton?: unknown } & Record<string, unknown>;
export type ForgedPreset = { id: string; name: string; recipe: ForgedRecipe };
export type Job = {
  id: string; status: 'queued' | 'running' | 'succeeded' | 'failed';
  snapshot: { photo_analysis?: PhotoAnalysis; name: string; spec: AnySpec; sources: Sources; preparation?: Preparation; draft_revision: number; template_version: string;
    template?: string; forged?: ForgedRecipe };
  report: Report | null; error: string | null; created_at: string; finished_at: string | null;
};
export type ReconstructionReport = {
  provider: string; device: string; model: string; source_image_id: string;
  source_image_sha256: string; artifact: { sha256: string; bytes: number };
  usage: 'visual_reference_only'; limitations: string[];
};
export type ReconstructionJob = {
  id: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; provider: string;
  snapshot: { name: string; draft_revision: number; image_id: string; image_name: string;
    image_sha256: string; provider: string; usage: 'visual_reference_only'; limitations: string[] };
  report: ReconstructionReport | null; error: string | null; created_at: string; finished_at: string | null;
};
export type ReconstructionStatus = {
  provider: string; available: boolean; reason: string | null; device: string; model: string;
  output: string; usage: 'visual_reference_only'; license: string;
};
export type AgentStatus = {
  provider: string | null; configured: boolean; model: string | null;
  supports_primary_image: boolean; mode: 'not_connected' | 'live_provider' | 'mock_provider'; reason?: string;
};
export type AgentCadAction = {
  id: string; operation: 'set_parameter' | 'replace_sketch' | 'mark_unknown' | 'request_measurement' | 'request_tool';
  target: string; value?: unknown; source?: 'observed' | 'inferred'; confidence?: number;
  rationale: string; evidence_refs?: string[]; question?: string;
};
export type AgentCadPlan = {
  schema_version: 'wheel-agent-cad-plan-v1'; base_revision: number; goal: string; actions: AgentCadAction[];
};
export type AgentPlanPreview = {
  schema_version: 'wheel-agent-cad-result-v1'; base_revision: number;
  proposed_spec: Spec; proposed_sources: Sources; pending_approval_action_ids: string[];
  measurement_requests: {action_id:string;target:string;question:string;rationale:string}[];
  tool_requests: {action_id:string;tool:string;rationale:string}[];
  can_apply: boolean; can_build: boolean;
  audit: {action_id:string;operation:string;target:string;requires_approval:boolean;approved:boolean;evidence_refs:string[]}[];
  limitations: string[];
};
export type AgentProposal = { provider: AgentStatus; plan: AgentCadPlan; preview: AgentPlanPreview; tool_trace?: {tool:string;round:number;result:Record<string,unknown>}[] };
export type Project = {
  id: string; name: string; spec: Spec; sources: Sources; revision: number;
  primary_image_id: string | null; images: Reference[]; jobs: Job[]; reconstructions: ReconstructionJob[];
  preparation: Preparation;
  photo_analysis?: PhotoAnalysis | null; applied_analysis_id?: string | null;
  agent_cad_runs?: { id:string; base_revision:number; resulting_revision:number; plan:unknown; result:unknown; created_at:string }[];
};
export type PhotoPose = {cx:number;cy:number;scale_px:number;distance_radii:number;rotation:number[][];radius_mm:number;reference_z_mm:number;angles_deg:number[]};
export type PhotoAnalysis = {
  camera_fit?: {status:string;pose:PhotoPose;before_held_out_px?:number;after_held_out_px?:number;held_out_groups:number[];note:string};
  root_fit?: {status:string;parameters:Record<string,number>;group:number;points:[number,number][];all_points:[number,number][][];note:string;support_groups?:number};
  algorithm?: string;
  traces?: {group:number; side:number; samples:{accepted:boolean; points:[number,number][]}[]}[];
  section_fit?: {status:string; before_rms_ratio?:number; after_rms_ratio?:number; constraint_fraction?:number; stations:{support_groups:number; radius_ratio:number; gap_ratio:number; width_ratio:number}[]};
  window_fit?: {held_out_iou:number[]; held_out_iou_mean:number|null; label_loo_mean:number; fit_groups:number[];
    held_out_groups:number[]; window_error:string|null; window_count:number; contour_method?:string; sector_consensus?: {
      medoid_group:number; fit_groups:number[]; held_out_groups:number[]; inlier_groups:number[]; outlier_groups:number[];
      phase_offsets_deg:number[]; quality_iou:number[]; quality_threshold:number; method:string;
    }};
  id: string; image_id: string; base_revision: number; status: string; can_apply: boolean;
  image_size: [number, number]; ellipse: {cx:number; cy:number; rx:number; ry:number; angle_deg?:number};
  edge_coverage: number; edge_residual_px: number; outer_points: [number,number][];
  stations: {points:[number,number][]}[]; suggested_parameters: Record<string, number | string | unknown[]>;
  scale: {reference_outer_mm:number|null; target_outer_mm:number; reference_gap_mm:number|null; basis:string};
  warnings: string[];
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
        : typeof body.detail === 'string' ? body.detail
        : typeof body.detail?.message === 'string' ? body.detail.message : message;
    } catch { /* HTTP status remains useful when response is not JSON. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
