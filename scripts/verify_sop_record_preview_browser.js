// Real headless Edge checks against the authenticated live guide preview.
const fs=require('fs'),cp=require('child_process'),path=require('path'),net=require('net');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>input+=x);
process.stdin.on('end',()=>main(JSON.parse(input)).catch(e=>{console.error(e.message);process.exitCode=1;}));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(p){
 const root=p.report_dir,profile=path.join(root,'edge-profile'),debug=19352;
 const key=fs.readFileSync(p.key_file,'utf8').trim();let browser,ws,id=0;const pending=new Map(),checks=[];
 function call(method,params={}){return new Promise((resolve,reject)=>{const number=++id,timer=setTimeout(()=>reject(Error('CDP timeout')),90000);pending.set(number,{resolve,reject,timer});ws.send(JSON.stringify({id:number,method,params}));});}
 async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('browser evaluation failed');return r.result.value;}
 async function wait(expression,ms=90000){const until=Date.now()+ms;while(Date.now()<until){if(await evaluate(expression))return;await delay(250);}throw Error('browser state wait failed');}
 function check(name,ok){if(!ok)throw Error(name);checks.push({case:name,passed:true});fs.writeFileSync(path.join(root,'browser-checks.json'),JSON.stringify(checks,null,2));}
 try{
  await new Promise((resolve,reject)=>{const probe=net.createServer();probe.once('error',()=>reject(Error('owned browser port occupied')));probe.listen(debug,'127.0.0.1',()=>probe.close(resolve));});
  browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--remote-debugging-port='+debug,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
  let targets;for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${debug}/json/list`)).json();if(targets.length)break;}catch{}await delay(100);}
  if(!targets?.length)throw Error('headless Edge unavailable');
  ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  ws.onmessage=e=>{const r=JSON.parse(e.data),task=pending.get(r.id);if(task){clearTimeout(task.timer);pending.delete(r.id);r.error?task.reject(Error('CDP failed')):task.resolve(r.result);}};
  await call('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
  ws.addEventListener('message',e=>{const r=JSON.parse(e.data);if(r.method==='Fetch.requestPaused'){const allowed=r.params.request.url.startsWith(p.url+'/')||r.params.request.url.startsWith('about:');call(allowed?'Fetch.continueRequest':'Fetch.failRequest',allowed?{requestId:r.params.requestId}:{requestId:r.params.requestId,errorReason:'BlockedByClient'}).catch(()=>{});}});
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:p.url+'/'});await wait("!!document.getElementById('support')");
  check('unauthenticated-support-hidden',await evaluate("document.getElementById('workspace').hidden&&document.querySelectorAll('.support-entry').length===0"));
  await call('Page.navigate',{url:p.url+'/#preview_key='+key});await wait("!document.getElementById('workspace').hidden");
  check('new-version-visible',await evaluate("document.querySelector('header').textContent.includes('test-sop-records-r2')"));
  check('base-catalog-twenty-retained',await evaluate("document.querySelectorAll('#catalog-items article').length===20"));
  await evaluate("document.querySelector('[data-tab=support]').click()");await wait("document.querySelectorAll('.support-entry').length===20");
  check('live-twenty-families-two-hundred-step-rows',await evaluate("document.querySelectorAll('.support-step').length===200&&document.getElementById('support-summary').textContent.includes('200')"));
  check('all-109-records-and-49-extensions-visible',await evaluate("document.querySelectorAll('[data-record-id]').length===109&&document.querySelectorAll('[data-record-id^=record_extension]').length===49&&document.getElementById('support-summary').textContent.includes('新增49')"));
  await evaluate("document.getElementById('support-filter').value='AMF';document.getElementById('support-filter').dispatchEvent(new Event('input'))");
  check('literal-topic-filter',await evaluate("Array.from(document.querySelectorAll('.support-entry')).filter(n=>!n.hidden).length===1"));
  await evaluate("document.querySelector('.support-entry:not([hidden])').open=true");
  check('new-record-requirements-without-action-approval',await evaluate("document.querySelector('.support-entry:not([hidden])').textContent.includes('本次已补齐文字要求')&&document.querySelector('.support-entry:not([hidden])').textContent.includes('没有可执行处置依据')"));
  check('both-branches-and-site-gaps-visible',await evaluate("document.querySelector('.support-entry:not([hidden])').textContent.includes('相反记录分支')&&document.querySelector('.support-entry:not([hidden])').textContent.includes('真实现场原始记录')"));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'support-desktop.png'),Buffer.from(x.data,'base64')));
  await evaluate("document.querySelector('.support-entry:not([hidden]) .support-step button').click()");await wait("!document.getElementById('source-panel').hidden");
  check('real-guide-source-reread',await evaluate("document.getElementById('source-meta').textContent.includes('guide_sop_evidence_001.md')&&document.getElementById('source-text').textContent.includes('交接应列')"));
  await evaluate("document.querySelector('.support-entry:not([hidden]) [data-record-id^=record_extension] button').click()");await wait("document.getElementById('source-text').textContent.includes('record_extension_001_2')");
  check('new-extension-reference-reread-from-r2',await evaluate("document.getElementById('source-meta').textContent.includes('draft-r2')&&document.getElementById('source-meta').textContent.includes('test-sop-records-r2')"));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:850,deviceScaleFactor:1,mobile:true});
  await evaluate("document.getElementById('support').scrollIntoView()");
  check('mobile-no-horizontal-overflow',await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'support-mobile.png'),Buffer.from(x.data,'base64')));
  await evaluate("document.getElementById('support-filter').value='no-match-literal';document.getElementById('support-filter').dispatchEvent(new Event('input'))");
  check('empty-filter-is-explicit',await evaluate("document.getElementById('support-count').textContent.includes('匹配 0 / 20')"));
  await evaluate("document.getElementById('logout').click()");
  check('logout-clears-support-and-key',await evaluate("document.querySelectorAll('.support-entry').length===0&&!sessionStorage.getItem('sop-record-preview-key')&&document.getElementById('workspace').hidden"));
  console.log('Real Edge support checks passed: '+checks.length);
 }finally{
  if(ws&&ws.readyState===1){try{await call('Browser.close');}catch{}ws.close();}
  if(browser){await delay(1000);if(browser.exitCode===null){browser.kill();await delay(1000);}}
  for(const task of pending.values())clearTimeout(task.timer);
 }
}
