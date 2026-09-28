import {Layout,Picture} from '../Layout';
export const Workbench=()=> <Layout chapter="04 / REVIEW & EDIT" title="在同一个工作台，持续对话与修正" subtitle="真实验收：斜面 4 → 5 mm，重建检查通过。旧照片评分自动标记过期，避免版本混用。">
 <div style={{position:'absolute',top:-15,bottom:-10,left:0,right:0,background:'#11171c',border:'1px solid #62727b',borderRadius:16,overflow:'hidden'}}><Picture src="workbench.png" style={{objectFit:'cover',objectPosition:'center'}}/></div>
 <div style={{position:'absolute',right:25,bottom:10,padding:'12px 22px',background:'#101a22ed',border:'1px solid #dbb977',borderRadius:8,fontSize:22}}>独立工作台验收截图 · 模型在 Spark / CAD 在 Mac</div>
 </Layout>;
