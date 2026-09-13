import { useEffect, useState } from 'react';
import type { Job, PhotoAnalysis, Project } from './types';

const names: Record<string, string> = { spoke_count: '双辐组数', spoke_phase_deg: '整体角度 °', paired_gap_mm: '间隙 mm', paired_tip_width_mm: '端宽 mm' };

export function PhotoPanel({ project, shown, busy, onAnalyze, onApply }: {
  project: Project; shown: Job | null; busy: boolean;
  onAnalyze: (outer?: number) => void; onApply: (analysis: PhotoAnalysis) => void;
}) {
  const a = project.photo_analysis;
  const [outer, setOuter] = useState('');
  const [opacity, setOpacity] = useState(.7);
  const [points, setPoints] = useState(true);
  const [dx, setDx] = useState(0), [dy, setDy] = useState(0), [scale, setScale] = useState(1), [rotation, setRotation] = useState(0);
  useEffect(() => { setDx(0); setDy(0); setScale(1); setRotation(0); setOuter(''); }, [project.id, project.primary_image_id]);
  const [w, h] = a?.image_size ?? [720, 540];
  const imageURL = project.primary_image_id ? `/api/images/${project.primary_image_id}` : null;
  const front = shown?.report?.artifacts['front.svg'] ? `/api/builds/${shown.id}/front` : null;
  const applied = a && project.applied_analysis_id === a.id && project.revision === a.base_revision + 1;
  const outdated = a && !applied && a.base_revision !== project.revision;
  return <div className="photo-panel">
    <div className="photo-actions">
      <button className="secondary-button" disabled={busy || !imageURL} onClick={() => onAnalyze(outer.trim() ? Number(outer) : undefined)}>{busy ? '处理中…' : '自动识图 · 提取候选'}</button>
      <label>参考实物外径 <input aria-label="参考实物外径" type="number" min={100} max={1200} placeholder="未知可留空" value={outer} disabled={busy} onChange={e => setOuter(e.target.value)}/> mm</label>
    </div>
    <p className="photo-help">只填已知的最外缘直径，不填标称英寸。无实测标尺时仅拟合比例，ET、PCD 和背面结构保持原值。</p>
    <div className="photo-canvas">
      {imageURL ? a ? <svg viewBox={`0 0 ${w} ${h}`} aria-label="原图、识图点位与 CAD 正面投影对照">
        <image href={imageURL} width={w} height={h}/>
        {front && <g opacity={opacity} transform={`translate(${a.ellipse.cx+dx} ${a.ellipse.cy+dy}) rotate(${rotation}) scale(${scale})`}>
          <image href={front} x={-a.ellipse.rx} y={-a.ellipse.ry} width={a.ellipse.rx*2} height={a.ellipse.ry*2} preserveAspectRatio="none"/>
        </g>}
        {points && <g fill="none" stroke="#55e6dd" strokeWidth={1.2}>
          <ellipse cx={a.ellipse.cx} cy={a.ellipse.cy} rx={a.ellipse.rx} ry={a.ellipse.ry}/>
          <path d={`M${a.ellipse.cx-8},${a.ellipse.cy}h16 M${a.ellipse.cx},${a.ellipse.cy-8}v16`}/>
          {a.outer_points.map(([x,y], i) => <circle key={`o${i}`} cx={x} cy={y} r={1.6}/>)}
          {a.stations.flatMap((s, j) => s.points.map(([x,y], i) => <circle key={`${j}-${i}`} cx={x} cy={y} r={2} stroke={j === 2 ? '#ff98b6' : '#99adff'}/>))}
        </g>}
      </svg> : <img src={imageURL} alt="当前主参考图，等待提取候选"/> : <p>先添加并选择一张主参考图。</p>}
    </div>
    {a && <>
      <div className="photo-legend"><span>青色：外圈候选</span><span>紫 / 粉：中段 / 外端点位</span><span>金色：{front ? `CAD ${shown!.id.slice(0,6)}` : '此版本没有正面投影，生成新版本后可叠加'}</span></div>
      <div className="photo-adjust">
        <label><input type="checkbox" checked={points} onChange={e => setPoints(e.target.checked)}/>显示检测点位</label>
        {([['投影透明度', opacity, setOpacity, 0, 1, .05], ['水平微调', dx, setDx, -60, 60, 1], ['垂直微调', dy, setDy, -60, 60, 1], ['投影缩放', scale, setScale, .8, 1.2, .005], ['投影旋转', rotation, setRotation, -20, 20, .5]] as const).map(([label,value,set,min,max,step]) => <label key={label}>{label}<input aria-label={label} type="range" value={value} min={min} max={max} step={step} onChange={e => set(Number(e.target.value))}/><span>{value.toFixed(2)}</span></label>)}
        <button onClick={() => { setDx(0); setDy(0); setScale(1); setRotation(0); }}>重置投影对齐</button>
      </div>
      <div className="photo-candidates">
        <strong>{a.status === 'candidates' ? '可核对的双辐候选' : '检测存在歧义，请人工核对'}</strong>
        <p>外圈边缘覆盖 {(a.edge_coverage*100).toFixed(0)}% · 拟合中位残差 {a.edge_residual_px} px（检测图分辨率，非实物精度）</p>
        <div className="photo-values">{Object.entries(a.suggested_parameters).map(([key,value]) => <span key={key}>{names[key] || key} <b>{value}</b></span>)}</div>
        <p>{a.scale.basis}。目标模型外径 {a.scale.target_outer_mm.toFixed(1)} mm。</p>
        {a.scale.reference_outer_mm && <p>用户填写的参考外径 {a.scale.reference_outer_mm} mm → 外端间隙候选约 {a.scale.reference_gap_mm?.toFixed(1)} mm，尚未核验。</p>}
        <button className="secondary-button" disabled={busy || !a.can_apply || !!applied || !!outdated} onClick={() => onApply(a)}>{applied ? '候选已应用到草稿' : outdated ? '草稿已变更，请重新识图' : '确认点位后，将候选应用到草稿'}</button>
        <p>应用后仍需生成新版本；当前金色投影始终来自已生成的 CAD。对齐微调仅改变视图，不改变尺寸。</p>
        {a.warnings.map((warning,i) => <p className="photo-help" key={i}>{warning}</p>)}
      </div>
    </>}
  </div>;
}
