// Static concrete admission review; never approve, submit QA, or load remote pages.
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
  for(let i=0;i<80;i++){if(await ev(`document.querySelectorAll('.entry').length===20`))break;await pause(100);}
  check(await ev(`document.querySelectorAll('.entry').length===20&&document.querySelectorAll('.entry pre').length===20`),'Twenty full bodies render for exact review');
  check(await ev(`[...document.querySelectorAll('.entry')].every(e=>e.textContent.includes('来源身份')&&e.textContent.includes('default租户')&&e.textContent.includes('ops/admin')&&e.textContent.includes('完整项绑定')&&e.textContent.includes('pending'))`),'Every body has identity, role scope and pending binding');
  check(await ev(`document.getElementById('target').textContent.includes('test_sop_evidence_r1 / 13306')&&document.getElementById('target').textContent.includes('Milvus 29530')&&document.getElementById('target').textContent.includes('仅20指南')`),'Concrete new isolated STAGED target and no implicit corpus copy');
  check(await ev(`document.getElementById('limits').textContent.includes('新DeepSeek外发')&&document.getElementById('limits').textContent.includes('未批准发布')&&document.getElementById('scope').textContent.includes('未创建数据库/集合')`),'Outbound/publication/creation exclusions explicit');
  check(await ev(`document.querySelectorAll('button,form,input,textarea').length===0`),'No local auto-approval, upload or execution controls');
  const base=path.dirname(p.reviewFile),plan=JSON.parse(fs.readFileSync(path.join(base,'admission-plan.json'),'utf8')),receipt=JSON.parse(fs.readFileSync(path.join(base,'receipt-template.json'),'utf8'));
  check(receipt.decision==='pending'&&receipt.user_message===null&&receipt.recorded_at_utc===null&&plan.entries.every(e=>e.approval.status==='pending'),'Empty real-decision receipt and twenty pending approvals');
  await ev(`document.querySelector('.entry').open=true`);await shot('admission-desktop.png');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  check(await ev(`document.documentElement.scrollWidth<=390`),'390px review has no horizontal overflow');await shot('admission-mobile.png');
  const links=await ev(`[...document.querySelectorAll('a')].map(a=>a.href)`);
  check(links.length===3&&links.every(x=>x.startsWith('file:')&&fs.existsSync(url.fileURLToPath(x))),'Three concrete local artifacts resolve');
  fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,headless:true,qa_requests:0,paid_calls:0,approval_submitted:false},null,2));console.log(JSON.stringify({passed:true,checks:checks.length,paid_calls:0}));
 }catch(e){fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:false,checks,error:String(e.message)},null,2));process.exitCode=1;}
 finally{if(ws){try{await call('Browser.close');}catch{}ws.close();}if(browser)browser.kill();for(const t of pending.values())clearTimeout(t.timer);}
});
