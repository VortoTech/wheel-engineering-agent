import { useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent } from 'react';
import { api } from './types';
import type { Job, PhotoPose, Project } from './types';
import { Viewer } from './Viewer';
import { PhotoProjection } from './PhotoProjection';
import './sector-study.css';

type Point = [number, number];
type NumericControls = { crown:number; ridge:number; groove:number; edge_width_px:number; ridge_width_px:number; thickness:number; end_blend_px:number; fairing_px:number; pocket_depth:number; hole_shoulder_depth:number; hole_shoulder_width_px:number };
type Controls = NumericControls & {camera_model:'legacy'|'anchors'; relief_profile:'legacy'|'smooth'; junction_mode:'capped'|'continuous';surface_scope:'spoke'|'root-panel';opening_mode:'through'|'recess';root_sampling:'fine'|'legacy';surface_model:'heightfield-v7'|'section-cage-v8'|'boundary-patches-v10'};
type Score = {median_px:number; max_px:number; p95_px:number; predicted:Point[]; observed:Point[]; threshold_passed:boolean; annotation_reviewed?:boolean};
type StudyReport = {
  algorithm:string; surface_model:Controls['surface_model']; groups:number; lug_count:number; boundary:Point[];
  full_wheel_preview:{algorithm:string;generated:boolean;groups:number;master_instances:number;geometry_instanced:boolean;connected_or_fused:boolean;rim_and_hub_are_assumed_context:boolean;lug_recesses_are_display_only:boolean;engineering_approved:boolean;solid?:{cadquery_valid:boolean;solid_count:number;volume_R3:number;vertex_count:number;triangle_count:number};note:string};
  section_cage:null|{algorithm:string;branch_count:number;section_order:string[];branches:{stations:number;median_half_width_px?:number;min_half_width_px?:number;max_half_width_px?:number;median_left_width_px?:number;median_right_width_px?:number;min_side_width_px?:number;max_side_width_px?:number;min_total_width_px?:number;median_total_width_px?:number;entry_station?:number;max_center_correction_px?:number;width_source?:string}[];review_required:boolean;narrow_branches:number[];root_patch?:{center_px:Point;blend_radius_px:number;independent:boolean;depth_measured:boolean};note:string};
  camera:{cx:number; cy:number; scale:number; rotation:number[][]; distance_radii:number; sag:number; model:string;
    candidates?:{distance_radii:number; anchor_rms_px:number; ring_check_rms_px:number; selection_score_px:number}[]};
  diagnostic:Score; holdout:Score|null; gate:{passed:boolean; reason:string}; glb_sha256:string;
  integrity:{nonmanifold_edges:number; min_axial_thickness:number; connected_components:number; euler_characteristic:number};
  openings:{count:number; mode:string; note:string; inner_wall_triangles:number; contours:Point[][];
    shape_metrics:{major_extent_px:number;minor_extent_px:number;aspect_ratio:number;axis_image:Point;measured_in_image:boolean}[];
    rib_metrics:{to_outer_px:number;to_other_opening_px:number|null;minimum_visible_rib_px:number;measured_in_image:boolean}[];
    shoulder:{enabled:boolean;note:string}};
  sampling:{spacing_px:number;note:string};
  fairing:{energy_before:number; energy_after:number; rms_displacement_R:number; boundary_max_displacement_R:number; width_px:number};
  junctions:{note:string};
};
type Run = {id:string; created_at:string; controls:Controls;full_wheel_available?:boolean};
type Metadata = {
  controls:Controls; cad_groups:number; cad_lugs:number; runs:Run[];
  evidence:{image_size:Point; master:{boundary:Point[];panel_boundary:Point[]}; uncertain_pockets:Point[][]; structure:{spoke_groups:number; lug_centers:Point[]};validation:{points:Point[]};candidate?:{algorithm:string;master_group:number;eligible_groups:number[];sample_rows:number}};
  holdout:{points:Point[]};
  evidence_scope:'per-image';
  provenance:{source_sha256:string;evidence_id:string;evidence_origin:'per-image'|'builtin-sample'};
};
const fields: {key:keyof NumericControls; label:string; min:number; max:number; step:number; scale:number; unit:string}[] = [
  {key:'crown',label:'正面拱高',min:0,max:25,step:1,scale:1000,unit:'‰ R'},
  {key:'ridge',label:'凸脊高度',min:0,max:25,step:1,scale:1000,unit:'‰ R'},
  {key:'groove',label:'凹槽深度',min:0,max:12,step:.5,scale:1000,unit:'‰ R'},
  {key:'edge_width_px',label:'边缘过渡宽度',min:1,max:8,step:.25,scale:1,unit:'px'},
  {key:'ridge_width_px',label:'脊槽过渡宽度',min:1,max:8,step:.25,scale:1,unit:'px'},
  {key:'thickness',label:'假设侧壁厚度',min:15,max:80,step:1,scale:1000,unit:'‰ R'},
  {key:'end_blend_px',label:'脊槽端部渐隐',min:2,max:30,step:1,scale:1,unit:'px'},
  {key:'fairing_px',label:'曲面平顺宽度',min:0,max:3,step:.1,scale:1,unit:'px'},
  {key:'pocket_depth',label:'浅槽目标深度',min:2,max:12,step:.5,scale:1000,unit:'‰ R'},
  {key:'hole_shoulder_depth',label:'孔口下沉深度',min:0,max:8,step:.5,scale:1000,unit:'‰ R'},
  {key:'hole_shoulder_width_px',label:'孔口过渡宽度',min:1,max:4,step:.25,scale:1,unit:'px'},
];

