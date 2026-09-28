import {Card,Layout,Picture,gold} from '../Layout';
export const Understand=()=> <Layout chapter="02 / UNDERSTAND & REASON" title="先区分：已知、观察、未知" subtitle="照片提供造型线索；工程尺寸来自确认单。看不见的背面、材料和厚度，仍需确认。">
 <div style={{display:'grid',gridTemplateColumns:'520px 1fr',gap:46,height:'100%'}}>
 <div style={{background:'#fff',borderRadius:24,overflow:'hidden'}}><Picture src="front.jpg"/></div>
 <div><Card><div style={{fontSize:25,color:gold}}>M59 · 已提供规格</div><div style={{fontSize:65,margin:'20px 0'}}>20 × 10.5 J　ET15</div><div style={{fontSize:39}}>5 × 112　 /　 CB 66.6　 /　 15×32×60</div></Card>
 <div style={{display:'flex',gap:24,marginTop:26}}><Card style={{flex:1}}><div style={{color:'#9dddba',fontSize:27}}>图像候选</div><p style={{fontSize:43,margin:'18px 0'}}>5 组辐条</p></Card><Card style={{flex:1}}><div style={{color:gold,fontSize:27}}>仍需确认</div><p style={{fontSize:35,margin:'18px 0'}}>背面 · 壁厚 · 材料</p></Card></div>
 </div></div>
 </Layout>;
