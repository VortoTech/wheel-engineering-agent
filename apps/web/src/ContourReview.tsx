import { useEffect, useRef, useState } from 'react';
import { api } from './types';
import type { Job, PhotoAnalysis, Project } from './types';
type Review={id:string;job_id:string;label:string;points:[number,number][];metrics:{mean_px:number;p95_px:number;within_3px:number;samples:{point:[number,number];distance_px:number}[]};note:string};
export function ContourReview({project,shown}:{project:Project;shown:Job|null}) {
  const analysis=shown?.snapshot.photo_analysis as PhotoAnalysis|undefined;
  const [points,setPoints]=useState<[number,number][]>([]),[label,setLabel]=useState('辐条可见边界');
  const [reviews,setReviews]=useState<Review[]>([]),[result,setResult]=useState<Review|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const svg=useRef<SVGSVGElement>(null);
  useEffect(()=>{let cancelled=false;api<Review[]>(`/projects/${project.id}/contour-reviews`).then(r=>{if(!cancelled)setReviews(r);}).catch(e=>{if(!cancelled)setError(e.message);});return()=>{cancelled=true;};},[project.id]);
  if(!shown || !analysis?.camera_fit || analysis.image_id!==project.primary_image_id)return <p className="photo-help">轮廓量化：请用当前主图识图、应用候选并生成版本，评估将使用该版本保存的相机。</p>;
  const [w,h]=analysis.image_size;
  const evaluate=async()=>{setBusy(true);setError('');try{
    const r=await api<Review>(`/projects/${project.id}/builds/${shown.id}/contour-reviews`,{method:'POST',body:JSON.stringify({expected_revision:project.revision,label,points})});
    setResult(r);setReviews(previous=>[r,...previous]);
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  return <details className="contour-review"><summary>标注轮廓并量化偏差 · CAD {shown.id.slice(0,6)}</summary>
    <p>在下图沿一段连续的辐条或窗口遮挡边界依次点击，至少 3 点。分开标注不同边；不要标高光或装饰线。此图不显示拟合点，标注不会反过来修改模型。</p>
    <p className="photo-help">使用该模型保存的相机，忽略上方临时对齐滑块。数值只代表已标区域；完整轮廓及未见结构仍未验证。</p>
    <svg ref={svg} viewBox={`0 0 ${w} ${h}`} aria-label="独立轮廓标注" onClick={e=>{if(busy||result||!svg.current)return;const matrix=svg.current.getScreenCTM();if(!matrix)return;const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());if(p.x>=0&&p.x<w&&p.y>=0&&p.y<h)setPoints(current=>[...current,[p.x,p.y] as [number,number]].slice(0,500));}}>
      <image href={`/api/images/${analysis.image_id}`} width={w} height={h}/>
      <polyline points={(result?.points??points).map(p=>p.join(',')).join(' ')} stroke="#55e6dd" strokeWidth={1.3} fill="none"/>
      {(result?.metrics.samples??points.map(point=>({point,distance_px:0}))).map(({point:[x,y],distance_px:d},i)=><circle key={i} cx={x} cy={y} r={result?1.5:2.5} fill={d>6?'#ff5b5b':d>3?'#ffc04d':'#55e6dd'}/>)}
    </svg>
    <div className="photo-actions"><label>区域名称<input aria-label="轮廓区域名称" value={label} maxLength={80} disabled={busy||!!result} onChange={e=>setLabel(e.target.value)}/></label>
      <button disabled={busy||!!result||!points.length} onClick={()=>setPoints(p=>p.slice(0,-1))}>撤销末点</button>
      <button disabled={busy} onClick={()=>{setPoints([]);setResult(null);}}>新标一段</button>
      <button className="secondary-button" disabled={busy||!!result||points.length<3||!label.trim()} onClick={evaluate}>{busy?'计算实体边界…':`保存并评估 ${points.length} 点`}</button></div>
    {error&&<p role="alert">{error}</p>}
    {result&&<div role="status"><strong>{result.label}：平均 {result.metrics.mean_px.toFixed(1)} px · P95 {result.metrics.p95_px.toFixed(1)} px · 3 px 内 {(result.metrics.within_3px*100).toFixed(0)}%</strong><p>{result.note}</p><a href={`/api/projects/${project.id}/contour-reviews/${result.id}`} download="contour-review.json">下载本次标注与评估 JSON</a></div>}
    <p className="photo-help">检测图 {w} × {h}；青色 ≤ 3 px，黄色 ≤ 6 px，红色 &gt; 6 px。这些是像素显示阈值。</p>
    <div className="review-history">{reviews.filter(r=>r.job_id===shown.id).map(r=><button key={r.id} disabled={busy} onClick={()=>setResult(r)}>{r.label} · {r.metrics.mean_px.toFixed(1)} px</button>)}</div>
  </details>;
}
