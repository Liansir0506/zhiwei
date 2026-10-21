// Headless local review only; never opens private holdout files or calls the QA service.
const fs=require('fs'),path=require('path'),cp=require('child_process'),url=require('url');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),checks=[],pending=new Map();let browser,ws,seq=0;
  const profile=path.join(p.reportDir,'edge-profile');fs.mkdirSync(profile,{recursive:true});
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout'));},15000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true});if(r.exceptionDetails)throw new Error('Page expression failed');return r.result.value;};
  const check=(ok,label)=>{if(!ok)throw new Error(label);checks.push(label);};
  const shot=async name=>fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));
  try{
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
    let port;for(let i=0;i<150;i++){try{port=Number(fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]);if(port)break;}catch{}await pause(100);}if(!port)throw new Error('Owned browser did not start');
    const pages=await(await fetch('http://127.0.0.1:'+port+'/json')).json();ws=new WebSocket(pages.find(x=>x.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});ws.onmessage=e=>{const d=JSON.parse(e.data),t=pending.get(d.id);if(t){clearTimeout(t.timer);pending.delete(d.id);d.error?t.reject(new Error('CDP error')):t.resolve(d.result);}};
    await call('Page.enable');await call('Runtime.enable');await call('Emulation.setDeviceMetricsOverride',{width:1365,height:980,deviceScaleFactor:1,mobile:false});await call('Page.navigate',{url:url.pathToFileURL(p.reviewFile).href});
    for(let i=0;i<80;i++){if(await ev(`!!document.getElementById('status')`))break;await pause(100);}
    check(await ev(`document.getElementById('status').textContent.includes('独立冻结分母 0')&&document.getElementById('status').textContent.includes('未执行评测')`),'Zero frozen denominator and no evaluation are explicit');
    check(await ev(`document.querySelectorAll('button,form,textarea').length===0`),'No freeze/run or question-writing action is available');
    const links=await ev(`[...document.querySelectorAll('#actions a')].map(a=>a.href)`);
    check(links.length===6&&links.every(x=>x.startsWith('file:')&&fs.existsSync(url.fileURLToPath(x))),'Six local human handoff artifacts resolve');
    const template=JSON.parse(fs.readFileSync(path.join(path.dirname(p.reviewFile),'intake-manifest-template.json'),'utf8'));
    check(template.cases.length===0&&template.custody.status==='pending','Template has no new questions or approved custody');
    const registry=JSON.parse(fs.readFileSync(path.join(path.dirname(p.reviewFile),'exposure-registry.json'),'utf8'));
    check(registry.records.every(r=>!('query' in r)&&!('question' in r)&&r.developer_exposed),'Exposure records contain hash commitments, not hidden question text');
    await ev(`document.getElementById('coverage').open=true`);check(await ev(`document.getElementById('coverage').textContent.includes('截图、聊天记录')`),'Incomplete exposure coverage remains visible');
    await shot('independent-desktop.png');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    check(await ev(`document.documentElement.scrollWidth<=390`),'390px review has no page overflow');
    await shot('independent-mobile.png');
    await ev(`document.getElementById('groups').open=true;document.getElementById('inputs').open=true`);
    check(await ev(`document.documentElement.scrollWidth<=390&&document.querySelectorAll('#inputs td').length>0&&document.querySelectorAll('#groups td').length>0`),'Long hashes and group/input tables stay inside mobile scroll containers');
    fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,screenshots:['independent-desktop.png','independent-mobile.png'],headless:true,model_calls:0},null,2));console.log(JSON.stringify({passed:true,checks:checks.length,model_calls:0}));
  }catch(e){fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:false,checks,error:String(e.message)},null,2));process.exitCode=1;
  }finally{if(ws){try{await call('Browser.close');}catch{}ws.close();}if(browser)browser.kill();for(const task of pending.values())clearTimeout(task.timer);}
});
