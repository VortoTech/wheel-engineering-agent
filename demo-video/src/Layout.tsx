import React from 'react';
import {AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';
export const gold='#ddb977';
export const Layout: React.FC<{chapter:string;title:string;subtitle:string;children:React.ReactNode}> = ({chapter,title,children}) => {
 const frame=useCurrentFrame();
 return <AbsoluteFill style={{background:'radial-gradient(ellipse at 80% 10%, #26323b 0%, #11191f 45%, #0b1015 100%)',color:'#f6f4ed',fontFamily:'"PingFang SC", "Microsoft YaHei", sans-serif'}}>
  <div style={{position:'absolute',inset:0,backgroundImage:'linear-gradient(#ffffff04 1px, transparent 1px),linear-gradient(90deg,#ffffff04 1px,transparent 1px)',backgroundSize:'72px 72px'}}/>
  <div style={{position:'absolute',left:90,top:48,fontSize:32,fontWeight:700}}>Wheel<span style={{color:gold}}>CAM</span><span style={{fontSize:21,marginLeft:28,color:'#92a2ac',fontWeight:400,letterSpacing:3}}>ENGINEERING RECONSTRUCTION AGENT</span></div>
  <div style={{position:'absolute',right:90,top:53,color:gold,fontSize:23}}>真实产物演示 · 中文配音</div>
  <div style={{position:'absolute',left:90,top:125,fontSize:23,color:gold,letterSpacing:5}}>{chapter}</div>
  <div style={{position:'absolute',left:88,top:167,fontSize:76,fontWeight:650,letterSpacing:-2,opacity:interpolate(frame,[0,16],[0,1],{extrapolateRight:'clamp'}),translate:`0 ${interpolate(frame,[0,20],[20,0],{extrapolateRight:'clamp'})}px`}}>{title}</div>
  <div style={{position:'absolute',left:90,right:90,top:285,bottom:100}}>{children}</div>
 </AbsoluteFill>
};
export const Picture:React.FC<{src:string;style?:React.CSSProperties}>=({src,style})=><Img src={staticFile(src)} style={{width:'100%',height:'100%',objectFit:'contain',...style}}/>;
export const Card:React.FC<{children:React.ReactNode;style?:React.CSSProperties}>=({children,style})=><div style={{border:'1px solid #ffffff20',borderRadius:24,background:'#1a242d',padding:32,...style}}>{children}</div>;
