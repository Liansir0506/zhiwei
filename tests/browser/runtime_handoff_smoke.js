// Live profile preparation only: no activation, rollback or model requests.
const fs=require('fs'),path=require('path'),cp=require('child_process');let input='';
process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),pending=new Map(),checks=[],shots=[];let browser,ws,seq=0;
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>reject(new Error('CDP timeout')),20000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error('Browser expression failed');return r.result.value;};
  const until=async(expression,label,n=1600)=>{for(let i=0;i<n;i++){if(await evaluate(expression))return;await pause(100)}throw new Error('Timed out: '+label);};
  const assert=(yes,label)=>{if(!yes)throw new Error(label);checks.push(label);};
  const shot=async name=>{fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));shots.push(name);};
  const role=async name=>{await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.keys[name])});GovernanceWorkspace.credentialsChanged();VersionWorkspace.credentialsChanged();switchTab('versions')`);await until(`!document.getElementById('versionStatus').textContent.includes('正在')`,'role');};
  try{
    let occupied=false;try{await fetch('http://127.0.0.1:19345/json/version',{signal:AbortSignal.timeout(1000)});occupied=true;}catch{}if(occupied)throw new Error('Browser port occupied');
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+path.join(p.reportDir,'edge-profile'),'--remote-debugging-port=19345','--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
    let targets;for(let i=0;i<100;i++){try{targets=await(await fetch('http://127.0.0.1:19345/json/list')).json();if(targets.length)break;}catch{}await pause(100)}
    if(!targets?.length)throw new Error('Edge unavailable');ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});ws.onmessage=e=>{const r=JSON.parse(e.data),q=pending.get(r.id);if(q){clearTimeout(q.timer);pending.delete(r.id);r.error?q.reject(new Error('CDP failed')):q.resolve(r.result)}};
    await call('Network.enable');await call('Network.setBlockedURLs',{urls:['*api.deepseek.com*']});await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});await call('Page.navigate',{url:p.url});await until(`typeof switchTab==='function'&&!!window.VersionWorkspace`,'platform');
    await role('ops');assert(await evaluate(`!document.getElementById('versionPrepareHandoff')&&!document.getElementById('versionRuntimeHandoff')`),'Ops has no full-profile handoff data or preparation control');
    await role('admin');await until(`!!document.getElementById('versionPrepareHandoff')&&!!document.getElementById('versionSelect')`,'admin handoff');
    assert(await evaluate(`document.querySelectorAll('[data-handoff]').length>=2`),'Previously prepared current/delta plans persist through refresh');
    await evaluate(`document.getElementById('versionSelect').value=${JSON.stringify(p.current)}`);
    const before=await evaluate(`document.querySelectorAll('[data-handoff]').length`);
    await evaluate(`document.getElementById('versionPrepareHandoff').click()`);await until(`document.querySelectorAll('[data-handoff]').length>${before}`,'actual full-profile preparation');
    assert(await evaluate(`document.getElementById('versionRuntimeHandoff').textContent.includes('完整配置SHA256')&&document.getElementById('versionRuntimeHandoff').textContent.includes('上一完整恢复配置')`),'Actual current/previous full configurations and stable digests visible');
    assert(await evaluate(`document.getElementById('versionRuntimeHandoff').textContent.includes('尚未执行发布或回滚')`),'Preparation explicitly leaves original pointer unchanged');
    await shot('handoff-desktop.png');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await pause(200);
    assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),'390px handoff page has no horizontal overflow');await shot('handoff-mobile.png');
    await role('auditor');await until(`!!document.querySelector('[data-handoff]')`,'auditor prepared report');
    assert(await evaluate(`!document.getElementById('versionPrepareHandoff')&&!Array.from(document.querySelectorAll('#versionRuntimeHandoff button')).some(b=>b.textContent.includes('重新核验'))`),'Auditor reads handoff receipts without preparation or revalidation controls');
    await role('scoped');assert(await evaluate(`!document.getElementById('versionRuntimeHandoff')&&!document.getElementById('versionList').textContent`),'Scoped administrator clears full configuration data');
    // Supplemental transport delay: discard a late administrator plan list.
    await role('admin');await evaluate(`window.realFetch=window.fetch.bind(window);window.fetch=async(url,options)=>{const response=await window.realFetch(url,options);if(String(url).endsWith('/runtime-handoffs'))await new Promise(r=>setTimeout(r,800));return response;};VersionWorkspace.load()`);await pause(150);await role('ops');await pause(1100);
    assert(await evaluate(`!document.getElementById('versionRuntimeHandoff')&&!document.getElementById('versionList').textContent`),'Late full-profile responses cannot restore administrator data after downgrade');
    fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,screenshots:shots,actual_switch:false,paid_calls:0},null,2));console.log(JSON.stringify({passed:true,checks:checks.length,paid_calls:0}));
  }catch(error){try{fs.writeFileSync(path.join(p.reportDir,'browser-failure.json'),JSON.stringify({passed:false,checks,error:error.message},null,2));if(ws?.readyState===1)await shot('failure.png');}catch{}console.error(error.message);process.exitCode=1;}
  finally{if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}browser?.kill();for(const q of pending.values())clearTimeout(q.timer);}
});
