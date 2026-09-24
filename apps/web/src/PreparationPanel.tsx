import { useEffect, useState } from 'react';
import type { Caliper, Material, Preparation, PreparationReport, Source, Stock } from './types';

const sourceNames = { template: '模板假设', manual: '手动输入', drawing: '图纸标注', measurement: '实物测量',
  observed: 'Agent 观测', inferred: 'Agent 推断', unknown: '未知' };
const assumed: Source = { kind: 'template', note: '演示假设，请以实际资料替换；未作工程审核' };
const defaultCaliper: Caliper = { inner_radius_mm: 90, outer_radius_mm: 180, z_min_mm: -70, z_max_mm: 10, required_clearance_mm: 3, source: assumed };
const defaultStock: Stock = { outer_diameter_mm: 520, height_mm: 280, center_z_mm: 0, cavity_diameter_mm: 390, front_web_mm: 120, required_allowance_mm: 1, source: assumed };
const defaultMaterial: Material = { name: '铝材（演示密度，待确认牌号）', density_kg_m3: 2700, source: assumed };

type NumericField = { key: string; label: string; min: number; max: number; unit?: string };
const caliperFields: NumericField[] = [
  { key: 'inner_radius_mm', label: '内半径', min: 1, max: 400 },
  { key: 'outer_radius_mm', label: '外半径', min: 2, max: 450 },
  { key: 'z_min_mm', label: '轴向起点（相对安装面）', min: -500, max: 500 },
  { key: 'z_max_mm', label: '轴向终点（相对安装面）', min: -500, max: 500 },
  { key: 'required_clearance_mm', label: '要求最小间隙', min: 0, max: 30 },
];
const stockFields: NumericField[] = [
  { key: 'outer_diameter_mm', label: '锻坯外径', min: 100, max: 1000 },
  { key: 'height_mm', label: '总高', min: 20, max: 600 },
  { key: 'center_z_mm', label: '中心 Z（相对轮辋中面）', min: -500, max: 500 },
  { key: 'cavity_diameter_mm', label: '内腔直径（0 为实心）', min: 0, max: 990 },
  { key: 'front_web_mm', label: '外侧底厚', min: 1, max: 600 },
  { key: 'required_allowance_mm', label: '要求最小余量', min: 0, max: 20 },
];

function Numeric({ id, field, value, onChange }: { id: string; field: NumericField; value: number; onChange: (value: number) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const parsed = Number(text);
    if (text.trim() && Number.isFinite(parsed) && parsed >= field.min && parsed <= field.max) onChange(parsed);
    else setText(String(value));
  };
  return <label className="prep-number" htmlFor={id}><span>{field.label}</span><span><input id={id} type="number" step="any" min={field.min} max={field.max} value={text} onChange={e => setText(e.target.value)} onBlur={commit} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur(); }}/><small>{field.unit ?? 'mm'}</small></span></label>;
}

