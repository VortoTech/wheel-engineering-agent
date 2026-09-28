import {Layout,Picture,gold} from '../Layout';
export const Deliver=()=> <Layout chapter="06 / ENGINEERING DELIVERABLES" title="交付的不只是模型，还有验证依据" subtitle="同次 Spark 运行：加工级 STEP、SVG 图纸、参考加工包。采样未发现新增过切，余量 0.467 L。">
 <div style={{display:'grid',gridTemplateColumns:'1.15fr 1fr',gap:32,height:'100%'}}>{[['drawing.svg','工程图 · SVG'],['simulation.png','采样去料检查 · 非机床碰撞仿真']].map(([src,title])=><div key={src} style={{display:'flex',flexDirection:'column',gap:20}}><div style={{height:480,background:'#fff',borderRadius:18,overflow:'hidden'}}><Picture src={src}/></div><div style={{fontSize:28,color:gold}}>{title}</div></div>)}</div>
 </Layout>;
