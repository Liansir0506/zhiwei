// Real local browser checks: training UI copy and exact pending revision package.
const fs=require('fs'),cp=require('child_process'),path=require('path'),net=require('net');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>input+=x);
process.stdin.on('end',()=>main(JSON.parse(input)).catch(e=>{console.error(e.message);process.exitCode=1;}));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(p){
 const root=p.report_dir,profile=path.join(root,'edge-profile'),debug=19350,key=fs.readFileSync(p.key_file,'utf8').trim();
 let browser,ws,id=0;const pending=new Map(),checks=[];
 function call(method,params={}){return new Promise((resolve,reject)=>{const n=++id,timer=setTimeout(()=>reject(Error('CDP timeout')),150000);pending.set(n,{resolve,reject,timer});ws.send(JSON.stringify({id:n,method,params}));});}
 async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('browser evaluation failed');return r.result.value;}
 async function wait(expression,ms=120000){const until=Date.now()+ms;while(Date.now()<until){if(await evaluate(expression))return;await delay(250);}throw Error('browser state wait failed');}
 function check(name,ok){if(!ok)throw Error(name);checks.push({case:name,passed:true});fs.writeFileSync(path.join(root,'browser-checks.json'),JSON.stringify(checks,null,2));}
 async function kind(name){await evaluate(`document.getElementById('businessKind').value=${JSON.stringify(name)};document.getElementById('businessKind').dispatchEvent(new Event('change'))`);await wait("!document.getElementById('businessStatus').textContent.includes('正在')&&document.querySelectorAll('#businessList article').length>0");}
 try{
  await new Promise((resolve,reject)=>{const probe=net.createServer();probe.once('error',()=>reject(Error('browser port occupied')));probe.listen(debug,'127.0.0.1',()=>probe.close(resolve));});
  browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--remote-debugging-port='+debug,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
  let targets;for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${debug}/json/list`)).json();if(targets.length)break;}catch{}await delay(100);}
  if(!targets?.length)throw Error('Edge unavailable');
  ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  ws.onmessage=e=>{const r=JSON.parse(e.data),task=pending.get(r.id);if(task){clearTimeout(task.timer);pending.delete(r.id);r.error?task.reject(Error('CDP failed')):task.resolve(r.result);}};
  await call('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
  ws.addEventListener('message',e=>{const r=JSON.parse(e.data);if(r.method==='Fetch.requestPaused'){const u=r.params.request.url,allowed=u.startsWith(p.url+'/')||u.startsWith('file:')||u.startsWith('about:');call(allowed?'Fetch.continueRequest':'Fetch.failRequest',allowed?{requestId:r.params.requestId}:{requestId:r.params.requestId,errorReason:'BlockedByClient'}).catch(()=>{});}});
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:p.url+'/#local_demo_key='+key});await wait("document.querySelectorAll('#businessList [data-identity]').length===2");
  check('business-list-without-simulation-badges',await evaluate("!document.getElementById('businessList').textContent.includes('模拟快照')&&!document.getElementById('businessStatus').textContent.includes('模拟')&&!document.getElementById('businessScope').textContent.includes('模拟')"));
  check('readonly-actions-and-record-times-retained',await evaluate("document.body.textContent.includes('读取时间')&&document.querySelectorAll('#businessList button').length===2"));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'business-desktop.png'),Buffer.from(x.data,'base64')));
  await evaluate("document.querySelector('#businessList [data-identity=SIM-ALM-000001] button').click()");await wait("document.getElementById('businessDetail').textContent.includes('带入只读诊断')");
  check('associated-detail-without-simulation-badges',await evaluate("!document.getElementById('businessDetail').textContent.includes('模拟快照')&&document.getElementById('businessDetail').textContent.includes('关联')"));
  await evaluate("document.getElementById('businessDiagnose').click()");
  await evaluate("document.getElementById('agentQuery').value='请核对关联工单 SIM-TT-00001 的原文';document.getElementById('agentSubmit').click()");
  await wait("document.getElementById('agentResult').textContent.includes('重新核对原文')");
  check('diagnostic-citation-without-synthetic-label',await evaluate("!Array.from(document.querySelectorAll('.evidence-card p')).some(n=>n.textContent.includes('模拟资料'))"));
  await evaluate("Array.from(document.querySelectorAll('#agentResult button')).find(n=>n.textContent==='重新核对原文').click()");await wait("document.querySelectorAll('.evidence-original').length>0");
  check('actual-original-reference-still-readable',await evaluate("document.querySelector('.evidence-original').textContent.length>30"));
  await evaluate("document.getElementById('businessTab').click()");await kind('integrations');
  check('integration-missing-ledger-without-simulation-column',await evaluate("document.querySelectorAll('#businessList article').length===6&&!document.getElementById('businessList').textContent.includes('模拟来源')&&document.getElementById('businessList').textContent.includes('未同步 / 无账本')"));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:850,deviceScaleFactor:1,mobile:true});
  check('business-mobile-no-overflow',await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'business-mobile.png'),Buffer.from(x.data,'base64')));
  await call('Page.navigate',{url:p.review_url});await wait("document.querySelectorAll('.revision').length===20");
  await evaluate("document.querySelectorAll('details').forEach(n=>n.open=true)");
  check('twenty-revisions-and-forty-nine-contracts-visible',await evaluate("document.querySelectorAll('.revision section').length===49&&document.querySelectorAll('.revision pre').length===20"));
  check('pending-boundary-and-full-body-review-visible',await evaluate("document.getElementById('scope').textContent.includes('未')&&document.getElementById('scope').textContent.includes('49')&&document.querySelector('pre').textContent.includes('逐动作授权')"));
  check('draft-mobile-no-overflow',await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'revision-mobile.png'),Buffer.from(x.data,'base64')));
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'revision-desktop.png'),Buffer.from(x.data,'base64')));
  console.log('Real training/revision browser checks passed: '+checks.length);
 }finally{if(ws&&ws.readyState===1){try{await call('Browser.close');}catch{}ws.close();}if(browser){await delay(1000);if(browser.exitCode===null){browser.kill();await delay(1000);}}}
}
