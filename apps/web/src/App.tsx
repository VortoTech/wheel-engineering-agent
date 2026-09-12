import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, Box, Check, CheckCircle2, ChevronDown, CircleDashed, Clock3, FileImage, FolderOpen, Hexagon, ImagePlus, Info, Layers3, Plus, RefreshCw, Save, SlidersHorizontal, Sparkles, X } from 'lucide-react';
import { api } from './types';
import type { Job, Project, Sources, Spec, Summary } from './types';
import { Viewer } from './Viewer';

type Field = { key: keyof Spec; label: string; min: number; max: number; step: number; unit: string };
const groups: { title: string; fields: Field[] }[] = [
  { title: '轮毂尺寸', fields: [
    { key: 'outer_diameter_mm', label: '实际外径', min: 380, max: 600, step: 1, unit: 'mm' },
    { key: 'width_mm', label: '整体宽度', min: 150, max: 280, step: 1, unit: 'mm' },
    { key: 'rim_wall_mm', label: '轮辋壁厚', min: 6, max: 16, step: .5, unit: 'mm' },
  ] },
  { title: '轮辐造型', fields: [
    { key: 'spoke_count', label: '轮辐数量', min: 5, max: 10, step: 1, unit: '根' },
    { key: 'spoke_width_mm', label: '轮辐宽度', min: 24, max: 52, step: 1, unit: 'mm' },
    { key: 'spoke_thickness_mm', label: '轮辐厚度', min: 12, max: 26, step: 1, unit: 'mm' },
    { key: 'dish_mm', label: '轮辐凹深', min: 12, max: 55, step: 1, unit: 'mm' },
    { key: 'sweep_deg', label: '轮辐偏转', min: -18, max: 18, step: 1, unit: '°' },
  ] },
  { title: '中心盘与孔系', fields: [
    { key: 'hub_diameter_mm', label: '中心盘直径', min: 140, max: 190, step: 1, unit: 'mm' },
    { key: 'center_bore_mm', label: '中心孔径', min: 45, max: 90, step: .1, unit: 'mm' },
    { key: 'bolt_count', label: '安装孔数量', min: 4, max: 6, step: 1, unit: '个' },
    { key: 'bolt_circle_mm', label: '节圆直径 PCD', min: 100, max: 140, step: .1, unit: 'mm' },
    { key: 'bolt_diameter_mm', label: '安装孔径', min: 10, max: 18, step: .5, unit: 'mm' },
  ] },
];
const sourceLabels = { template: '模板假设', manual: '手动输入', drawing: '图纸标注', measurement: '实物测量' };
const checkLabels: Record<string, string> = { valid_brep: '实体拓扑有效', single_solid: '单一连通实体', positive_volume: '有效实体体积', envelope_matches: '外廓尺寸一致', step_roundtrip: 'STEP 导出回读一致' };
const stamp = (value: string) => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });

