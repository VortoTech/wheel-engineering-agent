import { AlertTriangle, ArrowDownToLine, Box, CircleDashed, Cpu, Sparkles } from 'lucide-react';
import type { ReconstructionJob, ReconstructionStatus } from './types';
import { Viewer } from './Viewer';

export function VisualReconstruction({ status, job, active, busy, hasImage, onRun }: {
  status: ReconstructionStatus | null; job: ReconstructionJob | null; active: boolean;
  busy: boolean; hasImage: boolean; onRun: () => void;
}) {
  const ready = status?.available === true;
  return <div className="visual-reconstruction">
    <Viewer url={job ? `/api/reconstructions/${job.id}/glb` : null} building={active}
      axisMode="native-y-up" label={job ? 'AI 视觉网格 · 非工程实体' : '视觉重建空间'}/>
    <div className="visual-safety"><AlertTriangle size={17}/><div><strong>视觉参考，不是可加工模型</strong><span>不含可靠尺寸、背面结构与参数特征，不生成 STEP，不参与几何/强度/CAM 检查。</span></div></div>
    <div className="visual-controls">
      <div><span className="section-eyebrow">STABLE FAST 3D</span><p><Cpu size={15}/>{ready ? `${status.device.toUpperCase()} · ${status.model}` : status?.reason ?? '正在检查本机引擎…'}</p></div>
      <button className="visual-run" disabled={busy || active || !ready || !hasImage} onClick={onRun}>
        {active ? <span className="spinner"/> : <Sparkles size={16}/>} {active ? '正在重建…' : job ? '重新生成视觉网格' : '从主参考图生成'}
      </button>
    </div>
    {job?.report && <div className="visual-result"><Box size={16}/><span>{(job.report.artifact.bytes / 1024 / 1024).toFixed(1)} MB · 来源图 {job.snapshot.image_name}</span><a href={`/api/reconstructions/${job.id}/glb`} download><ArrowDownToLine size={14}/>下载 GLB</a></div>}
    {job?.error && <div className="visual-error"><CircleDashed size={16}/>{job.error}</div>}
    {status && <p className="visual-license">{status.license}</p>}
  </div>;
}
