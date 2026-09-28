import {interpolate,useCurrentFrame} from 'remotion';
import {Layout,Picture,gold} from '../Layout';
export const Intro=()=>{const f=useCurrentFrame();return <Layout chapter="01 / THE PROBLEM" title="从一张参考图，到可审核的工程初稿" subtitle="不完整的视觉信息 + 已知尺寸 + 工程规则 → 可编辑、可追溯、可验证的 CAD。">
 <div style={{display:'flex',alignItems:'center',height:'100%',gap:80}}>
  <div style={{flex:1}}><div style={{fontSize:94,fontWeight:700,lineHeight:1.16}}>Wheel<br/><span style={{color:gold}}>Engineering Skill</span></div><p style={{fontSize:36,color:'#9dadb7',lineHeight:1.8}}>看懂结构，识别未知。<br/>生成初稿，保留工程师审核。</p></div>
  <div style={{width:590,height:590,background:'#fff',borderRadius:'50%',overflow:'hidden',boxShadow:'0 0 100px #dbb87b18',scale:interpolate(f,[0,300],[.92,1],{extrapolateRight:'clamp'})}}><Picture src="after.png"/></div>
 </div>
 </Layout>};
