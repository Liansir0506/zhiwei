const fs=require('fs'),cp=require('child_process'),path=require('path'),net=require('net');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',v=>input+=v);process.stdin.on('end',()=>main(JSON.parse(input)).catch(e=>{console.error(e.message);process.exitCode=1;}));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(p){
 const keys=JSON.parse(fs.readFileSync(p.keys_file,'utf8')),debug=19361,profile=path.join(p.report_dir,'edge-profile');
 let browser,ws,id=0;const tasks=new Map(),pages=[],operations=[],errors=[],deniedWrites=[],httpErrors=[];
 const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id,timer=setTimeout(()=>reject(Error('CDP timeout: '+method)),45000);tasks.set(n,{resolve,reject,timer});ws.send(JSON.stringify({id:n,method,params}));});
 async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('Page evaluation failed');return r.result.value;}
 async function wait(expression,timeout=35000){const deadline=Date.now()+timeout;while(Date.now()<deadline){try{if(await evaluate(expression))return true;}catch{}await delay(200);}return false;}
 const save=()=>fs.writeFileSync(path.join(p.report_dir,'pages.json'),JSON.stringify({pages,operations,errors,deniedWrites,httpErrors},null,2));
 const expression={dashboard:"document.querySelectorAll('#dashboardBody .dash-metric').length>=6",qa:"!!document.getElementById('q')&&!!document.getElementById('f')",
  agent:"document.getElementById('agentAvailability').textContent.length>5",kb:"document.querySelectorAll('#kbResults .knowledge-card').length>0",
  docs:"document.getElementById('uploadNotice').textContent.length>5&&!document.getElementById('uploadNotice').textContent.includes('正在')&&document.querySelectorAll('#docsList .knowledge-card').length>0",
  business:"document.getElementById('businessStatus').textContent.length>5&&!document.getElementById('businessStatus').textContent.includes('正在')",
  versions:"document.getElementById('versionStatus').textContent.length>5&&!document.getElementById('versionStatus').textContent.includes('正在')",
  ops:"!document.getElementById('opsBody').hidden&&document.getElementById('opsStatus').textContent.length>5",
  eval:"!!document.getElementById('evalVersion')&&!document.getElementById('evalBody').hidden",perm:"!document.getElementById('permBody').hidden&&document.getElementById('permKeys').textContent.length>20"};
 async function visit(role,tab){
  const visible=await evaluate(`(()=>{const n=document.querySelector('[data-tab="${tab}"]');return !!n&&!n.hidden;})()`);
  if(!visible){pages.push({role,tab,state:'role-restricted'});save();return false;}
  const started=Date.now();await evaluate(`document.querySelector('[data-tab="${tab}"]').click()`);const ready=await wait(expression[tab]);await delay(300);
  const detail=await evaluate(`(()=>{const v=document.getElementById('view-${tab}');return {text:v.innerText.slice(0,2200),visible:v.classList.contains('active'),cards:v.querySelectorAll('article').length,buttons:Array.from(v.querySelectorAll('button')).filter(b=>!b.hidden).map(b=>({label:b.textContent.trim(),disabled:b.disabled})).slice(0,18)};})()`);
  pages.push({role,tab,state:ready?'loaded':'not-ready',elapsed_ms:Date.now()-started,...detail});save();
  if(role==='admin'||role==='ops')fs.writeFileSync(path.join(p.report_dir,role+'-'+tab+'.png'),Buffer.from((await call('Page.captureScreenshot')).data,'base64'));
  return ready;
 }
 try{
  await new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',()=>reject(Error('Debug port occupied')));s.listen(debug,'127.0.0.1',()=>s.close(resolve));});
  browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--remote-debugging-port='+debug,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
  let targets;for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${debug}/json/list`)).json();if(targets.length)break;}catch{}await delay(100);}if(!targets?.length)throw Error('Edge unavailable');
  ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  ws.onmessage=e=>{const r=JSON.parse(e.data),t=tasks.get(r.id);if(t){clearTimeout(t.timer);tasks.delete(r.id);r.error?t.reject(Error('CDP call failed')):t.resolve(r.result);}
   if(r.method==='Runtime.exceptionThrown')errors.push({type:'exception',description:r.params.exceptionDetails.exception?.description?.slice(0,250)||r.params.exceptionDetails.text});
   if(r.method==='Network.responseReceived'&&r.params.response.status>=400)httpErrors.push({url:r.params.response.url.split('#')[0],status:r.params.response.status});
   if(r.method==='Fetch.requestPaused'){
    const q=r.params.request,url=q.url.split('#')[0],local=url.startsWith(p.url+'/')||url.startsWith('about:')||url.startsWith('data:')||url.startsWith('blob:');
    const allowed=local&&['GET','HEAD'].includes(q.method);
    if(!allowed)deniedWrites.push({method:q.method,url});
    call(allowed?'Fetch.continueRequest':'Fetch.failRequest',allowed?{requestId:r.params.requestId}:{requestId:r.params.requestId,errorReason:'BlockedByClient'}).catch(()=>{});
   }
  };
  await call('Page.enable');await call('Runtime.enable');await call('Network.enable');await call('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
  for(const role of (p.roles||['ops','admin','auditor','guest'])){
   await call('Page.navigate',{url:p.url+'/?audit_role='+role+'#local_demo_key='+keys[role]});await wait("document.getElementById('capabilitiesStatus').textContent.length>10&&!document.getElementById('capabilitiesStatus').textContent.includes('正在')");await delay(500);
   const actual=await evaluate("fetch('/admin/capabilities',{headers:authHeaders()}).then(r=>r.json()).then(v=>v.roles)");
   if(!actual?.includes(role))throw Error('Role identity was not switched: '+role);
   operations.push({role,case:'actual-api-role-confirmed',passed:true});
   for(const tab of Object.keys(expression)){
    await visit(role,tab);
    if(role==='admin'&&tab==='kb'){
     await evaluate("document.querySelector('#kbResults .knowledge-card button')?.click()");const ok=await wait("document.getElementById('kbDetail').innerText.length>100");
     operations.push({role,case:'catalog-open-source-detail',passed:ok,text:await evaluate("document.getElementById('kbDetail').innerText.slice(0,700)")});
     const sourceButton=await evaluate("Array.from(document.querySelectorAll('#kbDetail button')).find(b=>b.textContent.includes('原文'))?.textContent||''");
     if(sourceButton){await evaluate("Array.from(document.querySelectorAll('#kbDetail button')).find(b=>b.textContent.includes('原文')).click()");await delay(1500);operations.push({role,case:'catalog-reread-original',text:await evaluate("document.getElementById('kbDetail').innerText.slice(-900)")});}
     await evaluate("document.getElementById('kbSearch').value='AMF';document.getElementById('kbFilter').requestSubmit()");await wait("document.querySelectorAll('#kbResults .knowledge-card').length>0");await delay(800);
     operations.push({role,case:'catalog-AMF-filter',text:await evaluate("document.getElementById('kbSummary').innerText"),count:await evaluate("document.querySelectorAll('#kbResults .knowledge-card').length")});
     await evaluate("document.getElementById('kbSearch').value='';document.getElementById('kbFilter').requestSubmit()");await wait("document.querySelectorAll('#kbResults .knowledge-card').length===12");
     if(await evaluate("!document.getElementById('kbNext').disabled")){await evaluate("document.getElementById('kbNext').click()");const ok=await wait("document.getElementById('kbPage').textContent.includes('第 2 页')");operations.push({role,case:'catalog-next-page',passed:ok});}
    }
    if(role==='admin'&&tab==='docs')operations.push({role,case:'upload-and-candidate-controls',...await evaluate("({candidate_visible:!document.getElementById('candidateForm').hidden,upload_visible:!document.getElementById('uploadForm').hidden,worker:document.getElementById('ingestWorker').textContent,candidates:document.getElementById('uploadVersion').options.length-1})")});
    if(role==='ops'&&tab==='qa'){
     await wait("!!document.querySelector('.chip[data-q]')&&!document.getElementById('send').disabled");
     const chip=await evaluate("document.querySelector('.chip[data-q]')?.textContent||''");if(chip)await evaluate("document.querySelector('.chip[data-q]').click()");
     operations.push({role,case:'recommendation-fills-composer',passed:await evaluate("document.getElementById('q').value.length>0"),query:await evaluate("document.getElementById('q').value"),submit:false});
    }
    save();
   }
  }
  await call('Page.navigate',{url:p.url+'/?audit_role=admin_mobile#local_demo_key='+keys.admin});await delay(1500);await call('Emulation.setDeviceMetricsOverride',{width:390,height:850,deviceScaleFactor:1,mobile:true});
  for(const tab of ['dashboard','qa','kb','docs','business','versions','ops','eval','perm']){
   await evaluate(`document.querySelector('[data-tab="${tab}"]').click()`);await delay(1200);
   const overflow=await evaluate("({document:document.documentElement.scrollWidth>innerWidth,main:document.querySelector('main').getBoundingClientRect().right>innerWidth+1})");operations.push({role:'admin',case:'mobile-'+tab,...overflow});
   if(['dashboard','kb','docs','versions'].includes(tab))fs.writeFileSync(path.join(p.report_dir,'mobile-'+tab+'.png'),Buffer.from((await call('Page.captureScreenshot')).data,'base64'));
  }
  save();console.log('Audit completed: '+pages.length+' role/page observations; '+errors.length+' JS exceptions.');
 }finally{if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}if(browser){await delay(1000);if(browser.exitCode===null){browser.kill();await delay(1000);}}for(const t of tasks.values())clearTimeout(t.timer);save();}
}