function NumberField({ field, value, source, onChange, onSource, disabled }: {
  field: Field; value: number; source: Sources[keyof Spec]; onChange: (value: number) => void;
  onSource: (kind: Sources[keyof Spec]['kind']) => void; disabled: boolean;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const numeric = Number(text);
    if (text.trim() && Number.isFinite(numeric) && numeric >= field.min && numeric <= field.max
        && (field.step !== 1 || Number.isInteger(numeric))) onChange(numeric);
    else setText(String(value));
  };
  return <div className="parameter">
    <div className="parameter-top"><label htmlFor={field.key}>{field.label}</label>
      <div className="number-input"><input id={field.key} type="number" min={field.min} max={field.max} step={field.step} value={text} disabled={disabled}
        onChange={(event) => setText(event.target.value)} onBlur={commit} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}/><span>{field.unit}</span></div>
    </div>
    <input className="range" type="range" aria-label={`${field.label}滑块`} min={field.min} max={field.max} step={field.step} value={value} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))}/>
    <select className={`source ${source.kind}`} aria-label={`${field.label}来源`} title={source.note} value={source.kind} disabled={disabled} onChange={(event) => onSource(event.target.value as Sources[keyof Spec]['kind'])}>
      {Object.entries(sourceLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
    </select>
  </div>;
}

export function App() {
  const [list, setList] = useState<Summary[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [spec, setSpec] = useState<Spec | null>(null);
  const [sources, setSources] = useState<Sources | null>(null);
  const [name, setName] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const [tab, setTab] = useState<'parameters' | 'history' | 'checks'>('parameters');
  const uploadInput = useRef<HTMLInputElement>(null);
  const booted = useRef(false);
  const currentProjectId = useRef<string | null>(null);
  currentProjectId.current = project?.id ?? null;

  const applyProject = useCallback((value: Project) => {
    setProject(value); setSpec(value.spec); setSources(value.sources); setName(value.name); setDirty(false);
  }, []);
  const refreshList = async () => setList(await api<Summary[]>('/projects'));
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    (async () => {
      let items = await api<Summary[]>('/projects');
      if (!items.length) {
        const created = await api<Project>('/projects', { method: 'POST', body: JSON.stringify({ name: '轮毂概念 01' }) });
        items = [created];
      }
      setList(items); applyProject(await api<Project>(`/projects/${items[0].id}`));
    })().catch((exc) => setError(`无法连接本地服务：${exc.message}`));
  }, [applyProject]);

  const active = project?.jobs.find((job) => job.status === 'queued' || job.status === 'running');
  const latest = project?.jobs.find((job) => job.status === 'succeeded') ?? null;
  const shown = project?.jobs.find((job) => job.id === selectedJob && job.status === 'succeeded') ?? latest;
  const stale = !!shown && (dirty || JSON.stringify(shown.snapshot.spec) !== JSON.stringify(project?.spec));

  useEffect(() => {
    if (!active || !project) return;
    let cancelled = false;
    const projectId = project.id;
    const interval = window.setInterval(async () => {
      try {
        const fresh = await api<Project>(`/projects/${projectId}`);
        if (cancelled || currentProjectId.current !== projectId) return;
        // Poll only result history: never overwrite unsaved inputs with a job response.
        setProject((previous) => previous?.id === projectId ? { ...previous, jobs: fresh.jobs } : previous);
        const finished = fresh.jobs.find((job) => job.id === active.id);
        if (finished?.status === 'succeeded') { setSelectedJob(finished.id); setNotice('新版本已生成，实体与 STEP 回读检查通过。'); }
        if (finished?.status === 'failed') setError(finished.error || '模型生成失败。');
      } catch (exc) { if (!cancelled) setError((exc as Error).message); }
    }, 1200);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [active?.id, project?.id]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (exc) { setError((exc as Error).message); } finally { setBusy(false); }
  };
  const save = async () => {
    if (!project || !spec || !sources) throw new Error('项目尚未载入。');
    if (!dirty) return project;
    const saved = await api<Project>(`/projects/${project.id}`, { method: 'PUT', body: JSON.stringify({ name, spec, sources, expected_revision: project.revision }) });
    applyProject(saved); await refreshList(); return saved;
  };
  const build = () => run(async () => {
    const saved = await save();
    const job = await api<{ id: string }>(`/projects/${saved.id}/builds`, { method: 'POST', body: JSON.stringify({ expected_revision: saved.revision }) });
    const fresh = await api<Project>(`/projects/${saved.id}`); setProject(fresh); setSelectedJob(null);
    setNotice(`已提交模型生成任务 · ${job.id.slice(0, 6)}`);
  });
  const change = (key: keyof Spec, value: number) => {
    if (spec?.[key] === value) return;
    setSpec((current) => current ? { ...current, [key]: value } : null);
    setSources((current) => current ? { ...current, [key]: { kind: 'manual', note: '用户手动输入，尚未作工程审核' } } : null);
    setDirty(true);
  };
  const uploadFiles = (files: FileList | null) => {
    if (!files?.length) return;
    const captured = Array.from(files);
    void run(async () => {
      const saved = await save();
      for (const file of captured) {
        const body = new FormData(); body.append('file', file);
        const result = await api<Project>(`/projects/${saved.id}/images`, { method: 'POST', body });
        applyProject(result);
      }
      setNotice(`已保存 ${captured.length} 张参考图片。`);
    });
  };

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><Hexagon size={29}/><span>Wheel<span>CAM</span></span><small>STUDIO</small></div><div className="topbar-divider"/><span className="app-title">轮毂建模工作台</span><div className="topbar-right"><span className="local-status"><span className="live-dot"/>本地工作区</span><span className="version-tag">0.1</span></div></header>
    <div className="projectbar">
      <div className="project-identity"><FolderOpen size={19}/><select aria-label="切换项目" value={project?.id ?? ''} disabled={busy || !project} onChange={(event) => { const id = event.target.value; void run(async () => { await save(); applyProject(await api<Project>(`/projects/${id}`)); setSelectedJob(null); }); }}>{list.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronDown size={14}/><button className="icon-button" aria-label="新建轮毂项目" title="新建轮毂项目" disabled={busy || !project} onClick={() => void run(async () => { await save(); const created = await api<Project>('/projects', { method: 'POST', body: JSON.stringify({ name: `轮毂概念 ${String(list.length + 1).padStart(2, '0')}` }) }); applyProject(created); setSelectedJob(null); await refreshList(); })}><Plus size={18}/></button><span className="concept-badge">概念设计</span></div>
      <div className="project-actions"><span className={dirty ? 'save-state dirty' : 'save-state'}>{dirty ? '有未保存修改' : project ? '已保存到本机' : '正在连接'}</span><button className="secondary-button" disabled={!project || busy || !dirty} onClick={() => void run(async () => { await save(); setNotice('参数已保存。'); })}><Save size={15}/>保存参数</button></div>
    </div>
    {(error || notice) && <div className={`message ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}><span>{error || notice}</span>{error && <button onClick={() => window.location.reload()}><RefreshCw size={14}/>重新载入</button>}<button className="message-close" aria-label="关闭提示" onClick={() => { setError(''); setNotice(''); }}><X size={15}/></button></div>}
    <main className="workspace">
      <aside className="references-panel">
        <div className="panel-heading"><span><FileImage size={17}/>设计参考</span><small>{project?.images.length ?? 0} / 20</small></div>
        <div className="reference-content">
          <div className="section-eyebrow">REFERENCE BOARD</div><h1>让造型有据可循</h1><p className="muted">添加正面、斜视或背面图片，结合参考调整模型参数。</p>
          <input ref={uploadInput} type="file" accept="image/png,image/jpeg,image/webp" multiple className="hidden-input" onChange={(event) => { uploadFiles(event.target.files); event.currentTarget.value = ''; }}/>
          <button className="upload-zone" disabled={busy || !project} onClick={() => uploadInput.current?.click()}><ImagePlus size={27} strokeWidth={1.3}/><strong>添加参考图片</strong><span>JPG / PNG / WebP · 每张 ≤ 10 MB</span></button>
          <div className="reference-list">{project?.images.map((image) => <button key={image.id} className={`reference-card ${project.primary_image_id === image.id ? 'primary' : ''}`} disabled={busy} title="设为主参考图" onClick={() => void run(async () => { const saved = await save(); applyProject(await api<Project>(`/projects/${saved.id}/primary/${image.id}`, { method: 'PUT' })); })}><img src={`/api/images/${image.id}`} alt={image.name}/><span>{image.name}</span>{project.primary_image_id === image.id && <em><Check size={12}/>主参考</em>}</button>)}</div>
          {!project?.images.length && <div className="reference-empty"><span>01</span><p>还没有参考图<br/><small>也可以先用模板探索造型</small></p></div>}
          <div className="reference-note"><Info size={16}/><p>当前图片用于人工对照。自动识图尚未接入，模型由右侧参数生成。</p></div>
        </div>
        <div className="workflow-card"><span className="section-eyebrow">当前进度</span><div><span className="step-circle active">1</span><strong>参数化建模</strong></div><div><span className="step-line"/></div><div className="future-step"><span className="step-circle">2</span><span>毛坯与工艺验证</span></div><div><span className="step-line"/></div><div className="future-step"><span className="step-circle">3</span><span>CAM 与机床接入</span></div></div>
      </aside>
      <section className="model-panel">
        <div className="model-toolbar"><div><Box size={17}/><strong>{shown ? '轮毂实体' : '轮毂概念'}</strong><span className="model-version">{shown ? `版本 ${shown.id.slice(0, 6)}` : '尚未生成'}</span></div>{shown && <span className={stale ? 'stale-badge' : 'checked-badge'}>{stale ? '参数已变更 · 显示历史模型' : '几何检查通过'}</span>}</div>
        <Viewer url={shown ? `/api/builds/${shown.id}/glb` : null} building={!!active}/>
        <div className="model-summary"><div><span>实体数量</span><strong>{shown?.report?.solid_count ?? '—'}<small>个</small></strong></div><div><span>外廓尺寸</span><strong className="dimensions">{shown?.report?.bbox_mm.map((value) => value.toFixed(0)).join(' × ') ?? '—'}<small>mm</small></strong></div><div><span>几何体积</span><strong>{shown?.report ? (shown.report.volume_mm3 / 1e6).toFixed(2) : '—'}<small>L</small></strong></div></div>
        <div className="export-bar"><div><span className="section-eyebrow">工程交接</span><p>{shown ? '导出当前显示版本，保留参数来源' : '生成模型后即可导出'}</p></div><div className="export-actions">{(['step', 'glb', 'recipe'] as const).map((type) => <a key={type} className={`export-link ${!shown ? 'disabled' : ''}`} href={shown ? `/api/builds/${shown.id}/${type}` : undefined} aria-disabled={!shown} download><ArrowDownToLine size={15}/>{type === 'recipe' ? '参数 JSON' : type.toUpperCase()}</a>)}</div></div>
        <div className="engineering-note"><CircleDashed size={16}/><span>概念模板 · 轮辋截面、过渡圆角与安装座面待设计；尚未验证载荷、毛坯及加工工艺。</span></div>
      </section>
      <aside className="parameters-panel">
        <div className="panel-tabs" role="tablist" aria-label="模型信息">
          <button role="tab" aria-selected={tab === 'parameters'} onClick={() => setTab('parameters')}><SlidersHorizontal size={15}/>参数</button>
          <button role="tab" aria-selected={tab === 'history'} onClick={() => setTab('history')}><Layers3 size={15}/>版本<span>{project?.jobs.filter((j) => j.status === 'succeeded').length ?? 0}</span></button>
          <button role="tab" aria-selected={tab === 'checks'} onClick={() => setTab('checks')}><CheckCircle2 size={15}/>检查</button>
        </div>
        <div className="parameters-scroll">
          {tab === 'parameters' && <><div className="template-heading"><div><span className="section-eyebrow">TEMPLATE 01</span><h2>周期轮辐</h2></div><span className="template-symbol"><Hexagon size={24}/></span></div><label className="project-name-label" htmlFor="project-name">项目名称</label><input className="project-name" id="project-name" maxLength={80} value={name} disabled={busy} onChange={(event) => { setName(event.target.value); setDirty(true); }}/><p className="dimension-note">尺寸单位为毫米。实际外径不等于轮胎公称配合直径。</p>{spec && sources && groups.map((group) => <fieldset key={group.title} disabled={busy}><legend>{group.title}</legend>{group.fields.map((field) => <NumberField key={field.key} field={field} value={spec[field.key]} source={sources[field.key]} onChange={(value) => change(field.key, value)} onSource={(kind) => { setSources({ ...sources, [field.key]: { kind, note: `${sourceLabels[kind]}，尚未作工程审核` } }); setDirty(true); }} disabled={busy}/>)}</fieldset>)}</>}
          {tab === 'history' && <><div className="tab-intro"><h2>模型版本</h2><p>每次生成都保留独立的参数与文件。</p></div>{!project?.jobs.length && <div className="tab-empty"><Clock3 size={30}/><p>生成第一版模型后<br/>在这里查看历史记录</p></div>}{project?.jobs.map((job: Job) => <div className={`history-card ${job.id === shown?.id ? 'selected' : ''}`} key={job.id}><div><strong>{job.snapshot.name}</strong><span className={`job-status ${job.status}`}>{{queued: '排队中', running: '生成中', succeeded: '已生成', failed: '生成失败'}[job.status]}</span></div><small>{stamp(job.created_at)} · {job.id.slice(0, 6)}</small><p>{job.snapshot.spec.spoke_count} 辐 · 外径 {job.snapshot.spec.outer_diameter_mm} mm</p>{job.error && <p className="job-error">{job.error}</p>}<div className="history-actions">{job.status === 'succeeded' && <button onClick={() => setSelectedJob(job.id)}>查看模型<ArrowRight size={13}/></button>}<button disabled={busy} onClick={() => { setSpec(job.snapshot.spec); setSources(job.snapshot.sources); setDirty(true); setTab('parameters'); setNotice('历史参数已载入，保存或生成后形成新版本。'); }}>载入参数</button></div></div>)}</>}
          {tab === 'checks' && <><div className="tab-intro"><h2>几何检查</h2><p>检查对应当前显示的模型版本。</p></div>{shown?.report ? <><div className="check-status"><CheckCircle2 size={28}/><strong>几何检查通过</strong><span>{shown.id.slice(0, 6)} · {stamp(shown.finished_at || shown.created_at)}</span></div><div className="checks-list">{Object.entries(shown.report.checks).map(([key, valid]) => <div key={key}>{valid ? <Check size={16}/> : <X size={16}/>}<span>{checkLabels[key] || key}</span></div>)}</div><a className="report-link" href={`/api/builds/${shown.id}/report`} download><ArrowDownToLine size={16}/>下载检查报告</a><div className="pending-checks"><h3>待工程确认</h3>{shown.report.limitations.map((item) => <p key={item}><CircleDashed size={14}/>{item}</p>)}</div></> : <div className="tab-empty"><CheckCircle2 size={30}/><p>生成模型后自动检查<br/>实体及 STEP 导出结果</p></div>}</>}
        </div>
        <div className="build-footer"><button className="build-button" disabled={busy || !!active || !project} onClick={() => void build()}>{active ? <span className="spinner"/> : <Sparkles size={18}/>}<span>{active ? '正在生成模型…' : shown ? '生成新版本' : '生成三维模型'}</span>{!active && <ArrowRight size={17}/>}</button><p>生成前保存参数 · 旧版本始终保留</p></div>
      </aside>
    </main>
    <footer className="statusbar"><span><span className="live-dot"/> {project ? '工作区已连接' : '正在连接本地服务'}</span><span>实体建模 <i/> 毫米 <i/> Z 轴为轮毂轴线</span><span>概念设计阶段</span></footer>
  </div>;
}
