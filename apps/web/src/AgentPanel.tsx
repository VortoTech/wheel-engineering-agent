import { useEffect, useState } from 'react';
import { AlertTriangle, Bot, Check, CircleDashed, Play, Sparkles } from 'lucide-react';
import { api } from './types';
import type { AgentCadPlan, AgentProposal, AgentStatus, Project } from './types';

const operationLabels = {
  set_parameter: '修改参数', replace_sketch: '替换草图', mark_unknown: '标记未知', request_measurement: '请求测量', request_tool: '请求本地工具',
};

export function AgentPanel({ project, disabled, onProject }: {
  project: Project; disabled: boolean; onProject: (project: Project, notice: string) => void;
}) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [goal, setGoal] = useState('根据当前参考图和已有参数，改进轮辐造型；看不见的尺寸保持 UNKNOWN。');
  const [proposal, setProposal] = useState<AgentProposal | null>(null);
  const [includeImage, setIncludeImage] = useState(false);
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setProposal(null); setApproved(new Set()); setError('');
    api<AgentStatus>('/agent-cad/status').then(setStatus).catch(exc => setError(exc.message));
  }, [project.id, project.revision]);

  const propose = async () => {
    setWorking(true); setError('');
    try {
      const result = await api<AgentProposal>(`/projects/${project.id}/agent-cad/propose`, {
        method: 'POST', body: JSON.stringify({expected_revision: project.revision, goal, include_primary_image: includeImage}),
      });
      setProposal(result); setApproved(new Set());
    } catch (exc) { setError((exc as Error).message); } finally { setWorking(false); }
  };

  const execute = async (buildAfter: boolean) => {
    if (!proposal) return;
    setWorking(true); setError('');
    try {
      const applied = await api<{project:Project}>(`/projects/${project.id}/agent-cad/apply`, {
        method: 'POST', body: JSON.stringify({plan: proposal.plan, approved_action_ids: [...approved]}),
      });
      let updated = applied.project;
      let notice = 'Agent 计划已应用为新的参数草稿。';
      if (buildAfter) {
        await api(`/projects/${project.id}/builds`, {
          method: 'POST', body: JSON.stringify({expected_revision: updated.revision}),
        });
        updated = await api<Project>(`/projects/${project.id}`);
        notice = 'Agent 修改已应用，并提交 CAD 构建与验证。';
      }
      onProject(updated, notice); setProposal(null); setApproved(new Set());
    } catch (exc) { setError((exc as Error).message); } finally { setWorking(false); }
  };

  const pending = proposal?.preview.pending_approval_action_ids ?? [];
  const allApproved = pending.every(id => approved.has(id));
  return <div className="agent-panel">
    <div className="tab-intro"><h2><Bot size={18}/>Engineering Agent</h2><p>Agent 可以修改参数和受控草图；几何内核与审批门仍是最终约束。</p></div>
    {!status?.configured ? <div className="agent-offline"><CircleDashed size={20}/><div><strong>AI 模型尚未连接</strong><p>{status?.reason || '正在读取 Agent 配置。'}</p><code>WHEELCAM_AGENT_BASE_URL</code><code>WHEELCAM_AGENT_MODEL</code></div></div> : <>
      <div className="agent-provider"><span className="live-dot"/><strong>{status.model}</strong><small>{status.mode === 'live_provider' ? '实时模型' : status.mode}</small></div>
      <label className="agent-goal">本轮建模目标<textarea value={goal} maxLength={1000} disabled={disabled || working} onChange={event => setGoal(event.target.value)}/></label>
      {project.primary_image_id && status.supports_primary_image && <label className="agent-image-consent"><input type="checkbox" checked={includeImage} disabled={disabled || working} onChange={event => setIncludeImage(event.target.checked)}/><span>把当前主参考图发送给已配置的模型 provider</span></label>}
      <button className="agent-propose" disabled={disabled || working || goal.trim().length < 3} onClick={() => void propose()}><Sparkles size={15}/>{working ? 'Agent 工作中…' : '让 Agent 分析并提出修改'}</button>
    </>}
    {error && <p className="agent-error"><AlertTriangle size={14}/>{error}</p>}
    {proposal && <div className="agent-proposal">
      <div className="agent-proposal-title"><strong>修改计划</strong><span>基于 revision {proposal.plan.base_revision}</span></div>
      {proposal.tool_trace?.map((entry, index) => <details key={index}><summary>已执行：{entry.tool}</summary><pre>{JSON.stringify(entry.result, null, 2)}</pre></details>)}
      {proposal.plan.actions.map(action => {
        const needsApproval = pending.includes(action.id);
        return <article className="agent-action" key={action.id}>
          <div><b>{operationLabels[action.operation]}</b><code>{action.target}</code></div>
          <p>{action.rationale}</p>
          {action.value !== undefined && <pre>{JSON.stringify(action.value, null, 2)}</pre>}
          {action.question && <p className="agent-question">需要补充：{action.question}</p>}
          {action.source && <small>{action.source} · 置信度 {Math.round((action.confidence ?? 0) * 100)}%</small>}
          {needsApproval && <label className="agent-approval"><input type="checkbox" checked={approved.has(action.id)} onChange={event => setApproved(current => { const next = new Set(current); event.target.checked ? next.add(action.id) : next.delete(action.id); return next; })}/><span>批准这项关键修改</span></label>}
        </article>;
      })}
      {proposal.preview.measurement_requests.length > 0 && <div className="agent-measurements"><strong>需要补充的信息</strong>{proposal.preview.measurement_requests.map(item => <p key={item.action_id}>{item.question}</p>)}</div>}
      {proposal.preview.tool_requests.length > 0 && <div className="agent-measurements"><strong>建议执行的受控工具</strong>{proposal.preview.tool_requests.map(item => <p key={item.action_id}><code>{item.tool}</code> · {item.rationale}</p>)}</div>}
      <div className="agent-actions"><button disabled={working || !allApproved} onClick={() => void execute(false)}><Check size={14}/>应用为草稿</button><button disabled={working || !allApproved || !proposal.preview.can_build} onClick={() => void execute(true)}><Play size={14}/>应用并生成 CAD</button></div>
      {!proposal.preview.can_build && <p className="agent-boundary">存在待补充测量或待执行工具，本轮可以保存草稿，但不会自动宣称输入完整。</p>}
      <div className="agent-limitations">{proposal.preview.limitations.map(item => <p key={item}>{item}</p>)}</div>
    </div>}
  </div>;
}
