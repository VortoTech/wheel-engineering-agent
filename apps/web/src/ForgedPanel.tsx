import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, FileUp, Info } from 'lucide-react';
import { api } from './types';
import type { ForgedPreset, ForgedRecipe } from './types';

type Family = ForgedRecipe['family'];
type Field = { key: string; label: string; min: number; max: number; step: number; unit: string; families: Family[]; hint?: string };
// Styling parameters only. Envelope sizes (rim, barrel, lip radii) are interdependent absolute
// dimensions and come from the preset or an imported recipe, not from independent sliders.
const groups: { title: string; fields: Field[] }[] = [
  { title: '轮辐', fields: [
    { key: 'spokes', label: '轮辐组数', min: 3, max: 12, step: 1, unit: '组', families: ['y_split', 'single', 'skeleton'] },
    { key: 'stem_w_hub', label: '根部宽度', min: 20, max: 80, step: 1, unit: 'mm', families: ['y_split', 'single'] },
    { key: 'stem_w_split', label: '分叉处宽度', min: 12, max: 80, step: 1, unit: 'mm', families: ['y_split'] },
    { key: 'stem_w_split', label: '外端宽度', min: 12, max: 80, step: 1, unit: 'mm', families: ['single'] },
    { key: 'split_r', label: '分叉位置（半径）', min: 100, max: 250, step: 1, unit: 'mm', families: ['y_split'] },
    { key: 'arm_angle_deg', label: '分叉张角（单侧）', min: 4, max: 25, step: .5, unit: '°', families: ['y_split'] },
    { key: 'arm_w', label: '支臂宽度', min: 8, max: 40, step: 1, unit: 'mm', families: ['y_split'] },
    { key: 'arm_bow', label: '支臂弯曲', min: 0, max: 12, step: .5, unit: 'mm', families: ['y_split'] },
    { key: 'spoke_sweep_deg', label: '轮辐偏转', min: -15, max: 15, step: .5, unit: '°', families: ['y_split', 'single', 'skeleton'] },
    { key: 'window_fillet', label: '窗口转角圆角', min: 1, max: 20, step: .5, unit: 'mm', families: ['y_split', 'single', 'skeleton'] },
  ] },
  { title: '凹面', fields: [
    { key: 'hub_z', label: '中心盘下沉（距轮缘正面）', min: -120, max: -10, step: 1, unit: 'mm', families: ['y_split', 'single', 'skeleton'] },
    { key: 'concavity_exp', label: '凹面曲线（1 为直线）', min: .8, max: 2.5, step: .05, unit: '', families: ['y_split', 'single', 'skeleton'] },
  ] },
  { title: '加工细节', fields: [
    { key: 'facet_deg', label: '辐条正面斜面角（0 为平面）', min: 0, max: 30, step: .5, unit: '°', families: ['y_split', 'single', 'skeleton'] },
    { key: 'edge_break', label: '窗口棱边倒角（0 为锐边）', min: 0, max: 3, step: .25, unit: 'mm', families: ['y_split', 'single', 'skeleton'] },
    { key: 'groove_depth', label: '辐条凹槽深度', min: 0, max: 6, step: .25, unit: 'mm', families: ['y_split', 'single', 'skeleton'], hint: '凹槽位置来自配方 groove_offsets' },
    { key: 'back_pocket_skin', label: '背腔顶面留量（0 为无背腔）', min: 0, max: 20, step: .5, unit: 'mm', families: ['y_split', 'single', 'skeleton'] },
    { key: 'lip_pockets', label: '外圈槽数量（0 为无）', min: 0, max: 40, step: 1, unit: '个', families: ['y_split', 'single', 'skeleton'] },
  ] },
];
const familyLabels: Record<Family, string> = { y_split: 'Y 形分叉', single: '单根直辐', skeleton: '节点图（网状 / 树状）' };
export const forgedSummary = (recipe?: ForgedRecipe) => recipe
  ? `锻坯 · ${recipe.spokes} 组${familyLabels[recipe.family] ?? recipe.family}` : '锻坯模板';

function Slider({ field, value, onChange, disabled }: { field: Field; value: number; onChange: (v: number) => void; disabled: boolean }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const numeric = Number(text);
    if (text.trim() && Number.isFinite(numeric) && numeric >= field.min && numeric <= field.max
        && (field.step !== 1 || Number.isInteger(numeric))) onChange(numeric);
    else setText(String(value));
  };
  const id = `forged-${field.key}`;
  return <div className="parameter">
    <div className="parameter-top"><label htmlFor={id} title={field.hint}>{field.label}</label>
      <div className="number-input"><input id={id} type="number" min={field.min} max={field.max} step={field.step} value={text} disabled={disabled}
        onChange={(event) => setText(event.target.value)} onBlur={commit} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}/><span>{field.unit}</span></div>
    </div>
    <input className="range" type="range" aria-label={`${field.label}滑块`} min={field.min} max={field.max} step={field.step} value={value} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))}/>
  </div>;
}

