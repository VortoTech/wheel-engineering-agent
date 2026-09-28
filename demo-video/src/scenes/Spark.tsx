import {interpolate,useCurrentFrame} from 'remotion';
import {Layout,gold} from '../Layout';
import evidence from '../evidence.json';
const labels:Record<string,string>={reconstruct:'理解 / 造型修正 / 重建',machining:'加工级 STEP',package:'加工包与采样去料',drawing:'SVG 工程图',compare:'工厂 CAD 对照'};
export const Spark=()=>{const frame=useCurrentFrame();return <Layout chapter="05 / VERIFY ON DGX SPARK" title="整条工程链，在 Spark 上完成" subtitle="模型、重建、STEP、加工准备与校验均在 Spark 执行。这里回放的是已完成的运行记录。">
 <div style={{display:'flex',height:'100%',gap:100,alignItems:'center'}}><div style={{width:420}}><div style={{fontSize:130,color:gold,fontWeight:650}}>57.7<span style={{fontSize:44}}> s</span></div><div style={{fontSize:56,marginTop:20}}>5 / 5 步通过</div><p style={{fontSize:27,lineHeight:1.7,color:'#9dafba'}}>M59 · 既有开发样本<br/>实际时长，非实时录屏<br/>步骤显现经过时序压缩</p></div>
 <div style={{flex:1}}>{evidence.chain.steps.map((s,i)=><div key={s.step} style={{opacity:interpolate(frame,[i*45,i*45+15],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'}),display:'flex',alignItems:'center',justifyContent:'space-between',padding:'24px 22px',margin:'10px 0',background:'#1b2932',borderRadius:12,borderLeft:'4px solid #90c4a2',fontSize:34}}><span><span style={{color:'#92d0aa',marginRight:20}}>✓</span>{labels[s.step]}</span><span style={{color:gold}}>{s.seconds===0?'< 0.1':s.seconds} s</span></div>)}</div></div>
 </Layout>};
