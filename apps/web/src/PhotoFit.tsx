import { useRef, useState } from 'react';
import { Check, CircleDot, Crosshair, Eraser, PenLine, Undo2, Wand2 } from 'lucide-react';
import { api } from './types';
import type { ForgedRecipe, Project } from './types';

type Pt = [number, number];
type Mode = 'rim' | 'hub' | 'window';
type FitReport = { family: string; window_iou: number; window_iou_before_refine: number; warnings: string[]; overlay_spokes_px: Pt[][] };

const modeHelp: Record<Mode, string> = {
  rim: '沿轮缘最外沿均匀点 5 个以上的点（越均匀越准）。',
  hub: '点中心盖的中心（凹面越深，这一点越重要）。',
  window: '描出相邻两根辐条之间一组的窗口：单根直辐 1 个窗口；Y 形分叉 2 个（分叉内的 + 组间的）。每个窗口描完点“完成窗口”。',
};

export function PhotoFit({ project, recipe, disabled, onFitted, onError }: {
  project: Project; recipe: ForgedRecipe | null; disabled: boolean;
  onFitted: (recipe: ForgedRecipe, message: string) => void; onError: (message: string) => void;
}) {
  const [mode, setMode] = useState<Mode>('rim');
  const [rim, setRim] = useState<Pt[]>([]);
  const [hub, setHub] = useState<Pt | null>(null);
  const [windows, setWindows] = useState<Pt[][]>([]);
  const [current, setCurrent] = useState<Pt[]>([]);
  const [groups, setGroups] = useState<number>(recipe?.spokes ?? 6);
  const [size, setSize] = useState<[number, number] | null>(null);
  const [report, setReport] = useState<FitReport | null>(null);
  const [fitting, setFitting] = useState(false);
  const svg = useRef<SVGSVGElement>(null);
  const imageId = project.primary_image_id;
  if (!imageId) return <div className="tab-empty"><p>请先在左侧添加参考图片，并设为主参考图。</p></div>;

  const click = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!size || !svg.current || disabled) return;
    const box = svg.current.getBoundingClientRect();
    const pt: Pt = [(event.clientX - box.left) / box.width * size[0], (event.clientY - box.top) / box.height * size[1]];
    setReport(null);
    if (mode === 'rim') setRim((pts) => [...pts, pt]);
    else if (mode === 'hub') setHub(pt);
    else setCurrent((pts) => [...pts, pt]);
  };
  const undo = () => {
    setReport(null);
    if (mode === 'rim') setRim((pts) => pts.slice(0, -1));
    else if (mode === 'hub') setHub(null);
    else if (current.length) setCurrent((pts) => pts.slice(0, -1));
    else setWindows((ws) => ws.slice(0, -1));
  };
  const finishWindow = () => { if (current.length >= 3) { setWindows((ws) => [...ws, current]); setCurrent([]); } };
  const clear = () => { setRim([]); setHub(null); setWindows([]); setCurrent([]); setReport(null); };
  const ready = rim.length >= 5 && !!hub && windows.length >= 1 && !current.length && !!recipe;
  const fit = async () => {
    if (!ready || !recipe) return;
    setFitting(true);
    try {
      const result = await api<{ recipe: ForgedRecipe; report: FitReport }>('/forged/photo-fit', { method: 'POST', body: JSON.stringify({
        rim_points: rim, hub_point: hub, windows, groups, base_recipe: recipe, image_id: imageId }) });
      setReport(result.report);
      const iou = (result.report.window_iou * 100).toFixed(1);
      onFitted(result.recipe, `照片拟合完成：${groups} 组${result.report.family === 'y_split' ? 'Y 形分叉' : '单根直辐'}，窗口吻合度 ${iou}%`
        + (result.report.warnings.length ? `；${result.report.warnings.join(' ')}` : '') + '。请对照绿色轮廓检查，再生成模型。');
    } catch (exc) { onError(`照片拟合失败：${(exc as Error).message}`); } finally { setFitting(false); }
  };
  const poly = (pts: Pt[]) => pts.map((p) => p.join(',')).join(' ');
  const dot = size ? Math.max(size[0], size[1]) / 180 : 4;

  return <div className="photo-fit">
    <div className="photo-fit-toolbar">
      <div className="view-mode" role="group" aria-label="描点模式">
        <button aria-pressed={mode === 'rim'} onClick={() => setMode('rim')}><CircleDot size={14}/>外圈 {rim.length}</button>
        <button aria-pressed={mode === 'hub'} onClick={() => setMode('hub')}><Crosshair size={14}/>中心 {hub ? '✓' : ''}</button>
        <button aria-pressed={mode === 'window'} onClick={() => setMode('window')}><PenLine size={14}/>窗口 {windows.length}</button>
      </div>
      {mode === 'window' && <button className="secondary-button" disabled={current.length < 3} onClick={finishWindow}><Check size={14}/>完成窗口</button>}
      <button className="secondary-button" onClick={undo}><Undo2 size={14}/>撤销</button>
      <button className="secondary-button" onClick={clear}><Eraser size={14}/>清空</button>
      <label className="photo-fit-groups">辐条组数 <input type="number" min={3} max={12} value={groups} onChange={(e) => setGroups(Math.max(3, Math.min(12, Number(e.target.value) || 6)))}/></label>
      <button className="build-button photo-fit-go" disabled={!ready || fitting || disabled} onClick={() => void fit()}><Wand2 size={16}/>{fitting ? '拟合中…' : '拟合配方'}</button>
    </div>
    <p className="dimension-note">{modeHelp[mode]} 外廓尺寸与凹面深度取自当前配方（预设或导入），照片只决定辐条与窗口造型。</p>
    <div className="photo-fit-stage">
      <img src={`/api/images/${imageId}`} alt="主参考图" onLoad={(e) => setSize([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])}/>
      {size && <svg ref={svg} viewBox={`0 0 ${size[0]} ${size[1]}`} preserveAspectRatio="none" onClick={click}>
        {report?.overlay_spokes_px.map((pts, i) => <polygon key={`o${i}`} points={poly(pts)} className="fit-overlay"/>)}
        {windows.map((pts, i) => <polygon key={`w${i}`} points={poly(pts)} className="fit-window"/>)}
        {current.length > 0 && <polyline points={poly(current)} className="fit-current"/>}
        {rim.map((p, i) => <circle key={`r${i}`} cx={p[0]} cy={p[1]} r={dot} className="fit-rim"/>)}
        {hub && <g className="fit-hub"><line x1={hub[0] - dot * 2} y1={hub[1]} x2={hub[0] + dot * 2} y2={hub[1]}/><line x1={hub[0]} y1={hub[1] - dot * 2} x2={hub[0]} y2={hub[1] + dot * 2}/></g>}
      </svg>}
    </div>
    {report && <p className="dimension-note">窗口吻合度 {(report.window_iou * 100).toFixed(1)}%（精修前 {(report.window_iou_before_refine * 100).toFixed(1)}%）· 绿色为拟合后的辐条轮廓投影回照片。吻合度只反映所描窗口，不代表深度、厚度或背面正确。</p>}
  </div>;
}
