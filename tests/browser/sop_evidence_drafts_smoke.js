// Offline pending review only. Owned headless Edge; no auth, QA or external pages.
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
    for(let i=0;i<80;i++){if(await ev(`document.querySelectorAll('.draft').length===20&&!!document.getElementById('matches')?.textContent`))break;await pause(100);}
    check(await ev(`document.querySelectorAll('.draft').length===20&&document.getElementById('matches').textContent==='匹配 20 / 20 个专题'`),'Twenty concrete pending guides render');
    check(await ev(`document.querySelectorAll('.draft details li').length===200`),'Two hundred original source identities are linked without runtime aliases');
    check(await ev(`[...document.querySelectorAll('.draft')].every(e=>e.textContent.includes('具体记录项')&&e.textContent.includes('支持记录')&&e.textContent.includes('相反记录')&&e.textContent.includes('pending'))`),'Every guide displays concrete and opposite evidence branches');
    check(await ev(`document.getElementById('scope').textContent.includes('真值/冻结0')&&document.getElementById('limits').textContent.includes('未入库、未STAGED、未发布')`),'Missing human truth and admission scope stay visible');
    check(await ev(`document.querySelectorAll('button,form,textarea').length===0`),'No approval, publication or private evidence upload controls');
    await ev(`document.getElementById('search').value='AMF';document.getElementById('search').dispatchEvent(new Event('input'))`);
    check(await ev(`document.getElementById('matches').textContent==='匹配 1 / 20 个专题'`),'AMF filter selects only its matching guide');
    await ev(`document.querySelector('.draft:not([hidden])').open=true`);await shot('sop-evidence-desktop.png');
    await ev(`document.getElementById('search').value='does-not-exist';document.getElementById('search').dispatchEvent(new Event('input'))`);
    check(await ev(`document.getElementById('matches').textContent==='匹配 0 / 20 个专题'`),'Empty topic filter reports zero explicitly');
    await ev(`document.getElementById('search').value='LTE';document.getElementById('search').dispatchEvent(new Event('input'));document.querySelector('.draft:not([hidden])').open=true`);
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    check(await ev(`document.getElementById('matches').textContent==='匹配 1 / 20 个专题'&&document.documentElement.scrollWidth<=390&&document.body.textContent.includes('LTE家族保留4G声明')`),'390px LTE review preserves scope without horizontal overflow');await shot('sop-evidence-mobile.png');
    const links=await ev(`[...document.querySelectorAll('a')].map(a=>a.href)`);
    check(links.length===23&&links.every(x=>x.startsWith('file:')&&fs.existsSync(url.fileURLToPath(x))),'All twenty bodies and three review artifacts resolve locally');
    fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,headless:true,model_calls:0,qa_requests:0},null,2));console.log(JSON.stringify({passed:true,checks:checks.length,model_calls:0}));
  }catch(e){fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:false,checks,error:String(e.message)},null,2));process.exitCode=1;}
  finally{if(ws){try{await call('Browser.close');}catch{}ws.close();}if(browser)browser.kill();for(const t of pending.values())clearTimeout(t.timer);}
});
