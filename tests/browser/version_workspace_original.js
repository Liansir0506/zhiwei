// Live 8010 review and persisted gate; no publication, QA generation or new admission.
const fs=require('fs'),path=require('path'),cp=require('child_process');let input='';
process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),pending=new Map(),checks=[],shots=[];let browser,ws,seq=0;
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>reject(new Error('CDP timeout')),20000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error('Browser evaluation failed');return r.result.value;};
  const until=async(expression,label,n=500)=>{for(let i=0;i<n;i++){if(await evaluate(expression))return;await pause(100)}throw new Error('Timed out: '+label);};
  const assert=(yes,label)=>{if(!yes)throw new Error(label);checks.push(label);};
  const shot=async name=>{fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));shots.push(name);};
  const role=async name=>{await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.keys[name])});GovernanceWorkspace.credentialsChanged();VersionWorkspace.credentialsChanged();switchTab('versions')`);await until(`!document.getElementById('versionStatus').textContent.includes('正在')`,'live version role');};
  try{
    let occupied=false;try{await fetch('http://127.0.0.1:19344/json/version',{signal:AbortSignal.timeout(1000)});occupied=true;}catch{}if(occupied)throw new Error('Browser port occupied');
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+path.join(p.reportDir,'edge-profile'),'--remote-debugging-port=19344','--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
    let targets;for(let i=0;i<100;i++){try{targets=await(await fetch('http://127.0.0.1:19344/json/list')).json();if(targets.length)break;}catch{}await pause(100)}
    if(!targets?.length)throw new Error('Edge unavailable');ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});ws.onmessage=e=>{const r=JSON.parse(e.data),q=pending.get(r.id);if(q){clearTimeout(q.timer);pending.delete(r.id);r.error?q.reject(new Error('CDP failed')):q.resolve(r.result)}};
    await call('Network.enable');await call('Network.setBlockedURLs',{urls:['*api.deepseek.com*']});await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});await call('Page.navigate',{url:p.url});await until(`typeof switchTab==='function'&&!!window.VersionWorkspace`,'live platform');
    await role('ops');assert(await evaluate(`document.querySelector('[data-tab="versions"]').hidden&&document.getElementById('versionStatus').textContent.includes('受限')`),'Original ops sees restricted page and no version governance nav');
    await role('admin');await until(`!!document.getElementById('versionSelect')`,'live administrator versions');
    assert(await evaluate(`!document.querySelector('[data-tab="versions"]').hidden&&document.getElementById('versionSelect').textContent.includes('local-content-quality-r1')`),'Administrator has real version route and current ACTIVE');
    await evaluate(`document.getElementById('versionSelect').value=${JSON.stringify(p.version)};document.getElementById('versionSelect').dispatchEvent(new Event('change'))`);await until(`!!document.getElementById('versionReview')`,'compatible candidate details');
    assert(await evaluate(`document.getElementById('versionDetail').textContent.includes('manual_pending_demo.md')&&document.querySelector('[data-admission="pending"]')`),'Actual pending upload remains explicitly quarantined');
    assert(await evaluate(`document.getElementById('versionGate').textContent.includes('门禁未通过')&&document.querySelectorAll('#versionGate [data-check]').length===5`),'Actual one-FAQ delta has five persisted checks and failed gate');
    assert(await evaluate(`document.getElementById('versionPreparePlan').disabled&&document.getElementById('versionWorkflow').textContent.includes('完整服务配置')`),'Original publication cannot change only ACTIVE pointer');
    await evaluate(`document.getElementById('versionReview').click()`);await until(`!!document.getElementById('versionPreparePackage')`,'live package preparation control');
    assert(await evaluate(`!!document.getElementById('versionRunGate')&&document.getElementById('versionWorkflow').textContent.includes('新资料准入0')`),'Administrator can manually prepare complete original package and zero-model gate');await shot('original-version-candidate.png');
    await evaluate(`document.querySelector('#versionDetail details').open=true;document.querySelector('[data-filename] button').click()`);await until(`document.querySelector('[data-filename]').querySelectorAll('pre').length===2`,'live source original');
    assert(await evaluate(`document.querySelector('[data-filename]').querySelectorAll('pre').length===2`),'Original source text is read through authenticated version endpoint');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await pause(250);assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),'Original 390px version view fits viewport');await shot('original-version-mobile.png');
    await role('auditor');await until(`!!document.getElementById('versionSelect')`,'auditor live view');assert(await evaluate(`!document.getElementById('versionReview')&&!document.getElementById('versionPreparePlan')`),'Original auditor has read-only status and no publishing controls');
    await role('scoped');assert(await evaluate(`document.querySelector('[data-tab="versions"]').hidden&&!document.getElementById('versionDetail').textContent&&!document.getElementById('versionHistory').textContent`),'Scoped administrator cannot restore global version data');
    fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,screenshots:shots,actual_publication:false,paid_calls:0,new_admissions:0},null,2));console.log(JSON.stringify({passed:true,checks:checks.length,paid_calls:0}));
  }catch(error){try{fs.writeFileSync(path.join(p.reportDir,'browser-failure.json'),JSON.stringify({passed:false,checks,error:error.message},null,2));if(ws?.readyState===1)await shot('failure.png');}catch{}console.error(error.message);process.exitCode=1;}
  finally{if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}browser?.kill();for(const q of pending.values())clearTimeout(q.timer);}
});