function SourceEditor({ label, value, onChange }: { label: string; value: Source; onChange: (value: Source) => void }) {
  return <div className="prep-source"><label>{label}来源<select value={value.kind} onChange={e => onChange({ ...value, kind: e.target.value as Source['kind'] })}>{Object.entries(sourceNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label><label>依据备注<input maxLength={200} value={value.note} onChange={e => onChange({ ...value, note: e.target.value })}/></label></div>;
}

export function PreparationPanel({ value, onChange, disabled }: { value: Preparation; onChange: (value: Preparation) => void; disabled: boolean }) {
  function updateNumbers(group: 'caliper' | 'stock', key: string, numeric: number) {
    const previous = value[group];
    if (!previous) return;
    onChange({ ...value, [group]: { ...previous, [key]: numeric, source: { kind: 'manual', note: '本组含手动修改，其余值可能仍为演示假设；待工程确认' } } });
  }
  return <div className="preparation-panel"><div className="tab-intro"><h2>加工准备输入</h2><p>随新模型保存并检查。启用后先填入演示假设；关闭即不检查该项。</p></div>
    <fieldset disabled={disabled}><legend>卡钳包络</legend><label className="prep-toggle"><input type="checkbox" checked={!!value.caliper} onChange={e => onChange({ ...value, caliper: e.target.checked ? structuredClone(defaultCaliper) : null })}/>启用卡钳检查</label>
      {value.caliper && <><p className="muted">径向与轴向范围绕 Z 轴旋转整圈，覆盖车轮旋转。轴向 + 值朝轮毂外侧；采用保守包络，不含配重和气门嘴。</p>{caliperFields.map(field => <Numeric key={field.key} id={`caliper-${field.key}`} field={field} value={Number(value.caliper![field.key as keyof Caliper])} onChange={v => updateNumbers('caliper', field.key, v)}/>)}<SourceEditor label="卡钳" value={value.caliper.source} onChange={source => onChange({ ...value, caliper: { ...value.caliper!, source } })}/></>}
    </fieldset>
    <fieldset disabled={disabled}><legend>锻坯规格</legend><label className="prep-toggle"><input type="checkbox" checked={!!value.stock} onChange={e => onChange({ ...value, stock: e.target.checked ? structuredClone(defaultStock) : null })}/>启用锻坯检查</label>
      {value.stock && <><p className="muted">同轴圆柱或杯形锻坯，内腔向内侧（-Z）开口。检查完整实体包含关系及所有边界的最小余量。</p>{stockFields.map(field => <Numeric key={field.key} id={`stock-${field.key}`} field={field} value={Number(value.stock![field.key as keyof Stock])} onChange={v => updateNumbers('stock', field.key, v)}/>)}<SourceEditor label="锻坯" value={value.stock.source} onChange={source => onChange({ ...value, stock: { ...value.stock!, source } })}/></>}
    </fieldset>
    <fieldset disabled={disabled}><legend>材料与重量</legend><label className="prep-toggle"><input type="checkbox" checked={!!value.material} onChange={e => onChange({ ...value, material: e.target.checked ? structuredClone(defaultMaterial) : null })}/>启用重量估算</label>
      {value.material && <><label className="prep-text">材料名称<input maxLength={100} value={value.material.name} onChange={e => onChange({ ...value, material: { ...value.material!, name: e.target.value, source: { kind: 'manual', note: '材料资料待工程确认' } } })}/></label><Numeric id="material-density" field={{ key: 'density', label: '材料密度', min: 0.001, max: 30000, unit: 'kg/m³' }} value={value.material.density_kg_m3} onChange={density_kg_m3 => onChange({ ...value, material: { ...value.material!, density_kg_m3, source: { kind: 'manual', note: '材料密度待工程确认' } } })}/><SourceEditor label="材料" value={value.material.source} onChange={source => onChange({ ...value, material: { ...value.material!, source } })}/></>}
    </fieldset>
  </div>;
}

const statusNames: Record<string, string> = { not_checked: '未检查', clear: '包络间隙满足输入要求', interference: '包络干涉', insufficient_clearance: '间隙不足或接触', missing_material: '锻坯缺料', insufficient_allowance: '包含成品，但余量不足', contained: '包含成品且余量满足输入要求', estimated: '估算完成' };
const format = (n: number | null | undefined, unit: string) => n == null ? '—' : `${n.toLocaleString('zh-CN', { maximumFractionDigits: 4 })} ${unit}`;
function InputNote({ source }: { source?: Source }) { return source ? <p className="prep-input-note">{sourceNames[source.kind]} · {source.note}</p> : null; }
export function PreparationResults({ report }: { report?: PreparationReport }) {
  if (!report) return <div className="prep-result"><h3>加工准备</h3><p>此历史版本尚无加工准备报告。</p></div>;
  const { caliper, stock, weight } = report;
  return <div className="preparation-results">
    <section className={`prep-result ${caliper.status}`}><h3>卡钳检查</h3><strong>{statusNames[caliper.status]}</strong><InputNote source={caliper.input?.source}/>{caliper.input && <><p>整圈保守包络最小间隙：{format(caliper.minimum_clearance_mm, 'mm')}<br/>要求：{format(caliper.required_clearance_mm, 'mm')}<br/>交叠体积：{format(caliper.overlap_mm3, 'mm³')}</p><small>结果仅覆盖输入包络，未验证整车适配。</small></>}</section>
    <section className={`prep-result ${stock.status}`}><h3>锻坯检查</h3><strong>{statusNames[stock.status]}</strong><InputNote source={stock.input?.source}/>{stock.input && <><p>缺料体积：{format(stock.missing_volume_mm3, 'mm³')}<br/>最小余量：{format(stock.minimum_allowance_mm, 'mm')}（要求 {format(stock.required_allowance_mm, 'mm')}）<br/>材料去除率：{format(stock.removal_percent, '%')}</p><small>缺料时不计算去除率；包含关系不等于工艺验证。</small></>}</section>
    <section className="prep-result"><h3>重量估算</h3><strong>{weight.status === 'not_checked' ? '未估算 · 缺少材料密度' : 'CAD 净重估算'}</strong><InputNote source={weight.input?.source}/>{weight.input && <><p>成品：{format(weight.finished_kg, 'kg')}<br/>锻坯：{format(weight.stock_kg, 'kg')}<br/>去除材料：{format(weight.removed_kg, 'kg')}</p><small>按同一均匀密度计算，不含附件与涂层。</small></>}</section>
  </div>;
}
