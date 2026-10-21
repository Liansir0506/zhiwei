// Actual headless Edge, local server only. No simulated API responses.
const fs=require('fs'),cp=require('child_process'),path=require('path');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>input+=x);
process.stdin.on('end',()=>main(JSON.parse(input)).catch(e=>{console.error(e.message);process.exitCode=1;}));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(p){
 const root=p.report_dir, profile=path.join(root,'edge-profile'),debug=19348;
 const key=fs.readFileSync(p.key_file,'utf8').trim();let browser,ws,id=0;const pending=new Map(),checks=[];
 function call(method,params={}){return new Promise((resolve,reject)=>{const number=++id;const timer=setTimeout(()=>{pending.delete(number);reject(Error('CDP timeout'));},180000);pending.set(number,{resolve,reject,timer});ws.send(JSON.stringify({id:number,method,params}));});}
 async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('browser evaluation failed');return r.result.value;}
 async function wait(expression,ms=100000){const until=Date.now()+ms;while(Date.now()<until){if(await evaluate(expression))return;await delay(300);}throw Error('browser state wait failed');}
 function check(name,ok){if(!ok)throw Error(name);checks.push({case:name,passed:true});fs.writeFileSync(path.join(root,'browser-checks.json'),JSON.stringify(checks,null,2));}
 try{
  const net=require('net');await new Promise((resolve,reject)=>{const probe=net.createServer();probe.once('error',()=>reject(Error('dedicated browser debug port occupied')));probe.listen(debug,'127.0.0.1',()=>probe.close(resolve));});
  browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--remote-debugging-port='+debug,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
  let targets;for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${debug}/json/list`)).json();if(targets.length)break;}catch{}await delay(100);}
  if(!targets?.length)throw Error('headless Edge unavailable');
  ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  ws.onmessage=e=>{const r=JSON.parse(e.data),task=pending.get(r.id);if(task){clearTimeout(task.timer);pending.delete(r.id);r.error?task.reject(Error('CDP command failed')):task.resolve(r.result);}};
  await call('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
  ws.addEventListener('message',e=>{const r=JSON.parse(e.data);if(r.method==='Fetch.requestPaused'){const u=r.params.request.url;const allowed=u.startsWith(p.url+'/')||u.startsWith('about:');call(allowed?'Fetch.continueRequest':'Fetch.failRequest',allowed?{requestId:r.params.requestId}:{requestId:r.params.requestId,errorReason:'BlockedByClient'}).catch(()=>{});}});
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:840,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:p.url+'/'});await wait("!!document.getElementById('login')");
  check('unauthenticated-restricted-page',await evaluate("!document.getElementById('login').hidden&&document.getElementById('workspace').hidden"));
  await call('Page.navigate',{url:p.url+'/#preview_key='+key});await wait("!document.getElementById('workspace').hidden");
  check('real-catalog-twenty',await evaluate("document.querySelectorAll('#catalog-items article').length===20"));
  check('credential-removed-from-address',await evaluate("location.hash===''&&!document.body.textContent.includes("+JSON.stringify(key)+")"));
  await evaluate("document.querySelector('[data-tab=checks]').click()");
  check('ordinary-failed-gate-and-zero-independent-visible',await evaluate("document.getElementById('gate-results').textContent.includes('未通过')&&document.getElementById('gaps').textContent.includes('分母0')"));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'checks-desktop.png'),Buffer.from(x.data,'base64')));
  await evaluate("document.querySelector('[data-tab=search]').click();document.getElementById('query').value="+JSON.stringify(p.query)+";document.getElementById('search-form').requestSubmit()");
  await wait("!document.getElementById('search-button').disabled&&document.querySelectorAll('#search-results article').length>0",150000);
  check('real-source-search-match',await evaluate("document.getElementById('search-results').textContent.includes("+JSON.stringify(p.expected_source)+")"));
  await evaluate("Array.from(document.querySelectorAll('#search-results article')).find(n=>n.querySelector('h3').textContent==="+JSON.stringify(p.expected_source)+").querySelector('button').click()");
  await wait("!document.getElementById('source-panel').hidden");
  check('citation-reread-matches-search-bytes',await evaluate("document.getElementById('source-text').textContent===Array.from(document.querySelectorAll('#search-results article')).find(n=>n.querySelector('h3').textContent==="+JSON.stringify(p.expected_source)+").querySelector('pre').textContent"));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'search-desktop.png'),Buffer.from(x.data,'base64')));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:840,deviceScaleFactor:1,mobile:true});
  check('mobile-no-horizontal-overflow',await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  await call('Page.captureScreenshot').then(x=>fs.writeFileSync(path.join(root,'search-mobile.png'),Buffer.from(x.data,'base64')));
  await evaluate("document.getElementById('query').value='AMF完整处置流程';document.getElementById('search-form').requestSubmit()");
  await wait("!document.getElementById('search-button').disabled&&document.getElementById('search-results').textContent.includes('没有找到')");
  check('complete-workflow-no-evidence',await evaluate("document.querySelectorAll('#search-results article').length===0"));
  await evaluate("document.getElementById('logout').click()");
  check('logout-clears-credential-and-source',await evaluate("!sessionStorage.getItem('sop-guide-preview-key')&&document.getElementById('workspace').hidden&&document.getElementById('source-text').textContent===''"));
  console.log('Real Edge checks passed: '+checks.length);
 }finally{
  if(ws&&ws.readyState===1){try{await call('Browser.close');}catch{}ws.close();}
  if(browser){await delay(1000);if(browser.exitCode===null){browser.kill();await delay(1000);}}
 }
}
