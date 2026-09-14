import { useEffect, useRef, useState } from 'react';
import type { PointerEvent } from 'react';
import { ContourReview } from './ContourReview';
import { PhotoProjection } from './PhotoProjection';
import type { Job, PhotoAnalysis, Project } from './types';

const names: Record<string, string> = { spoke_method: '轮辐构造', window_outlines_mm: '窗口轮廓', hub_diameter_mm: '中心盘直径 mm', spoke_count: '组数', spoke_phase_deg: '整体角度 °', paired_gap_mm: '间隙 mm', paired_tip_width_mm: '端宽 mm', paired_shoulder_mm: '辐根展开 mm', paired_mid_mm: '中段展开 mm', paired_gap_flare_mm: '分叉展开 mm', paired_root_round_mm: '底部圆角 mm', paired_split_start_mm: '分叉起点 mm' };

export function PhotoPanel({ project, shown, busy, onAnalyze, onApply, onRefineRoot, onWindowFit }: {
  project: Project; shown: Job | null; busy: boolean; onWindowFit: () => void;
  onRefineRoot: (analysis:PhotoAnalysis, group:number, points:[number,number][]) => void;
  onAnalyze: (outer?: number) => void; onApply: (analysis: PhotoAnalysis) => void;
}) {
  const a = project.photo_analysis;
  const [outer, setOuter] = useState('');
  const [zoom, setZoom] = useState(1);
  const [showCAD, setShowCAD] = useState(true);
  const [opacity, setOpacity] = useState(.45);
  const [meshProjection, setMeshProjection] = useState(true);
  const [editRoot, setEditRoot] = useState(false);
  const [rootGroup, setRootGroup] = useState(0);
  const [rootPoints, setRootPoints] = useState<[number,number][]>([]);
  const [rootDirty, setRootDirty] = useState(false);
  const dragging = useRef<number|null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  useEffect(() => {
    setRootGroup(a?.root_fit?.group ?? 0); setRootPoints(a?.root_fit?.points ?? []);
    setRootDirty(false); dragging.current=null;
  },[a?.id]);
  const drag = (event:PointerEvent<SVGSVGElement>) => {
    if (dragging.current === null || !svgRef.current) return;
    const matrix = svgRef.current.getScreenCTM(); if(!matrix)return;
    const p = new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix.inverse());
    const index = dragging.current;
    setRootPoints(current => current.map((point,i) => i===index ? [Math.max(0,Math.min(w,p.x)),Math.max(0,Math.min(h,p.y))] : point));
    setRootDirty(true);
  };
  const [points, setPoints] = useState(true);
  const [dx, setDx] = useState(0), [dy, setDy] = useState(0), [scale, setScale] = useState(1), [rotation, setRotation] = useState(0);
  useEffect(() => { setDx(0); setDy(0); setScale(1); setRotation(0); setOuter(''); setZoom(1); }, [project.id, project.primary_image_id]);
  const [w, h] = a?.image_size ?? [720, 540];
  const imageURL = project.primary_image_id ? `/api/images/${project.primary_image_id}` : null;
  const front = shown?.report?.artifacts['front.svg'] ? `/api/builds/${shown.id}/front` : null;
  const applied = a && project.applied_analysis_id === a.id && project.revision === a.base_revision + 1;
  const outdated = a && !applied && a.base_revision !== project.revision;
  const fit = a?.section_fit;
  const radius = a ? (a.ellipse.rx+a.ellipse.ry)/2 : 1;
  const viewBox = a && zoom > 1 ? `${a.ellipse.cx-w/(2*zoom)} ${a.ellipse.cy-h/(2*zoom)} ${w/zoom} ${h/zoom}` : `0 0 ${w} ${h}`;
  return <div className="photo-panel">
    <div className="photo-actions">
      <button className="secondary-button" disabled={busy || !imageURL} onClick={() => onAnalyze(outer.trim() ? Number(outer) : undefined)}>{busy ? '处理中…' : '自动识图 · 提取候选'}</button>
      <button className="secondary-button" disabled={busy || !imageURL} title="读取标注工具为这张图保存的外圈与窗口标注" onClick={onWindowFit}>窗口标注 · 拟合窗口</button>
      <label>参考实物外径 <input aria-label="参考实物外径" type="number" min={100} max={1200} placeholder="未知可留空" value={outer} disabled={busy} onChange={e => setOuter(e.target.value)}/> mm</label>
    </div>
    <p className="photo-help">只填已知的最外缘直径，不填标称英寸。无实测标尺时仅拟合比例，ET、PCD 和背面结构保持原值。</p>
    <div className="photo-canvas">
      {imageURL ? a ? <svg ref={svgRef} viewBox={viewBox} onPointerMove={drag} onPointerUp={() => {dragging.current=null;}} onPointerCancel={() => {dragging.current=null;}} aria-label="原图、识图点位与 CAD 正面投影对照">
        <image href={imageURL} width={w} height={h}/>
        {front && showCAD && <g opacity={opacity} transform={`translate(${a.ellipse.cx+dx} ${a.ellipse.cy+dy}) rotate(${rotation}) scale(${scale})`}>
          {meshProjection && a.camera_fit && shown ? <foreignObject x={-a.ellipse.cx} y={-a.ellipse.cy} width={w} height={h} pointerEvents="none">
            <PhotoProjection url={`/api/builds/${shown.id}/glb`} pose={a.camera_fit.pose} width={w} height={h}/>
          </foreignObject> : <image href={front} x={-a.ellipse.rx} y={-a.ellipse.ry} width={a.ellipse.rx*2} height={a.ellipse.ry*2} preserveAspectRatio="none"/>}
        </g>}
        {points && <g fill="none" stroke="#55e6dd" strokeWidth={1.2}>
          <ellipse cx={a.ellipse.cx} cy={a.ellipse.cy} rx={a.ellipse.rx} ry={a.ellipse.ry} transform={a.ellipse.angle_deg ? `rotate(${a.ellipse.angle_deg} ${a.ellipse.cx} ${a.ellipse.cy})` : undefined}/>
          <path d={`M${a.ellipse.cx-8},${a.ellipse.cy}h16 M${a.ellipse.cx},${a.ellipse.cy-8}v16`}/>
          {a.outer_points.map(([x,y], i) => <circle key={`o${i}`} cx={x} cy={y} r={1.6}/>)}
          {!a.traces?.length && a.stations.flatMap((s, j) => s.points.map(([x,y], i) => <circle key={`${j}-${i}`} cx={x} cy={y} r={2} stroke={j === 2 ? '#ff98b6' : '#99adff'}/>))}
          {a.traces?.flatMap((trace, j) => [0, 1].map(edge => {
            let connected = false;
            const d = trace.samples.map(sample => {
              if (!sample.accepted) { connected = false; return ''; }
              const [x,y] = sample.points[edge];
              const command = `${connected ? 'L' : 'M'}${x},${y}`;
              connected = true; return command;
            }).join(' ');
            return <path key={`trace-${j}-${edge}`} d={d} stroke="#ff98b6" strokeWidth={.8}/>;
          }))}
        </g>}
        {editRoot && rootPoints.map(([x,y],i) => <g key={`root-${i}`}>
          <circle cx={x} cy={y} r={4} fill="#ff784f" stroke="white" strokeWidth={1} style={{cursor:busy||outdated?'default':'grab',touchAction:'none'}}
            onPointerDown={event => {if(busy||outdated)return; dragging.current=i; svgRef.current?.setPointerCapture(event.pointerId); event.preventDefault();}}/>
          <text x={x+6} y={y-6} fill="white" fontSize={8} pointerEvents="none">{i+1}</text>
        </g>)}
      </svg> : <img src={imageURL} alt="当前主参考图，等待提取候选"/> : <p>先添加并选择一张主参考图。</p>}
    </div>
    <ContourReview key={`${project.id}-${shown?.id}-${project.primary_image_id}-${project.revision}`} project={project} shown={shown}/>
    {a && <>
      <div className="photo-legend"><span>青色：外圈候选</span><span>粉色：逐条辐边（弱证据处留空）</span><span>金色{meshProjection && a.camera_fit ? '实体投影' : '正面线条'}：{front ? `CAD ${shown!.id.slice(0,6)}` : '此版本没有正面投影，生成新版本后可叠加'}</span></div>
      <div className="photo-adjust">
        {a.camera_fit && <label><input type="checkbox" checked={meshProjection} onChange={e=>setMeshProjection(e.target.checked)}/>按照片视角投影实体（关闭看正面线条）</label>}
        <label><input type="checkbox" checked={showCAD} onChange={e => setShowCAD(e.target.checked)}/>显示 CAD 投影</label>
        <label>对照放大<input aria-label="对照放大" type="range" min={1} max={3} step={.25} value={zoom} onChange={e => setZoom(Number(e.target.value))}/><span>{zoom.toFixed(2)}×</span></label>
        <label><input type="checkbox" checked={points} onChange={e => setPoints(e.target.checked)}/>显示检测点位</label>
        {([['投影透明度', opacity, setOpacity, 0, 1, .05], ['水平微调', dx, setDx, -60, 60, 1], ['垂直微调', dy, setDy, -60, 60, 1], ['投影缩放', scale, setScale, .8, 1.2, .005], ['投影旋转', rotation, setRotation, -20, 20, .5]] as const).map(([label,value,set,min,max,step]) => <label key={label}>{label}<input aria-label={label} type="range" value={value} min={min} max={max} step={step} onChange={e => set(Number(e.target.value))}/><span>{value.toFixed(2)}</span></label>)}
        <button onClick={() => { setDx(0); setDy(0); setScale(1); setRotation(0); }}>重置投影对齐</button>
      </div>
      {a.camera_fit && <div className="photo-camera-result">
        <strong>{a.camera_fit.status==='fitted' ? '照片观察角度已拟合' : '沿用正面对齐，相机证据不足'}</strong>
        {a.camera_fit.before_held_out_px !== undefined && <p>未参与拟合的辐条组：轴线偏差 {a.camera_fit.before_held_out_px.toFixed(1)} → {a.camera_fit.after_held_out_px?.toFixed(1)} px。验证组 {a.camera_fit.held_out_groups.map(g=>g+1).join('、')}。</p>}
        <p className="photo-help">{a.camera_fit.note}。该指标衡量轴线对齐，不代表完整轮廓精度。</p>
      </div>}
      {a.root_fit && <div className="photo-root-editor">
        <strong>分叉底部与转接点</strong>
        <p className="photo-help">{a.root_fit.note}</p>
        <label><input type="checkbox" checked={editRoot} onChange={e=>{setEditRoot(e.target.checked);if(e.target.checked)setZoom(2.5);}}/>显示可拖动分叉点</label>
        {editRoot && <>
          <label>参考分叉组 <select aria-label="参考分叉组" value={rootGroup} disabled={busy} onChange={e=>{const g=Number(e.target.value);setRootGroup(g);setRootPoints(a.root_fit!.all_points[g]);setRootDirty(false);}}>
            {a.root_fit.all_points.map((_,i)=><option value={i} key={i}>第 {i+1} 组</option>)}
          </select></label>
          <p className="photo-help">1：底部；2 / 3：左右转接。修改按对称约束作用于整圈。切换组会恢复该组候选点。</p>
          {rootPoints.map((point,i)=><div className="photo-point-row" key={i}><span>点 {i+1}</span>{point.map((value,j)=><label key={j}>{j?'Y':'X'}<input aria-label={`分叉点 ${i+1} ${j?'Y':'X'}`} type="number" step={.1} min={0} max={j?h:w} value={Number(value.toFixed(2))} disabled={busy||!!outdated} onChange={e=>{const v=Number(e.target.value);if(Number.isFinite(v)){setRootPoints(current=>current.map((p,k)=>k===i ? (j?[p[0],v]:[v,p[1]]) : p));setRootDirty(true);}}}/></label>)}</div>)}
          <button className="secondary-button" onClick={()=>svgRef.current?.scrollIntoView({block:'center',behavior:'smooth'})}>回到图片调整</button>
          <button className="secondary-button" disabled={busy||!!outdated||!rootDirty} onClick={()=>onRefineRoot(a,rootGroup,rootPoints)}>按点位重新拟合</button>
          <button className="secondary-button" disabled={busy} onClick={()=>{setRootPoints(a.root_fit!.all_points[rootGroup]);setRootDirty(false);}}>恢复候选点</button>
          {rootDirty && <p className="photo-help">点位尚未拟合；重新拟合通过后再应用到草稿。</p>}
        </>}
      </div>}
      {shown?.report?.skeleton && <div className="photo-skeleton">
        <strong>当前 CAD 的骨架粗细 · {shown.id.slice(0,6)}</strong>
        <p className="photo-help">{shown.report.skeleton.explicit_profile ? '直顺轮廓已进入实体；从过渡端到末端按单臂宽度逐渐收窄。侧壁与棱边处理见检查页。' : shown.report.skeleton.window ? '大窗口弧形连接已进入实体。底部外移控制连接区大小，过渡终点控制根部向细辐收敛的长度。' : '大窗口弧形连接未启用，可在右侧双辐造型中调整。'}</p>
        <table><thead><tr><th>位置</th><th>单臂正面宽度</th><th>前后厚度</th></tr></thead><tbody>
          {shown.report.skeleton.stations.map((s,i)=><tr key={s.fraction}><td>{['辐根过渡','前段','中段','后段','末端'][i]}</td><td>{s.blade_width_mm.toFixed(1)} mm</td><td>{s.depth_mm.toFixed(1)} mm</td></tr>)}
        </tbody></table><p className="photo-help">{shown.report.skeleton.note}</p>
      </div>}
      <div className="photo-candidates">
        <strong>{a.window_fit ? (a.status === 'candidates' ? '可核对的窗口法候选' : '窗口轮廓未通过模板校验') : a.status === 'candidates' ? '可核对的双辐候选' : '检测存在歧义，请人工核对'}</strong>
        {!a.window_fit && <p>外圈边缘覆盖 {(a.edge_coverage*100).toFixed(0)}% · 拟合中位残差 {a.edge_residual_px} px（检测图分辨率，非实物精度）</p>}
        {a.window_fit ? <div className="photo-fit-result">
          <strong>{applied ? '窗口轮廓已应用 · 以当前 CAD 版本为准' : `每组 ${a.window_fit.window_count} 个窗口 · 待应用生成`}</strong>
          <p>未参与拟合的第 {a.window_fit.held_out_groups.map(g => g + 1).join('、')} 组：重合度 {a.window_fit.held_out_iou_mean?.toFixed(2) ?? '—'}；标注自身各组一致性（可达上限）{a.window_fit.label_loo_mean.toFixed(2)}。</p>
          {a.window_fit.window_error && <p className="photo-help">{a.window_fit.window_error}</p>}
          <p className="photo-help">在假设的辐条正面上按面积计算，只评价正面窗口形状，不代表深度或实物尺寸。</p>
        </div> : fit?.status === 'fitted' ? <div className="photo-fit-result">
          <strong>{applied ? '辐条截面已应用 · 以当前 CAD 版本为准' : '辐条截面已拟合 · 待应用生成'}</strong>
          <p>截面宽度偏差 RMS：{(fit.before_rms_ratio!*radius).toFixed(1)} → {(fit.after_rms_ratio!*radius).toFixed(1)} px。各截面支持组数：{fit.stations.map(s => s.support_groups).join(' / ')}。</p>
          <p className="photo-help">按当前外圈对齐计算净间隙与整组宽度；这是候选截面的拟合偏差，不是整轮相似度或实物精度。金色线仍以生成后的 CAD 为准。</p>
        </div> : <p className="photo-help">{fit?.status === 'manual_window_active' ? '大窗口曲线已启用，保留已确认的截面宽度；当前识图不重新求解整组宽度。' : fit?.status === 'superseded_by_manual_points' ? '分叉已按人工点位修正，旧截面拟合指标不再适用。' : fit?.status === 'no_improvement' ? '现有截面已接近本次候选，本次不重复调整轮廓。' : '轮廓证据不足或超出 CAD 约束；本次没有自动修改辐根和中段宽度。'}</p>}
        <div className="photo-values">{Object.entries(a.suggested_parameters).map(([key,value]) => <span key={key}>{names[key] || key} <b>{Array.isArray(value) ? `${value.length} 个/组` : value === 'window' ? '窗口法' : String(value)}</b></span>)}</div>
        <p>{a.scale.basis}。目标模型外径 {a.scale.target_outer_mm.toFixed(1)} mm。</p>
        {a.scale.reference_outer_mm && a.scale.reference_gap_mm !== null && <p>用户填写的参考外径 {a.scale.reference_outer_mm} mm → 外端间隙候选约 {a.scale.reference_gap_mm?.toFixed(1)} mm，尚未核验。</p>}
        <button className="secondary-button" disabled={busy || rootDirty || !a.can_apply || !!applied || !!outdated} onClick={() => onApply(a)}>{applied ? '候选已应用到草稿' : outdated ? '草稿已变更，请重新识图' : '确认点位后，将候选应用到草稿'}</button>
        <p>应用后仍需生成新版本；当前金色投影始终来自已生成的 CAD。对齐微调仅改变视图，不改变尺寸。</p>
        {a.warnings.map((warning,i) => <p className="photo-help" key={i}>{warning}</p>)}
      </div>
    </>}
  </div>;
}
