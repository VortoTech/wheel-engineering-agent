import type { BuildResolution, VolumeMeasurement, VolumeMethod } from './types';

const names: Record<string, string> = {
  hub_junction: '中心盘连接圆角', rim_junction: '轮辋连接圆角',
  window_edges: '窗口棱边', spoke_ridges: '辐条凸脊', spoke_edges: '辐条棱边',
  back_pockets: '背腔截面深度', window_side_draft: '窗口实际拔模角',
};
const outcomes = {exact: '按请求生成', adjusted: '已调整', partial: '部分生成', unverified: '尚未核实'};

function VolumeSummary({measurement, stepMethod}: {measurement?: VolumeMeasurement; stepMethod?: VolumeMethod}) {
  const currentMethod = (value?: VolumeMethod) => value?.schema_version === 'wheel-volume-measurement-v1'
    && value.method === 'BRepGProp.VolumePropertiesGK_s'
    && Number.isFinite(value.requested_epsilon) && value.requested_epsilon > 0;
  if (!currentMethod(measurement) || !currentMethod(stepMethod)) return <div className="prep-result" role="status">
    <h3>体积记录尚未复核</h3>
    <p>历史报告缺少当前体积积分或 STEP 回读方法记录；体积、重量和去料比例需重新构建后复核，不能视为已确认。</p>
    <p>这不表示 B-Rep 自动失效，也不是工程或制造批准。</p>
  </div>;
  return <div className="prep-result">
    <h3>已记录体积计算方法</h3>
    <p>Gauss–Kronrod 积分；模型计算 ε = {measurement!.requested_epsilon.toExponential()}，
      STEP 回读计算 ε = {stepMethod!.requested_epsilon.toExponential()}。</p>
    <p>ε 是数值计算设置，不是尺寸公差；内核误差估计不代表独立误差认证、工程精度或制造批准。</p>
  </div>;
}

export function BuildResolutionSummary({resolution, volumeMeasurement, stepVolumeMethod}: {
  resolution?: BuildResolution; volumeMeasurement?: VolumeMeasurement; stepVolumeMethod?: VolumeMethod;
}) {
  return <>
    {!resolution ? <div className="prep-result"><h3>请求与实际构造</h3>
      <p>旧版本尚无逐特征构造记录，不能视为按请求完整生成。</p></div>
    : <div className="prep-result" role="status">
    <h3>{resolution.status === 'degraded' ? '降级构建 · 需要复核' : resolution.status === 'unverified'
      ? '构造结果尚未核实' : '已记录特征按请求生成'}</h3>
    <p>几何检查与设计要求分别判断；此状态不是工程或制造批准。</p>
    {resolution.feature_results.filter(item => item.status !== 'exact').map(item => <p key={item.feature}>
      {names[item.feature] || item.feature}：{outcomes[item.status]}；请求 {JSON.stringify(item.requested)}，
      实际 {item.applied === null ? '未知' : JSON.stringify(item.applied)}
      {item.details.edges_total !== undefined && <>；已倒圆 {String(item.details.edges_rounded ?? '?')}/{String(item.details.edges_total ?? '?')} 条边</>}
    </p>)}
    </div>}
    <VolumeSummary measurement={volumeMeasurement} stepMethod={stepVolumeMethod}/>
  </>;
}
