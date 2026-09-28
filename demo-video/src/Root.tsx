import {Composition, Sequence, staticFile} from 'remotion';
import {Audio} from '@remotion/media';
import {TransitionSeries} from '@remotion/transitions';
import {Intro} from './scenes/Intro';
import {Understand} from './scenes/Understand';
import {Style} from './scenes/Style';
import {Workbench} from './scenes/Workbench';
import {Spark} from './scenes/Spark';
import {Deliver} from './scenes/Deliver';
import {Closing} from './scenes/Closing';
import voice from './voice.json';
const scenes=[Intro,Understand,Style,Workbench,Spark,Deliver,Closing];
const Demo=()=> <TransitionSeries>{voice.scenes.map((scene,i)=>{
 const Component=scenes[i];
 return <TransitionSeries.Sequence key={scene.id} durationInFrames={scene.frames} name={scene.id}>
  <Component/><Sequence from={15}><Audio src={staticFile(scene.audio)}/></Sequence>
 </TransitionSeries.Sequence>;
})}</TransitionSeries>;
export const RemotionRoot=()=> <Composition id="WheelDemo" component={Demo} durationInFrames={2700} calculateMetadata={()=>({durationInFrames:voice.frames})} fps={30} width={1920} height={1080}/>;
