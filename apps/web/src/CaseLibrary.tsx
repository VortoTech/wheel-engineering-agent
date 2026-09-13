import { useEffect, useState } from 'react';
import { api } from './types';
import type { Job, Project } from './types';

const labels:Record<string,string>={spoke_style:'辐条类型',spoke_count:'辐条组数',spoke_phase_deg:'整体角度 °',sweep_deg:'偏转 °',spoke_width_hub_mm:'辐根宽 mm',spoke_width_rim_mm:'辐端宽 mm',paired_blade_root_mm:'单臂过渡宽 mm',paired_window_root_mm:'大窗口底部外移 mm',paired_window_blend_mm:'大窗口过渡终点 mm',paired_root_round_mm:'分叉底圆角 mm',paired_gap_flare_mm:'间隙展开 mm',paired_gap_mm:'分叉间隙 mm',paired_tip_width_mm:'单臂末端宽 mm',paired_split_start_mm:'分叉起点 mm',paired_shoulder_mm:'辐根展开 mm',paired_mid_mm:'中段展开 mm',paired_tip_inset_mm:'末端内收 mm',lip_extension_mm:'外缘延伸 mm'};
const display=(v:number|string)=>v==='paired'?'双辐':v==='single'?'单辐':String(v);
type Candidate = {id:string;name:string;kind:string;role:string;image_id:string|null;validation:string;spec:Record<string,number|string>;rank_score:number;reasons:string[];changes:Record<string,number|string>|null;blocked_reason:string|null;same_image:boolean};
type Matches = {base_revision:number;basis:string;candidates:Candidate[]};
export function CaseLibrary({project,shown,disabled,onApply}:{project:Project;shown:Job|null;disabled:boolean;onApply:(id:string,revision:number)=>void}) {
  const [matches,setMatches]=useState<Matches|null>(null), [selected,setSelected]=useState('');
  const [name,setName]=useState(shown?.snapshot.name ?? project.name), [note,setNote]=useState('');
  const [role,setRole]=useState('reference'), [working,setWorking]=useState(false), [message,setMessage]=useState('');
  const [reload,setReload]=useState(0);
  useEffect(()=>{let cancelled=false;setMatches(null);setSelected('');
    api<Matches>(`/projects/${project.id}/case-candidates`).then(value=>{if(!cancelled)setMatches(value);}).catch(e=>{if(!cancelled)setMessage(e.message);});
    return ()=>{cancelled=true;};
  },[project.id,project.revision,reload]);
  const candidate=matches?.candidates.find(c=>c.id===selected);
  const save=async()=>{if(!shown)return;setWorking(true);setMessage('');try{
    await api(`/projects/${project.id}/cases`,{method:'POST',body:JSON.stringify({job_id:shown.id,name,note,role})});
    setMessage('已收录固定版本。几何通过检查，照片吻合度与工程尺寸仍待核验。');setReload(v=>v+1);
  }catch(e){setMessage((e as Error).message);}finally{setWorking(false);}};
  return <section className="case-library">
    <h2>案例与模板</h2><p className="dimension-note">先确认辐条类型和组数，再参考已有造型。案例尺寸不会自动成为实测尺寸。</p>
    {shown && <fieldset disabled={disabled||working}><legend>收录当前展示版本 · {shown.id.slice(0,6)}</legend>
      <label>案例名称<input aria-label="案例名称" value={name} maxLength={80} onChange={e=>setName(e.target.value)}/></label>
      <label>拟合经验<textarea aria-label="拟合经验" value={note} maxLength={1000} onChange={e=>setNote(e.target.value)} placeholder="哪些边界已核对，哪些地方仍有偏差"/></label>
      <label>用途<select value={role} onChange={e=>setRole(e.target.value)}><option value="reference">可复用参考</option><option value="evaluation">评估保留集（不参与推荐）</option></select></label>
      <button className="secondary-button" disabled={!name.trim()} onClick={save}>{working?'收录中…':'收录这个模型版本'}</button>
    </fieldset>}
    {message&&<p role="status">{message}</p>}
    <h3>可选造型</h3><p className="dimension-note">{matches?.basis ?? '读取候选…'}。排序分不是识图准确率。</p>
    {matches?.candidates.map(c=><button key={c.id} className={`case-card ${selected===c.id?'selected':''}`} onClick={()=>setSelected(c.id)} aria-pressed={selected===c.id}>
      {c.image_id&&<img src={`/api/images/${c.image_id}`} alt="案例参考照片"/>}<strong>{c.name}</strong>
      <span>{c.kind==='case'?'已生成案例 · 工程未核验':'内置模板 · 假设参数'} · 排序 {c.rank_score}</span>
      <span>{c.reasons.join(' · ')}{c.same_image?' · 同源图片':''}</span>
    </button>)}
    {candidate&&<div className="case-preview"><h3>套用前预览</h3>
      <p>换算可见造型；保留当前直径、宽度、ET、孔系、轴向厚度与加工准备。生成后需要重新识图和核对。</p>
      {candidate.blocked_reason?<p role="alert">{candidate.blocked_reason}</p>:<>
        <table><thead><tr><th>参数</th><th>当前</th><th>候选</th></tr></thead><tbody>{Object.entries(candidate.changes??{}).map(([k,v])=><tr key={k}><td>{labels[k]??k}</td><td>{display(project.spec[k as keyof typeof project.spec])}</td><td>{display(v)}</td></tr>)}</tbody></table>
        {!Object.keys(candidate.changes??{}).length&&<p>造型参数已经一致。</p>}
        <button className="secondary-button" disabled={disabled||working||!Object.keys(candidate.changes??{}).length} onClick={()=>onApply(candidate.id,matches!.base_revision)}>确认套用造型到草稿</button>
      </>}
    </div>}
    {disabled&&<p className="dimension-note">请先保存草稿，并等待当前操作完成。</p>}
  </section>;
}