export function ForgedPanel({ recipe, onRecipe, disabled, onError }: {
  recipe: ForgedRecipe | null; onRecipe: (recipe: ForgedRecipe, message?: string) => void; disabled: boolean; onError: (message: string) => void;
}) {
  const [presets, setPresets] = useState<ForgedPreset[]>([]);
  const [defaults, setDefaults] = useState<ForgedRecipe | null>(null);
  const importInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    api<{ defaults: ForgedRecipe; presets: ForgedPreset[] }>('/forged/presets').then((data) => {
      setPresets(data.presets); setDefaults(data.defaults);
      if (!recipe && data.presets.length) onRecipe(data.presets[0].recipe);
    }).catch((exc) => onError(`无法读取锻坯预设：${(exc as Error).message}`));
  // Load once; the recipe itself is owned by the parent.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const importRecipe = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text());
      // A build's recipe.json wraps the recipe in its job snapshot; a plain recipe is the object itself.
      const raw = (parsed && typeof parsed === 'object' && parsed.forged) ? parsed.forged : parsed;
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('文件不是配方对象');
      const clean = Object.fromEntries(Object.entries(raw).filter(([key]) => !key.startsWith('_')));
      // Partial recipes override the template defaults, not whatever is currently on screen.
      onRecipe({ ...(defaults ?? {}), ...clean } as ForgedRecipe, `已导入配方 ${file.name}；生成时由服务端校验全部字段。`);
    } catch (exc) { onError(`配方导入失败：${(exc as Error).message}`); }
  };
  const exportRecipe = () => {
    if (!recipe) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(recipe, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'forged-recipe.json'; link.click();
    URL.revokeObjectURL(url);
  };

  if (!recipe) return <div className="tab-empty"><p>正在读取锻坯预设…</p></div>;
  const set = (key: string, value: unknown) => onRecipe({ ...recipe, [key]: value } as ForgedRecipe);
  const skeleton = recipe.skeleton as { nodes?: Record<string, unknown>; edges?: unknown[] } | undefined;
  const grooves = Array.isArray(recipe.groove_offsets) ? recipe.groove_offsets.length : 0;
  return <>
    <div className="template-heading"><div><span className="section-eyebrow">FORGED-BLANK-V1</span><h2>锻坯减材模板</h2></div></div>
    <p className="dimension-note">按加工顺序建模：回转锻坯 → 辐条正面斜面 → 穿透窗口 → 窗口倒角 → 凹槽 → 背腔 → 外圈槽 → 螺栓孔。所有尺寸为设计假设，非实测。</p>
    <div className="parameter"><div className="parameter-top"><label htmlFor="forged-preset">从预设开始</label>
      <select id="forged-preset" value="" disabled={disabled} onChange={(event) => { const preset = presets.find((item) => item.id === event.target.value); if (preset) onRecipe(preset.recipe, `已载入预设：${preset.name}`); }}>
        <option value="" disabled>选择预设…</option>
        {presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
      </select></div></div>
    <div className="parameter"><div className="parameter-top"><label htmlFor="forged-family">轮辐结构</label>
      <select id="forged-family" value={recipe.family} disabled={disabled} onChange={(event) => set('family', event.target.value)}>
        <option value="y_split">{familyLabels.y_split}</option>
        <option value="single">{familyLabels.single}</option>
        <option value="skeleton" disabled={!skeleton?.nodes}>{skeleton?.nodes ? familyLabels.skeleton : `${familyLabels.skeleton}（需导入配方）`}</option>
      </select></div></div>
    {recipe.family === 'skeleton' && <div className="reference-note"><Info size={16}/><p>节点图来自配方：{Object.keys(skeleton?.nodes ?? {}).length} 个节点 · {skeleton?.edges?.length ?? 0} 条边。修改节点请编辑配方 JSON 后重新导入。</p></div>}
    {groups.map((group) => <fieldset key={group.title} disabled={disabled}><legend>{group.title}</legend>
      {group.fields.filter((field) => field.families.includes(recipe.family)).map((field) =>
        <Slider key={`${field.key}-${field.label}`} field={field} value={Number(recipe[field.key] ?? field.min)} onChange={(value) => set(field.key, value)} disabled={disabled}/>)}
      {group.title === '加工细节' && !grooves && <p className="dimension-note">当前配方没有凹槽位置（groove_offsets 为空），凹槽深度不起作用。</p>}
    </fieldset>)}
    <div className="forged-recipe-actions">
      <input ref={importInput} type="file" accept="application/json,.json" className="hidden-input" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importRecipe(file); event.currentTarget.value = ''; }}/>
      <button className="secondary-button" disabled={disabled} onClick={() => importInput.current?.click()}><FileUp size={15}/>导入配方 JSON</button>
      <button className="secondary-button" onClick={exportRecipe}><ArrowDownToLine size={15}/>导出当前配方</button>
    </div>
    <p className="dimension-note">轮辋、轮缘、中心孔等外廓尺寸来自预设或导入的配方，面板不单独调整，避免尺寸之间互相冲突。生成约需 3–8 分钟。</p>
  </>;
}
