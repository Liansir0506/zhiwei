const fs=require('fs'),cp=require('child_process'),path=require('path'),net=require('net');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>input+=s);
process.stdin.on('end',()=>main(JSON.parse(input)).catch(e=>{console.error(e.message);process.exitCode=1;}));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(p){
 const profile=path.join(p.report_dir,'edge-profile'),debug=19356,key=fs.readFileSync(p.key_file,'utf8').trim();
 let browser,ws,id=0,context,childSession,paused=null,hold=false;const pending=new Map(),checks=[],requests=[],wireResponses=[],methods=new Map();
 const call=(method,params={},session)=>new Promise((resolve,reject)=>{const n=++id,timer=setTimeout(()=>reject(Error('CDP timeout: '+method)),30000);pending.set(n,{resolve,reject,timer});ws.send(JSON.stringify({id:n,method,params,...(session?{sessionId:session}:{})}));});
 async function evaluate(expression,child=false){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,...(child?{contextId:context}:{})},child?childSession:undefined);if(r.exceptionDetails)throw Error('Browser evaluation failed');return r.result.value;}
 async function wait(expression,child=false){const until=Date.now()+45000;while(Date.now()<until){if(await evaluate(expression,child))return;await delay(100);}throw Error('Browser state wait failed');}
 function check(name,value){if(!value)throw Error(name);checks.push({case:name,passed:true});fs.writeFileSync(path.join(p.report_dir,'browser-checks.json'),JSON.stringify(checks,null,2));}
 const click=(id,child=false)=>evaluate('document.getElementById('+JSON.stringify(id)+').click()',child);
 async function mount(){await click('trainingTab');await click('trainingLaunch');await wait("!!document.querySelector('#trainingFrameHost iframe')");let frame,targets;childSession=undefined;
  for(let i=0;i<100;i++){
   const tree=await call('Page.getFrameTree');frame=tree.frameTree.childFrames?.find(f=>f.frame.url==='about:srcdoc')?.frame;
   if(frame)break;
   targets=(await call('Target.getTargets')).targetInfos;const remote=targets.find(t=>t.type==='iframe'&&t.url==='about:srcdoc');
   if(remote){childSession=(await call('Target.attachToTarget',{targetId:remote.targetId,flatten:true})).sessionId;await call('Page.enable',{},childSession);frame=(await call('Page.getFrameTree',{},childSession)).frameTree.frame;break;}
   await delay(100);
  }
  if(!frame){fs.writeFileSync(path.join(p.report_dir,'frame-diagnostic.json'),JSON.stringify({tree:await call('Page.getFrameTree'),targets:targets?.map(t=>({type:t.type,url:t.url}))},null,2));throw Error('Sandbox frame missing');}
  context=(await call('Page.createIsolatedWorld',{frameId:frame.id,worldName:'training-acceptance'},childSession)).executionContextId;
  await wait("document.getElementById('json')?.textContent.length>20",true);
 }
 const state=()=>evaluate("JSON.parse(document.getElementById('json').textContent)",true);
 async function choose(value){await evaluate("document.getElementById('scenario').value="+JSON.stringify(value)+";document.getElementById('scenario').dispatchEvent(new Event('change'))",true);}
 async function prepare(value){await choose(value);await click('collect',true);await click('review',true);}
 async function execute(value){await prepare(value);await click('confirm',true);await click('execute',true);}
 try{
  await new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',()=>reject(Error('Owned debug port occupied')));s.listen(debug,'127.0.0.1',()=>s.close(resolve));});
  browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--remote-debugging-port='+debug,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
  let targets;for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${debug}/json/list`)).json();if(targets.length)break;}catch{}await delay(100);}
  if(!targets?.length)throw Error('Headless Edge unavailable');ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  ws.onmessage=e=>{const r=JSON.parse(e.data),task=pending.get(r.id);if(task){clearTimeout(task.timer);pending.delete(r.id);r.error?task.reject(Error('CDP failed: '+r.error.message)):task.resolve(r.result);}
   if(r.method==='Network.requestWillBeSent'){methods.set(r.params.requestId,r.params.request.method);requests.push({method:r.params.request.method,url:r.params.request.url.split('#')[0]});}
   if(r.method==='Network.responseReceived')wireResponses.push({method:methods.get(r.params.requestId),url:r.params.response.url});
   if(r.method==='Fetch.requestPaused'){
    if(r.params.responseStatusCode&&hold&&r.params.request.url===p.url+'/training/amf'){paused=r.params.requestId;return;}
    const u=r.params.request.url,allowed=u.startsWith(p.url+'/')||u.startsWith('about:')||u.startsWith('blob:');
    call(allowed?'Fetch.continueRequest':'Fetch.failRequest',allowed?{requestId:r.params.requestId}:{requestId:r.params.requestId,errorReason:'BlockedByClient'}).catch(()=>{});
   }
  };
  await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
  await call('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'},{urlPattern:p.url+'/training/amf',requestStage:'Response'}]});
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:p.url+'/'});await wait("!!document.getElementById('trainingTab')");check('no-login-training-hidden',await evaluate("document.getElementById('trainingTab').hidden&&!document.querySelector('#trainingFrameHost iframe')"));
  await call('Page.navigate',{url:p.url+'/#local_demo_key='+key});await wait("!document.getElementById('trainingTab').hidden&&document.querySelectorAll('#businessList article').length===2");
  check('existing-login-capability-and-business-list',await evaluate("!location.hash&&document.getElementById('businessStatus').textContent.length>0"));
  await mount();check('isolated-frame-receives-no-same-origin-permission',await evaluate("document.querySelector('#trainingFrameHost iframe').getAttribute('sandbox')==='allow-scripts allow-downloads'&&document.querySelector('#trainingFrameHost iframe').contentDocument===null"));
  check('child-cannot-read-parent-login',await evaluate("(()=>{try{parent.sessionStorage.getItem('readonly_key');return false;}catch{return true;}})()",true));
  check('child-cannot-use-browser-storage',await evaluate("(()=>{try{sessionStorage.setItem('escape','1');return false;}catch{return true;}})()",true));
  check('child-network-blocked-by-csp',await evaluate("(async()=>{let blocked=false;document.addEventListener('securitypolicyviolation',e=>{if(e.violatedDirective==='connect-src')blocked=true;},{once:true});try{await fetch("+JSON.stringify(p.url+'/business/alarms')+",{method:'POST',body:'{}'});}catch{}await new Promise(r=>setTimeout(r,100));return blocked;})()",true));
  check('eight-scenarios-and-confirmation-required',await evaluate("document.querySelectorAll('#scenario option').length===8&&document.getElementById('execute').disabled",true));
  await prepare('normal');check('same-window-plan-awaits-confirmation',(await state()).phase==='awaiting_confirmation');
  await click('execute',true);check('cannot-run-before-confirmation',(await state()).phase==='awaiting_confirmation');
  await click('confirm',true);await click('execute',true);await click('observe',true);let x=await state();
  check('backlog-and-current-cohorts-separated',x.observations[0].prior_completed===40&&x.observations[0].current_completed===40&&x.observations[0].queue===20);
  await click('observe',true);check('one-good-window-still-open',(await state()).phase==='observing');await click('observe',true);
  check('normal-completes-with-audit',(await state()).phase==='closed_training'&&(await state()).audit.at(-1).type==='training_completed');
  const downloads=path.join(p.report_dir,'downloads');fs.mkdirSync(downloads,{recursive:true});await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});await click('export',true);
  const file=path.join(downloads,'amf-exercise-normal.json');for(let i=0;i<100&&!fs.existsSync(file);i++)await delay(100);if(!fs.existsSync(file))throw Error('Sandbox export did not complete');
  x=JSON.parse(fs.readFileSync(file,'utf8'));check('actual-export-retains-model-and-review-boundaries',x.data_origin==='generated_training_model'&&!x.real_action_authorized&&!x.human_review_verified&&!x.source_authenticity_verified&&!x.site_truth_verified);
  await evaluate("document.getElementById('trainingFrameHost').scrollIntoView({block:'start',behavior:'instant'})");await evaluate('window.scrollTo(0,0)',true);await delay(300);fs.writeFileSync(path.join(p.report_dir,'training-desktop.png'),Buffer.from((await call('Page.captureScreenshot')).data,'base64'));
  await prepare('counter');check('counter-branch-no-ingress-action',(await state()).phase==='alternative');
  await prepare('conflict');check('conflicting-records-stop',(await state()).reason==='conflicting_evidence');
  await prepare('missing');check('missing-records-stop',(await state()).reason==='missing_evidence');
  await choose('wrong_target');await click('collect',true);check('wrong-object-stops',(await state()).phase==='stopped');
  await execute('apply_failure');x=await state();check('apply-failure-rolls-back-without-claiming-recovery',x.phase==='rolled_back_escalated'&&x.model.ingress_budget===20&&x.audit.at(-1).detail.service_recovered===false);
  await execute('rollback_failure');check('rollback-failure-escalates',(await state()).phase==='escalated_unrestored');
  await execute('recurrence');await click('observe',true);await click('observe',true);await click('observe',true);check('recurrence-rolls-back',(await state()).phase==='rolled_back_escalated'&&(await state()).reason==='recurrence');
  await execute('normal');await click('stop',true);check('manual-stop-restores-local-backup',(await state()).phase==='rolled_back_escalated'&&(await state()).model.ingress_budget===20);
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:850,deviceScaleFactor:1,mobile:true});await evaluate("document.getElementById('trainingFrameHost').scrollIntoView({block:'start',behavior:'instant'})");await evaluate('window.scrollTo(0,0)',true);await delay(300);
  check('parent-and-child-mobile-width',await evaluate('document.documentElement.scrollWidth<=innerWidth')&&await evaluate('document.documentElement.scrollWidth<=innerWidth',true));
  fs.writeFileSync(path.join(p.report_dir,'training-mobile.png'),Buffer.from((await call('Page.captureScreenshot')).data,'base64'));
  check('training-controls-visible-after-opening',await evaluate("document.getElementById('trainingClose').getBoundingClientRect().top>=0&&document.getElementById('trainingClose').getBoundingClientRect().bottom<innerHeight"));
  await click('trainingClose');check('return-removes-memory-frame-and-restores-list',await evaluate("!document.querySelector('#trainingFrameHost iframe')&&document.getElementById('view-training').hidden&&!document.getElementById('view-business').hidden"));
  await mount();check('reentry-creates-fresh-training',(await state()).phase==='new'&&(await state()).model.ingress_budget===20);
  await click('businessTab');await click('trainingTab');hold=true;await click('trainingLaunch');for(let i=0;i<100&&!paused;i++)await delay(100);if(!paused)throw Error('Late response was not intercepted');
  await click('businessTab');hold=false;try{await call('Fetch.continueResponse',{requestId:paused});}catch{}await delay(300);
  check('late-response-after-leaving-cannot-mount-frame',await evaluate("!document.querySelector('#trainingFrameHost iframe')&&document.getElementById('view-training').hidden"));
  await mount();await evaluate("document.getElementById('isolatedKey').value='';document.getElementById('isolatedKeyForm').requestSubmit()");await wait("document.getElementById('trainingTab').hidden");
  check('logout-clears-training-context-and-credentials',await evaluate("!sessionStorage.getItem('readonly_key')&&!document.querySelector('#trainingFrameHost iframe')&&document.getElementById('view-training').hidden"));
  check('no-business-or-model-post-responses-from-training',!wireResponses.some(r=>r.method==='POST'));
  fs.writeFileSync(path.join(p.report_dir,'requests.json'),JSON.stringify(requests,null,2));console.log('Training browser checks passed: '+checks.length);
 }finally{if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}if(browser){await delay(1000);if(browser.exitCode===null){browser.kill();await delay(1000);}}for(const task of pending.values())clearTimeout(task.timer);}
}
