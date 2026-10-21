// Static exact-body review in a hidden local Edge; no credential or external request.
const fs=require('fs'),cp=require('child_process'),path=require('path'),net=require('net');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>input+=s);
process.stdin.on('end',()=>main(JSON.parse(input)).catch(e=>{console.error(e.message);process.exitCode=1;}));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function main(p){
 const root=p.report_dir,profile=path.join(root,'exercise-edge-profile'),port=19355;
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
  for(let i=0;i<100;i++){if(await evaluate("document.querySelectorAll('#scenario option').length===8"))break;await delay(100);}
  async function click(id){await evaluate("document.getElementById("+JSON.stringify(id)+").click()");}
  async function select(value){await evaluate("document.getElementById('scenario').value="+JSON.stringify(value)+";document.getElementById('scenario').dispatchEvent(new Event('change'))");}
  async function prepare(value){await select(value);await click('collect');await click('review');}
  async function execute(value){await prepare(value);await click('confirm');await click('execute');}
  const state=()=>evaluate("JSON.parse(document.getElementById('json').textContent)");
  check('eight-real-scenarios-and-initial-actions-closed',await evaluate("document.querySelectorAll('#scenario option').length===8&&document.getElementById('confirm').disabled&&document.getElementById('execute').disabled"));
  await prepare('normal');let x=await state();check('same-window-evidence-and-explicit-plan',x.phase==='awaiting_confirmation'&&x.plan.before_budget===20&&x.plan.to_budget===80&&x.records.registration_window.incoming===60);
  await click('execute');check('execution-disabled-before-confirmation',(await state()).phase==='awaiting_confirmation');
  await click('confirm');await click('execute');check('bound-action-changes-memory-model-only',(await state()).phase==='observing'&&(await state()).model.ingress_budget===80);
  await click('observe');x=await state();check('backlog-cohort-not-counted-as-current-success',x.phase==='observing'&&x.observations[0].prior_completed===40&&x.observations[0].current_completed===40&&x.observations[0].queue===20);
  await click('observe');check('one-good-window-does-not-close',(await state()).phase==='observing');
  await click('observe');x=await state();check('normal-path-completes-with-full-audit',x.phase==='closed_training'&&x.consecutive_good===2&&x.audit.at(-1).type==='training_completed');
  check('export-data-retains-training-provenance',x.data_origin==='generated_training_model'&&!x.site_truth_verified&&!x.real_action_authorized&&!x.human_review_verified&&!x.source_authenticity_verified);
  await call('Page.captureScreenshot').then(r=>fs.writeFileSync(path.join(root,'exercise-desktop.png'),Buffer.from(r.data,'base64')));
  await prepare('counter');check('counter-branch-blocks-ingress-action',(await state()).phase==='alternative'&&await evaluate("document.getElementById('execute').disabled"));
  await prepare('conflict');check('conflicting-records-stop',(await state()).reason==='conflicting_evidence');
  await prepare('missing');check('missing-records-stop',(await state()).reason==='missing_evidence');
  await select('wrong_target');await click('collect');check('wrong-object-stops-before-review',(await state()).phase==='stopped'&&await evaluate("document.getElementById('review').disabled"));
  await execute('apply_failure');x=await state();check('interrupted-apply-restores-config-but-keeps-escalation',x.phase==='rolled_back_escalated'&&x.model.ingress_budget===20&&x.audit.at(-1).detail.service_recovered===false);
  await execute('rollback_failure');x=await state();check('rollback-failure-never-closes',x.phase==='escalated_unrestored'&&x.model.ingress_budget===70);
  await execute('recurrence');await click('observe');await click('observe');await click('observe');check('recurrence-rolls-back-and-escalates',(await state()).reason==='recurrence'&&(await state()).phase==='rolled_back_escalated');
  await prepare('normal');await click('stop');check('stop-before-change-preserves-config',(await state()).model.ingress_budget===20&&(await state()).phase==='stopped');
  await execute('normal');await click('stop');check('stop-after-change-restores-saved-config',(await state()).model.ingress_budget===20&&(await state()).phase==='rolled_back_escalated');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:850,deviceScaleFactor:1,mobile:true});
  await evaluate('window.scrollTo(0,0)');check('mobile-no-horizontal-overflow',await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  await call('Page.captureScreenshot').then(r=>fs.writeFileSync(path.join(root,'exercise-mobile.png'),Buffer.from(r.data,'base64')));
  console.log('Real in-memory exercise browser checks passed: '+checks.length);
 }finally{
  if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}
  if(browser){await delay(1000);if(browser.exitCode===null){browser.kill();await delay(1000);}}
  for(const task of pending.values())clearTimeout(task.timer);
 }
}
