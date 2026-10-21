// Owned headless browser only; no human-held question files or QA requests.
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
    check(await ev(`document.getElementById('status').textContent.includes('冻结题数 0')&&document.getElementById('status').textContent.includes('未运行评测')`),'No frozen questions or evaluation metrics claimed');
    check(await ev(`document.getElementById('policy').textContent.includes('共享现有已准入来源/正文/主题允许')&&document.getElementById('status').textContent.includes('不具备来源独立性')`),'Known-source question-holdout scope explicit');
    check(await ev(`document.getElementById('status').textContent.includes('角色占位')&&document.getElementById('status').textContent.includes('尚未核验')`),'Anonymous roles are not fabricated authenticated reviewers');
    check(await ev(`document.querySelectorAll('button,form,textarea').length===0`),'No freeze/run/self-approval or gold input control');
    const links=await ev(`[...document.querySelectorAll('#actions a')].map(a=>a.href)`);
    check(links.length===7&&links.every(x=>x.startsWith('file:')&&fs.existsSync(url.fileURLToPath(x))),'Seven local handoff artifacts resolve');
    const base=path.dirname(p.reviewFile),template=JSON.parse(fs.readFileSync(path.join(base,'intake-manifest-template.json'),'utf8'));
    check(template.cases.length===0&&template.custody.status==='pending'&&JSON.parse(fs.readFileSync(path.join(base,'source-roster.json'),'utf8')).length===306,'Empty pending intake and 306 current assets');
    await shot('question-holdout-desktop.png');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    check(await ev(`document.documentElement.scrollWidth<=390`),'390px review has no page overflow');await shot('question-holdout-mobile.png');
    check(await ev(`document.getElementById('limits').textContent.includes('截图、聊天')&&document.getElementById('limits').textContent.includes('指标为null')&&document.getElementById('limits').textContent.includes('SOP')`),'Exposure coverage, SOP limits and missing denominator stay visible');
    fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,screenshots:['question-holdout-desktop.png','question-holdout-mobile.png'],headless:true,model_calls:0},null,2));console.log(JSON.stringify({passed:true,checks:checks.length,model_calls:0}));
  }catch(e){fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:false,checks,error:String(e.message)},null,2));process.exitCode=1;
  }finally{if(ws){try{await call('Browser.close');}catch{}ws.close();}if(browser)browser.kill();for(const task of pending.values())clearTimeout(task.timer);}
});
