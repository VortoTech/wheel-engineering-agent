// Run with: ego-browser nodejs < demo-video/scripts/record-workbench.mjs
// Records real compositor frames and timestamps; no fabricated UI or responses.
const fs = await import('node:fs/promises');
const path = await import('node:path');
const out = path.resolve(process.env.DEMO_CAPTURE_DIR || 'runs/demo-video/continuous');
await fs.mkdir(out + '/frames', {recursive:true});
if (!process.env.DEMO_SPACE_ID) throw new Error('Set DEMO_SPACE_ID to the inspected demo task space');
const task = await taskSpace(Number(process.env.DEMO_SPACE_ID));
const page = task.page('p1');
const frames=[]; const actions=[]; const started=Date.now(); let done=false;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const mark=name=>actions.push({name,seconds:(Date.now()-started)/1000});
await page.cdp('Page.startScreencast',{format:'jpeg',quality:90,maxWidth:1920,maxHeight:1080,everyNthFrame:1});
const pump=(async()=>{while(!done){for(const e of await page.events()){
 if(e.method==='Page.screencastFrame'){
  const file=`frames/${String(frames.length).padStart(6,'0')}.jpg`;
  await fs.writeFile(out+'/'+file,Buffer.from(e.params.data,'base64'));
  frames.push({file,timestamp:e.params.metadata.timestamp});
  await page.cdp('Page.screencastFrameAck',{sessionId:e.params.sessionId});
 }
}await pause(40);}})();
try {
 mark('initial_workbench'); await pause(3500);
 await page.click('loc=role:button[name="正面"]'); mark('front_view'); await pause(3000);
 await page.fill('loc=css:textarea[aria-label="造型修改要求"]','把窗口侧斜面宽度改成3毫米，保持15个外圈盲窗，所有工程尺寸保持不变。');
 mark('request_entered'); await pause(2500);
 await page.click('loc=role:button[name="发送"]'); mark('request_sent');
 await page.waitForFunction(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='发送');return b&&!b.disabled;},undefined,{timeout:120000});
 mark('response_complete'); console.log(await page.snapshot()); await pause(4500);
 await page.click('loc=role:button[name="透视"]'); mark('perspective'); await pause(3500);
 await page.click('loc=role:tab[name="校验"]'); mark('validation'); await pause(4500);
 console.log(await page.snapshot());
} finally {
 await page.cdp('Page.stopScreencast');done=true;await pump;
 await fs.writeFile(out+'/capture.json',JSON.stringify({kind:'continuous_browser_compositor_capture',started,duration:(Date.now()-started)/1000,frames,actions},null,2));
 console.log({frames:frames.length,actions});
}