export function SectorStudy({project, shown}: {project:Project; shown:Job|null}) {
  const [meta,setMeta] = useState<Metadata|null>(null);
  const [controls,setControls] = useState<Controls|null>(null);
  const [boundaries,setBoundaries] = useState<Record<Controls['surface_scope'],Point[]>>({'spoke':[],'root-panel':[]});
  const boundary = boundaries[controls?.surface_scope??'root-panel'];
  const [openings,setOpenings] = useState<Point[][]>([]);
  const [editTarget,setEditTarget] = useState(-1);
  const [preview,setPreview] = useState<{report:StudyReport; glb_base64:string;full_glb_base64:string}|null>(null);
  const [error,setError] = useState('');
  const [pending,setPending] = useState(true);
  const [saving,setSaving] = useState(false);
  const [creating,setCreating] = useState(false);
  const [reload,setReload] = useState(0);
  const [saved,setSaved] = useState<Run|null>(null);
  const [mode,setMode] = useState<'overlay'|'source'|'edit'|'holdout'|'diagnostic'|'3d'|'full3d'|'old'>('overlay');
  const [opacity,setOpacity] = useState(.75);
  const [full,setFull] = useState(false);
  const [rootFocus,setRootFocus] = useState(true);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<number|null>(null);
  const alive = useRef(true);
  const sequence = useRef(0);
  const base = `/projects/${project.id}/sector-study`;

  useEffect(() => {
    let cancelled=false;
    setMeta(null);setControls(null);setPending(true);setError('');
    api<Metadata>(base).then(value=>{
      if(cancelled)return;
      setMeta(value);setControls(value.controls);setBoundaries({'spoke':value.evidence.master.boundary,'root-panel':value.evidence.master.panel_boundary});setOpenings(value.evidence.uncertain_pockets);
    }).catch(exc=>{if(!cancelled){setError(exc.message);setPending(false);}});
    return ()=>{cancelled=true;};
  },[base,reload]);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{
    if(!controls || !boundary.length)return;
    const controller=new AbortController();const current=++sequence.current;
    setPending(true);setError('');setSaved(null);
    const timer=window.setTimeout(()=>{
      api<{report:StudyReport; glb_base64:string;full_glb_base64:string}>(`${base}/preview`,{method:'POST',signal:controller.signal,
        body:JSON.stringify({expected_revision:project.revision,controls,boundary,openings})})
        .then(value=>{if(!controller.signal.aborted && current===sequence.current){setPreview(value);setPending(false);}})
        .catch(exc=>{if(!controller.signal.aborted && current===sequence.current){setError(exc.message);setPending(false);}});
    },400);
    return()=>{clearTimeout(timer);controller.abort();};
  },[base,project.revision,controls,boundary,openings]);

  const url=useMemo(()=>{
    if(!preview)return null;
    const bytes=Uint8Array.from(atob(preview.glb_base64),c=>c.charCodeAt(0));
    return URL.createObjectURL(new Blob([bytes],{type:'model/gltf-binary'}));
  },[preview]);
  useEffect(()=>()=>{if(url)URL.revokeObjectURL(url);},[url]);
  const fullUrl=useMemo(()=>{
    if(!preview)return null;
    const bytes=Uint8Array.from(atob(preview.full_glb_base64),c=>c.charCodeAt(0));
    return URL.createObjectURL(new Blob([bytes],{type:'model/gltf-binary'}));
  },[preview]);
  useEffect(()=>()=>{if(fullUrl)URL.revokeObjectURL(fullUrl);},[fullUrl]);
  const pose=useMemo<PhotoPose|null>(()=>preview?{
    cx:preview.report.camera.cx,cy:preview.report.camera.cy,scale_px:preview.report.camera.scale,
    rotation:preview.report.camera.rotation,distance_radii:preview.report.camera.distance_radii,radius_mm:1,reference_z_mm:0,
    angles_deg:[0,0,0],
  }:null,[preview]);

  async function save() {
    if(!controls || pending || error)return;
    setSaving(true);setError('');
    try {
      const result=await api<Run>(`${base}/runs`,{method:'POST',body:JSON.stringify({expected_revision:project.revision,controls,boundary,openings})});
      if(alive.current){setSaved(result);setMeta(value=>value?{...value,runs:[result,...value.runs]}:value);}
    } catch(exc){if(alive.current)setError((exc as Error).message);}
    finally{if(alive.current)setSaving(false);}
  }
  async function createCandidate() {
    setCreating(true);setError('');
    try {
      await api(`${base}/evidence/candidate`,{method:'POST',body:JSON.stringify({expected_revision:project.revision})});
      if(alive.current){setPending(true);setReload(value=>value+1);}
    } catch(exc){if(alive.current)setError((exc as Error).message);}
    finally{if(alive.current)setCreating(false);}
  }
  function move(event:PointerEvent<SVGSVGElement>) {
    if(drag.current===null || !svg.current || saving || !controls)return;
    const point=svg.current.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
    const matrix=svg.current.getScreenCTM();if(!matrix)return;
    const p=point.matrixTransform(matrix.inverse());
    const moved=(points:Point[])=>points.map((value,i):Point=>i===drag.current
      ? [Math.max(0,Math.min(meta?.evidence.image_size[0]??0,p.x)),Math.max(0,Math.min(meta?.evidence.image_size[1]??0,p.y))] : value);
    if(editTarget<0)setBoundaries(value=>({...value,[controls.surface_scope]:moved(value[controls.surface_scope])}));
    else setOpenings(value=>value.map((loop,i)=>i===editTarget?moved(loop):loop));
  }
  if(!meta || !controls)return <div className="sector-study"><h2>辐条研究</h2><p role={error?'alert':'status'}>{error||'正在核对原图与结构标注…'}</p>{error&&<><button className="secondary-button" disabled={creating||!project.photo_analysis} onClick={()=>void createCandidate()}>{creating?'正在组合多组边缘…':'从当前识图建立可编辑母扇区'}</button><p className="photo-help">需要先在“照片对照与识图”中提取当前主图的外圈、周期和逐支臂边缘。候选不会自动应用为完整轮毂。</p></>}</div>;
  const report=preview?.report;
  const editing=editTarget<0?boundary:openings[editTarget]??[];
  const score=mode==='holdout'?report?.holdout:mode==='diagnostic'?report?.diagnostic:null;
  const [imageWidth,imageHeight]=meta.evidence.image_size;
  const box=(points:Point[],padding=.12)=>{const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);const pad=Math.max(x1-x0,y1-y0)*padding+4;return `${Math.max(0,x0-pad)} ${Math.max(0,y0-pad)} ${Math.min(imageWidth,x1+pad)-Math.max(0,x0-pad)} ${Math.min(imageHeight,y1+pad)-Math.max(0,y0-pad)}`;};
  const focusPoints=[...boundary,...openings.flat()];
  const viewBox=full?`0 0 ${imageWidth} ${imageHeight}`:mode==='holdout'?box(meta.holdout.points,.3):mode==='diagnostic'?box(meta.evidence.validation.points,.3):box(focusPoints,rootFocus&&controls.surface_scope==='root-panel'&&mode!=='old'?.08:.35);
  const oldPose=shown?.snapshot.photo_analysis?.camera_fit?.pose;
  const oldSize=shown?.snapshot.photo_analysis?.image_size ?? [620,591];
  return <div className="sector-study">
    <p role="note" className="sector-error">历史视觉实验已冻结：这里的局部曲面和整轮灰模不能作为工程 CAD、STEP 或制造输入。项目主线已切回参数化 B-Rep。</p>
    <div className="sector-heading"><div><span className="section-eyebrow">MASTER SURFACE · 实验</span><h2>{controls.surface_scope==='root-panel'?'根部镂空与主辐条一起建模':'先把一组 Y 形辐条做准'}</h2></div><span className="visual-badge">{mode==='full3d'?'整轮试拼':'局部灰模'}</span></div>
    <p className="sector-topology">原图标注：{meta.evidence.structure.spoke_groups} 组 Y 形 · {meta.evidence.structure.lug_centers.length} 个孔位。旧 CAD：{meta.cad_groups} 组 · {meta.cad_lugs} 个孔位。</p>
    <p className="sector-topology">证据范围：当前图片独立 · {meta.provenance.evidence_origin==='per-image'?'版本':'内置样例'} {meta.provenance.evidence_id.slice(0,8)} · 原图 {meta.provenance.source_sha256.slice(0,10)}</p>
    {meta.evidence.candidate&&<p className="sector-topology">自动草稿：第 {meta.evidence.candidate.master_group+1} 组母扇区 · {meta.evidence.candidate.eligible_groups.length} 组边缘支持 · {meta.evidence.candidate.sample_rows} 个径向截面。尚未人工复核，不会放行整轮。</p>}
    <div className="sector-modes" role="group" aria-label="辐条对照方式">{([
      ['overlay','新灰模叠加'],['source','原图'],['edit','编辑边界'],['3d','旋转灰模'],['full3d','整轮试拼'],['old','旧 CAD'],['diagnostic','相邻组诊断'],['holdout','固定观察组'],
    ] as const).map(([value,label])=><button key={value} aria-pressed={mode===value} onClick={()=>setMode(value)}>{label}</button>)}</div>
    {mode==='3d'?<Viewer url={url} building={pending} initialNeutral inspectionLighting unitLabel="R = 1，未测量" label="单组 Y 形 · 灰模侧光检查"/>:mode==='full3d'?<Viewer url={fullUrl} building={pending} initialNeutral inspectionLighting unitLabel="R = 1，未测量" label="六组实例化整轮 · 轮辋与中心盘为假设上下文"/>:
      <svg className="sector-photo" ref={svg} viewBox={viewBox} onPointerMove={move}
        onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}
        aria-label="原图与辐条几何对照">
        <image href={`/api${base}/source`} width={imageWidth} height={imageHeight}/>
        {mode==='overlay' && url && pose && <foreignObject x="0" y="0" width={imageWidth} height={imageHeight} opacity={opacity} pointerEvents="none"><PhotoProjection url={url} pose={pose} width={imageWidth} height={imageHeight} neutral/></foreignObject>}
        {mode==='old' && shown && oldPose && <foreignObject x="0" y="0" width={imageWidth} height={imageHeight} opacity={opacity} pointerEvents="none"><PhotoProjection url={`/api/builds/${shown.id}/glb`} pose={oldPose} width={oldSize[0]} height={oldSize[1]}/></foreignObject>}
        {mode==='edit' && <><polygon points={boundary.map(p=>p.join(',')).join(' ')} fill="#06b6d422" stroke="#00cbd9" strokeWidth=".7"/>{controls.surface_scope==='root-panel' && openings.map((loop,i)=><polygon key={i} points={loop.map(p=>p.join(',')).join(' ')} fill="#fb923c33" stroke="#ff983d" strokeWidth=".7"/>)}{editing.map(([x,y],i)=><circle key={i} cx={x} cy={y} r="1.7" fill={editTarget<0?'#02e3eb':'#ff983d'} stroke="#123" strokeWidth=".4" style={{cursor:'grab'}} onPointerDown={event=>{if(saving)return;drag.current=i;svg.current?.setPointerCapture(event.pointerId);}}/>)}</>}
        {score && <><polygon points={score.predicted.map(p=>p.join(',')).join(' ')} fill="none" stroke="#f24891" strokeWidth="1"/>{score.observed.map(([x,y],i)=><circle key={i} cx={x} cy={y} r="1.8" fill="#00e1e9" stroke="#153340" strokeWidth=".4"/>)}</>}
      </svg>}
    <div className="sector-caption">
      {mode!=='3d'&&mode!=='full3d' && <label><input type="checkbox" checked={full} onChange={e=>setFull(e.target.checked)}/>查看整张图</label>}
      {['overlay','source','edit'].includes(mode)&&controls.surface_scope==='root-panel'&&!full && <label><input type="checkbox" checked={rootFocus} onChange={e=>setRootFocus(e.target.checked)}/>放大根部孔区</label>}
      {(mode==='overlay'||mode==='old') && <label>叠加透明度 <input aria-label="辐条叠加透明度" type="range" min="0" max="1" step=".05" value={opacity} onChange={e=>setOpacity(+e.target.value)}/></label>}
      {mode==='edit' && <><label>编辑对象<select aria-label="编辑对象" value={editTarget} onChange={e=>{drag.current=null;setEditTarget(+e.target.value);}}><option value="-1">整体外轮廓</option>{controls.surface_scope==='root-panel' && openings.map((_,i)=><option key={i} value={i}>{i===0?'上方':i===1?'下方':i+1}狭长孔</option>)}</select></label><span>拖动节点调整外轮廓或孔边；孔不能穿出外缘或互相接触。</span></>}
      {mode==='old' && <span>旧模型 {shown?.id.slice(0,6)??'无'} · 使用它保存时的拟合相机。{!oldPose && '该版本没有相机记录，无法叠加。'}</span>}
    </div>
    <p role="status" className="sector-progress">{pending?'正在更新曲面，画面暂为上一结果…':saved?`已保存研究版本 ${saved.id.slice(0,8)} · ${new Date(saved.created_at).toLocaleString()}`:report?'当前为未保存的研究预览':'尚无预览'}</p>
    {error && <p role="alert" className="sector-error">{error}</p>}
    <fieldset className="sector-controls" disabled={saving}><legend>{controls.surface_scope==='root-panel'?'根部孔区 · 保留镂空，连好周围薄肋':'母扇区范围 · 先校正两条分叉臂外缘'}</legend>
      <label>建模范围<select aria-label="建模范围" value={controls.surface_scope} onChange={e=>{setControls({...controls,surface_scope:e.target.value as Controls['surface_scope']});setEditTarget(-1);}}><option value="root-panel">主辐条＋两侧孔区（新）</option><option value="spoke">仅 Y 形主体（v3 对照）</option></select></label>
      <label>孔结构假设<select aria-label="孔结构假设" disabled={controls.surface_scope==='spoke'} value={controls.opening_mode} onChange={e=>setControls({...controls,opening_mode:e.target.value as Controls['opening_mode']})}><option value="through">贯通孔＋孔壁</option><option value="recess">浅凹槽（保留底部）</option></select></label>
      <p>{controls.surface_scope==='root-panel'?'先对照孔形，再在“编辑边界”中选择上方或下方狭长孔。孔深和背面出口尚未测量。':'自动轨迹只给出可编辑外缘；请先检查根部连接、两个辐端切口和分叉间距，再考虑孔区。'}</p>
    </fieldset>
    <details className="sector-advanced"><summary>高级造型参数（相机、脊槽、厚度）</summary>
    <fieldset className="sector-controls" disabled={saving}><legend>方法对照 · 不改变原图和验证点</legend>
      <label>正面曲面模型<select aria-label="正面曲面模型" value={controls.surface_model} onChange={e=>setControls({...controls,surface_model:e.target.value as Controls['surface_model']})}><option value="heightfield-v7">v7 距离场基线</option><option value="section-cage-v8">v8 双支臂截面控制笼</option><option value="boundary-patches-v10">v10 外缘截面＋根部补片（实验）</option></select></label>
      <label>相机假设<select aria-label="相机假设" value={controls.camera_model} onChange={e=>setControls({...controls,camera_model:e.target.value as Controls['camera_model']})}><option value="legacy">原外圈相机（默认）</option><option value="anchors">外圈＋六孔位候选（实验）</option></select></label>
      <label>脊槽造型<select aria-label="脊槽造型" value={controls.relief_profile} onChange={e=>setControls({...controls,relief_profile:e.target.value as Controls['relief_profile']})}><option value="smooth">平滑导线＋端部渐隐（新）</option><option value="legacy">折线导线（v1 对照）</option></select></label>
      <label>根部与辐端切口<select aria-label="根部与辐端切口" value={controls.junction_mode} onChange={e=>setControls({...controls,junction_mode:e.target.value as Controls['junction_mode']})}><option value="continuous">连接切口延续曲面（新）</option><option value="capped">所有边缘倒圆（v2 对照）</option></select></label>
      <p>{controls.surface_model==='boundary-patches-v10'?'v10 从母扇区外缘求每个截面的左右宽度，并单独平顺根部分叉。':controls.surface_model==='section-cage-v8'?'v8 将每条支臂按外缘—凸脊—主体—凹槽—内缘截面插值；根部仍共用旧面。':'v7 用距离导线叠加凸脊与凹槽，作为历史基线。'} 候选相机未改善邻组轮廓，未升级为默认。</p>
    </fieldset>
    <fieldset className="sector-controls" disabled={saving}><legend>曲面控制 · R 为轮毂外半径，尺寸尚未测量</legend>{fields.filter(field=>field.key==='pocket_depth'?controls.surface_scope==='root-panel'&&controls.opening_mode==='recess':field.key.startsWith('hole_shoulder')?controls.surface_scope==='root-panel'&&controls.opening_mode==='through':true).map(field=><label key={field.key}>{field.label}<output>{+(controls[field.key]*field.scale).toFixed(2)} {field.unit}</output><input aria-label={field.label} type="range" min={field.min} max={field.max} step={field.step} value={controls[field.key]*field.scale} onChange={e=>setControls({...controls,[field.key]:+e.target.value/field.scale})}/></label>)}</fieldset>
    <button disabled={saving} onClick={()=>setControls({...controls,junction_mode:'capped',fairing_px:0})}>对照 v2 接口与平顺</button>
    </details>
    {controls.surface_scope==='root-panel'&&controls.opening_mode==='through'&&<div className="sector-actions" role="group" aria-label="孔口细化对照"><button disabled={saving} aria-pressed={controls.root_sampling==='fine'&&controls.hole_shoulder_depth>0} onClick={()=>setControls({...controls,root_sampling:'fine',hole_shoulder_depth:meta.controls.hole_shoulder_depth,hole_shoulder_width_px:meta.controls.hole_shoulder_width_px})}>细化孔口过渡</button><button disabled={saving} aria-pressed={controls.root_sampling==='legacy'&&controls.hole_shoulder_depth===0} onClick={()=>setControls({...controls,root_sampling:'legacy',hole_shoulder_depth:0})}>对照上一版孔口</button></div>}
    <div className="sector-actions"><button disabled={pending||saving||!!error||!report} onClick={()=>void save()}>{saving?'正在保存…':'保存研究版本'}</button><button disabled={saving} onClick={()=>{setControls(meta.controls);setBoundaries({'spoke':meta.evidence.master.boundary,'root-panel':meta.evidence.master.panel_boundary});setOpenings(meta.evidence.uncertain_pockets);setEditTarget(-1);}}>恢复初始曲面</button></div>
    {report && <div className="sector-validation"><h3>验收记录 · {report.algorithm}</h3><table><thead><tr><th>观察范围</th><th>中位误差</th><th>最大误差</th><th>用途</th></tr></thead><tbody>{[['相邻组',report.diagnostic,'已用于结构诊断'],['下方固定组',report.holdout,'未参与数值拟合 · 已用于版本比较']].map(([label,s,scope])=>{const value=s as Score|null;return <tr key={label as string}><td>{label as string}</td><td>{value?.median_px.toFixed(1)??'—'} px</td><td>{value?.max_px.toFixed(1)??'—'} px</td><td>{scope as string}</td></tr>;})}</tbody></table><p>{report.gate.passed?'局部边界门槛通过，连接结构仍待构建。':'整轮复制尚未放行。'} {report.gate.reason}</p><p>局部网格非流形边：{report.integrity.nonmanifold_edges}；背面、厚度和凹凸高度仍是假设，尚未验证自交、实体连接或加工。此处像素误差只评价边界。固定观察组已看过多轮，不再作为最终盲测。</p>
      <p>孔区：{report.openings.count} 个{report.openings.mode==='through'?'贯通候选孔':report.openings.mode==='recess'?'浅槽':'孔区'} · 连通分量 {report.integrity.connected_components} · 孔壁三角面 {report.openings.inner_wall_triangles}。{report.openings.note}</p>
      {!!report.openings.shape_metrics.length&&<p>原图孔形：{report.openings.shape_metrics.map((shape,i)=>`${i===0?'上':'下'}孔 ${shape.major_extent_px.toFixed(1)}×${shape.minor_extent_px.toFixed(1)} px（长宽比 ${shape.aspect_ratio.toFixed(1)}）`).join('；')}。这里只量原图轮廓，不代表毫米尺寸。</p>}
      {!!report.openings.rib_metrics.length&&<p>孔周围可见薄肋：{report.openings.rib_metrics.map((rib,i)=>`${i===0?'上':'下'}孔到外缘最窄 ${rib.to_outer_px.toFixed(2)} px`).join('；')}。数值只用于发现贴边和破面风险，不是实物肋厚。</p>}
      <p>曲面采样间距 {report.sampling.spacing_px} px。{report.sampling.note}{report.openings.shoulder.enabled&&report.openings.shoulder.note}</p>
      {report.section_cage&&<p>截面控制笼：{report.section_cage.branch_count} 条支臂 × {report.section_cage.branches[0]?.stations??0} 个纵向站位；截面顺序为{report.section_cage.section_order.join(' → ')}。{report.section_cage.branches[0]?.width_source&&` 有效支臂左右中位宽度：${report.section_cage.branches.map((branch,i)=>`支臂 ${i+1} 为 ${branch.median_left_width_px?.toFixed(1)}/${branch.median_right_width_px?.toFixed(1)} px（从 ${(100*(branch.entry_station??0)).toFixed(0)}% 站位接管）`).join('；')}。`}{report.section_cage.root_patch&&` 根部独立补片覆盖半径 ${report.section_cage.root_patch.blend_radius_px.toFixed(1)} px。`}{report.section_cage.note}</p>}
      <p>整轮试拼：{report.full_wheel_preview.connected_or_fused?'中心盘、辐条与轮辋为同一融合实体':`同一母扇区实例化 ${report.full_wheel_preview.master_instances} 次`}；{report.full_wheel_preview.solid&&`实体数 ${report.full_wheel_preview.solid.solid_count}，三角面 ${report.full_wheel_preview.solid.triangle_count}。`}{report.full_wheel_preview.note}</p>
      <p>平顺处理：凹凸场弯曲能量下降 {report.fairing.energy_before>0?Math.max(0,100*(1-report.fairing.energy_after/report.fairing.energy_before)).toFixed(1):'0'}%；RMS 改变量 {(report.fairing.rms_displacement_R*1000).toFixed(2)} ‰ R，处理前后边界高度不变。此数值只衡量平顺程度，不是还原准确率。</p><p>{report.junctions.note}</p>
      <details><summary>相机与深度假设</summary><p>凹面深度 {report.camera.sag.toFixed(3)} R · {report.camera.distance_radii>=1e5?'弱透视':`相机距离 ${report.camera.distance_radii} R`}；不是实测尺寸。候选仅使用外圈、孔位与中心，不读取辐条观察点。</p>{report.camera.candidates && <table><thead><tr><th>距离假设</th><th>锚点 RMS</th><th>外圈检查 RMS</th></tr></thead><tbody>{report.camera.candidates.map(c=><tr key={c.distance_radii}><td>{c.distance_radii>=1e5?'弱透视':`${c.distance_radii} R`}{c.distance_radii===report.camera.distance_radii?' · 当前':''}</td><td>{c.anchor_rms_px.toFixed(2)} px</td><td>{c.ring_check_rms_px.toFixed(2)} px</td></tr>)}</tbody></table>}</details>
    </div>}
    {!!meta.runs.length && <div className="sector-history"><h3>已保存的研究</h3>{meta.runs.slice(0,5).map(run=><div key={run.id}><span>{new Date(run.created_at).toLocaleString()} · {run.id.slice(0,8)}</span>{(['glb',...(run.full_wheel_available?['full-glb'] as const:[]),'report','evidence','recipe'] as const).map(kind=><a key={kind} href={`/api${base}/runs/${run.id}/${kind}`} download>{({glb:'局部 GLB','full-glb':'整轮试拼 GLB',report:'验证报告',evidence:'曲线标注',recipe:'配方与摘要'})[kind]}</a>)}</div>)}</div>}
  </div>;
}
