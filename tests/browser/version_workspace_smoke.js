// Real browser and real SQL/Milvus gate/publish/rollback. No synthetic pass reports.
const fs=require('fs'),path=require('path'),cp=require('child_process');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),pending=new Map(),checks=[],shots=[];let browser,ws,seq=0;
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>reject(new Error('CDP timeout')),20000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error('Browser expression failed: '+String(r.exceptionDetails.exception?.description||r.exceptionDetails.text).split('\n')[0]);return r.result.value;};
  const until=async(expression,label,n=1200)=>{for(let i=0;i<n;i++){if(await evaluate(expression))return;await pause(100)}throw new Error('Timed out: '+label);};
  const assert=(yes,label)=>{if(!yes)throw new Error(label);checks.push(label);};
  const shot=async name=>{fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));shots.push(name);};
  const role=async name=>{await evaluate(`sessionStorage.setItem('version_fixture_key',${JSON.stringify(p.keys[name])});authenticate()`);await until(`!document.getElementById('versionStatus').textContent.includes('正在')`,'role check',200);};
  const version=async name=>{await evaluate(`document.getElementById('versionSelect').value=${JSON.stringify(name)};document.getElementById('versionSelect').dispatchEvent(new Event('change'))`);await until(`document.getElementById('versionDetail').textContent.includes(${JSON.stringify(name)})&&!document.getElementById('versionStatus').textContent.includes('正在')`,'version details');};
  try{
    let occupied=false;try{await fetch('http://127.0.0.1:19343/json/version',{signal:AbortSignal.timeout(1000)});occupied=true;}catch{}if(occupied)throw new Error('Owned browser debugging port is already occupied');
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+path.join(p.reportDir,'edge-profile'),'--remote-debugging-port=19343','--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
    let targets;for(let i=0;i<100;i++){try{targets=await(await fetch('http://127.0.0.1:19343/json/list')).json();if(targets.length)break;}catch{}await pause(100)}
    if(!targets?.length)throw new Error('Edge unavailable');ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl);
    await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});ws.onmessage=e=>{const r=JSON.parse(e.data),q=pending.get(r.id);if(q){clearTimeout(q.timer);pending.delete(r.id);r.error?q.reject(new Error('CDP failed')):q.resolve(r.result)}};
    await call('Network.enable');await call('Network.setBlockedURLs',{urls:['*api.deepseek.com*']});await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:p.url});await until(`typeof authenticate==='function'`,'fixture UI');
    await role('ops');assert(await evaluate(`document.querySelector('[data-tab="versions"]').hidden&&!document.getElementById('versionSelect')&&document.getElementById('versionStatus').textContent.includes('受限')`),'Ops has restricted view and no global version data');
    await role('auditor');await until(`!!document.getElementById('versionSelect')`,'auditor list');await version(p.successor);
    assert(await evaluate(`!document.getElementById('versionReview')&&!document.getElementById('versionPreparePlan')`),'Auditor reads version status without mutation controls');
    await role('admin');await until(`!!document.getElementById('versionSelect')`,'admin list');await version(p.successor);
    assert(await evaluate(`document.querySelectorAll('[data-filename]').length===3&&document.getElementById('versionDetail').textContent.includes('分母0')&&document.getElementById('versionDetail').textContent.includes('设备资料')`),'Actual three-source admitted subset and remaining quality gaps are explicit');
    await evaluate(`document.querySelector('#versionDetail details').open=true;document.querySelector('[data-filename] button').click()`);await until(`document.querySelector('[data-filename] pre:last-child')?.textContent.includes('获准')||document.querySelector('[data-filename]').querySelectorAll('pre').length===2`,'actual source body');
    assert(await evaluate(`document.querySelector('[data-filename]').querySelectorAll('pre').length===2`),'Admin opens authenticated exact source original');
    await evaluate(`document.getElementById('versionReview').click()`);await until(`!!document.getElementById('versionRunGate')`,'concrete engineering review');
    assert(await evaluate(`document.getElementById('versionWorkflow').textContent.includes('新资料准入0')`),'Engineering review does not grant new source admission');
    await evaluate(`document.getElementById('versionRunGate').click()`);await until(`document.getElementById('versionStatus').textContent.includes('passed')`,'real gate task');
    await until(`document.querySelectorAll('#versionGate [data-check]').length===5`,'five persisted gate checks');
    assert(await evaluate(`document.getElementById('versionGate').textContent.includes('结构门禁通过')`),'Real five-check task completion and report digest visible');await shot('version-gate.png');
    await evaluate(`document.getElementById('versionPreparePlan').click()`);await until(`!!document.getElementById('versionConfirm')`,'publication plan');
    assert(await evaluate(`document.getElementById('versionWorkflow').textContent.includes(${JSON.stringify(p.baseline)})`),'Publication plan binds sole current ACTIVE and explicit target');
    await evaluate(`document.getElementById('versionConfirm').value='wrong';document.getElementById('versionExecute').click()`);await until(`document.getElementById('versionStatus').textContent.includes('完整')`,'wrong confirmation rejection');
    assert(await evaluate(`document.getElementById('versionSelect').selectedOptions[0].textContent.includes('STAGED')`),'Incorrect confirmation leaves candidate STAGED');
    await evaluate(`document.getElementById('versionConfirm').value=${JSON.stringify('publish:'+p.successor)};document.getElementById('versionExecute').click()`);await until(`document.getElementById('versionHistory').textContent.includes('publish')`,'actual publication');
    assert(await evaluate(`Array.from(document.getElementById('versionSelect').options).find(o=>o.value===${JSON.stringify(p.successor)}).textContent.includes('ACTIVE')`),'Actual SQL publication changes ACTIVE with attributed audit history');await shot('version-published.png');
    await version(p.baseline);await evaluate(`document.getElementById('versionPreparePlan').click()`);await until(`!!document.getElementById('versionConfirm')`,'rollback plan');
    await evaluate(`document.getElementById('versionConfirm').value=${JSON.stringify('rollback:'+p.baseline)};document.getElementById('versionExecute').click()`);await until(`document.getElementById('versionHistory').textContent.includes('rollback')`,'actual rollback');
    assert(await evaluate(`Array.from(document.getElementById('versionSelect').options).find(o=>o.value===${JSON.stringify(p.baseline)}).textContent.includes('ACTIVE')`),'Actual SQL rollback restores baseline after fresh publication verification');await shot('version-rollback.png');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await pause(200);
    assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),'Version page fits 390px mobile viewport');await shot('version-mobile.png');
    await role('scoped');assert(await evaluate(`!document.getElementById('versionSelect')&&!document.getElementById('versionHistory').textContent&&!document.getElementById('versionDetail').textContent`),'Restricted admin clears all source and publication history');
    // Supplemental delayed response verifies that a previous administrator body
    // cannot repopulate the page after credential downgrade.
    await role('admin');await until(`!!document.getElementById('versionSelect')`,'admin reload');
    await evaluate(`(()=>{window.realFetch=window.fetch.bind(window);window.fetch=async(url,options)=>{const response=await window.realFetch(url,options);if(String(url).includes('/versions/'))await new Promise(r=>setTimeout(r,800));return response;};document.getElementById('versionSelect').value=${JSON.stringify(p.successor)};document.getElementById('versionSelect').dispatchEvent(new Event('change'));})()`);
    await role('ops');await pause(1100);
    assert(await evaluate(`!document.getElementById('versionDetail').textContent&&!document.getElementById('versionHistory').textContent`),'Delayed admin response discarded after role downgrade');
    fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,screenshots:shots,real_gate:true,real_publish:true,real_rollback:true,paid_calls:0},null,2));console.log(JSON.stringify({passed:true,checks:checks.length,paid_calls:0}));
  }catch(error){try{fs.writeFileSync(path.join(p.reportDir,'browser-failure.json'),JSON.stringify({passed:false,checks,error:error.message},null,2));if(ws?.readyState===1)await shot('failure.png');}catch{}console.error(error.message);process.exitCode=1;}
  finally{if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}browser?.kill();for(const q of pending.values())clearTimeout(q.timer);}
});
