// Static exact-body review in a hidden local Edge; no credential or external request.
const fs=require('fs'),cp=require('child_process'),path=require('path'),net=require('net');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>input+=s);
process.stdin.on('end',()=>main(JSON.parse(input)).catch(e=>{console.error(e.message);process.exitCode=1;}));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(p){
 const root=p.report_dir,profile=path.join(root,'review-edge-profile'),port=19351;
 let browser,ws,id=0;const pending=new Map(),checks=[];
 const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id,timer=setTimeout(()=>reject(Error('CDP timeout')),30000);pending.set(n,{resolve,reject,timer});ws.send(JSON.stringify({id:n,method,params}));});
 async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('review evaluation failed');return r.result.value;}
 function check(name,ok){if(!ok)throw Error(name);checks.push({case:name,passed:true});fs.writeFileSync(path.join(root,'browser-checks.json'),JSON.stringify(checks,null,2));}
 try{
  await new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',()=>reject(Error('browser port occupied')));s.listen(port,'127.0.0.1',()=>s.close(resolve));});
  browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--remote-debugging-port='+port,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
  let targets;for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();if(targets.length)break;}catch{}await delay(100);}
  if(!targets?.length)throw Error('Edge unavailable');
  ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  ws.onmessage=e=>{const r=JSON.parse(e.data),task=pending.get(r.id);if(task){clearTimeout(task.timer);pending.delete(r.id);r.error?task.reject(Error('CDP failed')):task.resolve(r.result);}};
  await call('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
  ws.addEventListener('message',e=>{const r=JSON.parse(e.data);if(r.method==='Fetch.requestPaused'){const allowed=r.params.request.url.startsWith('file:')||r.params.request.url.startsWith('about:');call(allowed?'Fetch.continueRequest':'Fetch.failRequest',allowed?{requestId:r.params.requestId}:{requestId:r.params.requestId,errorReason:'BlockedByClient'}).catch(()=>{});}});
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:p.review_url});
  for(let i=0;i<100;i++){if(await evaluate('document.querySelectorAll(".entry").length===20'))break;await delay(100);}
  check('twenty-exact-review-entries',await evaluate('document.querySelectorAll(".entry").length===20'));
  await evaluate('document.querySelectorAll("details").forEach(n=>n.open=true)');
  check('forty-nine-record-extensions',await evaluate('document.querySelectorAll(".entry li").length===49'));
  const texts=await evaluate('Array.from(document.querySelectorAll(".entry pre"),n=>n.textContent)');
  check('all-twenty-full-bodies-match-sealed-drafts',texts.length===20&&texts.every((s,i)=>s===p.bodies[i].replace(/\r\n/g,'\n')));
  check('exact-target-and-disabled-generation-shown',await evaluate('document.getElementById("target").textContent.includes("test_sop_records_r2")&&document.getElementById("target").textContent.includes("180")&&document.getElementById("target").textContent.includes("关闭")'));
  check('pending-no-merge-publication-or-outbound-scope',await evaluate('document.getElementById("limits").textContent.includes("不发布")&&document.getElementById("limits").textContent.includes("DeepSeek")&&document.querySelector("h1").textContent.includes("待审")'));
  check('all-new-identities-and-byte-bindings-visible',await evaluate('Array.from(document.querySelectorAll(".entry"),n=>n.textContent.includes("draft-r2")&&n.textContent.includes("整项绑定")&&n.textContent.includes("旧正文SHA")).every(Boolean)'));
  check('desktop-no-horizontal-overflow',await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'review-desktop.png'),Buffer.from(x.data,'base64')));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:850,deviceScaleFactor:1,mobile:true});
  check('mobile-no-horizontal-overflow',await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'review-mobile.png'),Buffer.from(x.data,'base64')));
  console.log('Real review browser checks passed: '+checks.length);
 }finally{
  if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}
  if(browser){await delay(1000);if(browser.exitCode===null){browser.kill();await delay(1000);}}
  for(const task of pending.values())clearTimeout(task.timer);
 }
}
