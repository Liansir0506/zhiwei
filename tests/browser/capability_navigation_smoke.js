// Real Edge and API identities; only transport delays/failures are injected.
const fs=require('fs'),path=require('path'),cp=require('child_process');
let input='';process.stdin.on('data',chunk=>input+=chunk);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),pending=new Map(),checks=[],shots=[];
  let browser,ws,sequence=0;
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>reject(new Error('CDP timeout')),15000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails){let detail=(r.exceptionDetails.exception?.description||r.exceptionDetails.text).split('\n')[0];for(const key of Object.values(p.keys))detail=detail.replaceAll(key,'[credential]');throw new Error('Browser DOM: '+detail);}return r.result.value;};
  const until=async(expression,label)=>{for(let i=0;i<200;i++){if(await evaluate(expression))return;await pause(100)}throw new Error('Timed out: '+label);};
  const assert=(value,label)=>{if(!value)throw new Error(label);checks.push(label);};
  const shot=async name=>{fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));shots.push(name);};
  const role=async name=>{await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.keys[name])});window.dispatchEvent(new StorageEvent('storage',{key:'api_key'}));`);};
  const settled=()=>until(`!document.getElementById('capabilitiesStatus').textContent.includes('正在')`,'capabilities settled');
  const visible=()=>evaluate(`[...document.querySelectorAll('.nav-item')].filter(x=>!x.hidden).map(x=>x.dataset.tab)`);
  const open=async view=>{await evaluate(`switchTab('${view}')`);await until(`!document.getElementById('${view}Status').textContent.includes('正在')`,'view settled');};
  try{
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+path.join(p.reportDir,'edge-profile'),'--remote-debugging-port=19337','--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
    let targets;for(let i=0;i<100;i++){try{targets=await(await fetch('http://127.0.0.1:19337/json/list')).json();if(targets.length)break}catch{}await pause(100)}
    if(!targets?.length)throw new Error('Edge unavailable');ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});ws.onmessage=e=>{const r=JSON.parse(e.data),t=pending.get(r.id);if(t){clearTimeout(t.timer);pending.delete(r.id);r.error?t.reject(new Error('CDP failed')):t.resolve(r.result)}};
    await call('Network.enable');await call('Network.setBlockedURLs',{urls:['*api.deepseek.com*']});
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:p.loginUrl});await until(`!!document.getElementById('open')`,'actual private launcher');await evaluate(`document.getElementById('open').click()`);
    await until(`typeof switchTab==='function'`,'actual platform');
    await evaluate(`(()=>{const real=window.fetch.bind(window);window.auditRequests=[];window.auditWrites=0;window.auditRealFetch=(url,options={})=>{if((options.method||'GET').toUpperCase()!=='GET'){window.auditWrites++;throw new Error('Browser writes forbidden')}window.auditRequests.push(String(url));return real(url,options);};window.fetch=async(url,options={})=>{if(String(url)==='/admin/capabilities')await new Promise(r=>setTimeout(r,500));return window.auditRealFetch(url,options);};GovernanceWorkspace.credentialsChanged();})()`);
    assert(await evaluate(`[...document.querySelectorAll('.nav-item[data-tab="ops"],.nav-item[data-tab="eval"],.nav-item[data-tab="perm"]')].every(x=>x.hidden)`),'Governance navigation hidden while capabilities are verified');
    await settled();assert(JSON.stringify(await visible())===JSON.stringify(['qa','agent','kb','docs','ops']),'Actual ops sees only authorized governance navigation');
    await shot('ops-navigation.png');
    for(const view of ['eval','perm']){
      const before=await evaluate(`window.auditRequests.filter(x=>x==='/admin/${view==='eval'?'eval':'permissions'}').length`);
      await open(view);
      assert(await evaluate(`document.getElementById('${view}Body').hidden&&!document.getElementById('${view}Access').hidden&&document.getElementById('${view}Access').textContent.includes('当前账号无法查看')`),'Ops direct '+view+' renders a restricted card without empty panels');
      assert((await evaluate(`window.auditRequests.filter(x=>x==='/admin/${view==='eval'?'eval':'permissions'}').length`))===before,'Ops direct '+view+' never fetches protected records');
      await shot('ops-restricted-'+view+'.png');
    }
    await evaluate(`document.querySelector('#permAccess .access-actions button:nth-child(2)').click()`);assert(await evaluate(`document.getElementById('view-qa').classList.contains('active')`),'Restricted page returns to QA');
    await evaluate(`document.getElementById('keyBtn').click();document.getElementById('keyInput').value=${JSON.stringify(p.keys.admin)};document.getElementById('keySave').click()`);await settled();
    assert((await visible()).includes('eval')&&(await visible()).includes('perm'),'Manual credential save restores actual admin navigation');
    await open('perm');assert(await evaluate(`!document.getElementById('permBody').hidden&&!document.getElementById('keyIssuePanel').hidden&&document.getElementById('permKeys').textContent.includes(${JSON.stringify(p.adminActor)})`),'Admin permission page and management entry remain usable');await shot('admin-permissions.png');
    await open('eval');assert(await evaluate(`!document.getElementById('evalBody').hidden&&document.getElementById('evalAccess').hidden`),'Admin evaluation page remains usable');await shot('admin-evaluation.png');
    await role('auditor');await settled();await open('perm');assert(await evaluate(`!document.getElementById('permBody').hidden&&document.getElementById('keyIssuePanel').hidden&&!document.querySelector('#permKeys button')`),'Auditor is read only and cannot issue or revoke keys');
    await role('tenant_admin');await settled();assert(JSON.stringify(await visible())===JSON.stringify(['qa','agent','kb','docs','perm']),'Other tenant admin cannot see global ops or eval navigation');await open('ops');assert(await evaluate(`document.getElementById('opsBody').hidden&&document.getElementById('opsAccess').textContent.includes('default')`),'Other tenant direct global view has complete restricted explanation');
    await role('guest');await settled();assert((await visible()).length===4&&await evaluate(`document.getElementById('governanceNavHeading').hidden`),'Guest has no misleading governance group');
    await role('admin');await settled();await open('perm');await evaluate(`document.getElementById('nkKey').value='temporary-input-only';`);await role('ops');
    assert(await evaluate(`document.getElementById('permBody').hidden&&!document.getElementById('permKeys').textContent&&document.getElementById('nkKey').value===''&&document.getElementById('keyIssuePanel').hidden`),'Credential downgrade synchronously clears protected data and input');await settled();await until(`document.getElementById('permAccess').textContent.includes('当前账号无法查看')`,'downgrade restriction');
    // Keep actual old responses in memory and delay them beyond a credential change.
    await evaluate(`window.fetch=async(url,options={})=>{const r=await window.auditRealFetch(url,options);if(String(url)==='/admin/capabilities'&&options.headers?.['X-API-Key']===${JSON.stringify(p.keys.admin)}){const text=await r.text();window.delayedCapsReady=true;await new Promise(resolve=>setTimeout(resolve,900));return new Response(text,{status:r.status,headers:r.headers});}return r;};window.delayedCapsReady=false;`);
    await role('admin');await until(`window.delayedCapsReady`,'old admin capability response');await role('ops');await settled();await pause(1100);
    assert(!(await visible()).includes('perm')&&await evaluate(`document.getElementById('permBody').hidden`),'Delayed admin capabilities cannot restore ops navigation or data');
    await evaluate(`window.fetch=window.auditRealFetch;`);await role('admin');await settled();await open('perm');
    await evaluate(`window.fetch=async(url,options={})=>{const r=await window.auditRealFetch(url,options);if(String(url)==='/admin/permissions'){const text=await r.text();window.delayedDataReady=true;await new Promise(resolve=>setTimeout(resolve,900));return new Response(text,{status:r.status,headers:r.headers});}return r;};window.delayedDataReady=false;switchTab('perm');`);await until(`window.delayedDataReady`,'old admin permission response');await role('ops');await settled();await pause(1100);
    assert(await evaluate(`document.getElementById('permBody').hidden&&!document.getElementById('permKeys').textContent&&!document.getElementById('permMatrix').textContent`),'Delayed privileged records cannot reappear after downgrade');
    await evaluate(`window.fetch=window.auditRealFetch;localStorage.setItem('api_key','invalid-navigation-audit-key');window.dispatchEvent(new StorageEvent('storage',{key:'api_key'}));`);await settled();await until(`document.getElementById('permAccess').textContent.includes('登录已失效')`,'invalid key explanation');assert((await visible()).length===4,'Invalid credentials do not preserve authorized governance navigation');await shot('invalid-credential.png');
    await role('ops');await settled();await evaluate(`window.fetch=async(url,options={})=>String(url)==='/admin/capabilities'?new Response('{}',{status:503}):window.auditRealFetch(url,options);document.getElementById('capabilitiesRetry').click();`);await settled();assert((await visible()).length===4,'Unavailable capability service fails closed');
    await evaluate(`window.fetch=window.auditRealFetch;document.getElementById('capabilitiesRetry').click()`);await settled();assert((await visible()).includes('ops'),'Capability retry restores authorized navigation');
    await open('eval');await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await pause(100);
    assert(!await evaluate(`document.documentElement.scrollWidth>innerWidth`),'Restricted page has no mobile horizontal overflow');await shot('ops-restricted-mobile.png');
    await evaluate(`localStorage.clear();window.dispatchEvent(new StorageEvent('storage',{key:null}));`);await settled();assert((await visible()).length===4&&await evaluate(`document.getElementById('evalBody').hidden`),'Cross tab storage clear removes access and data');
    fs.writeFileSync(path.join(p.reportDir,'browser-result.json'),JSON.stringify({passed:true,checks,screenshots:shots,paid_calls:0,qa_submitted:false,transport_faults_injected:['capability delay','permission response delay','capability 503'],actual_identity_roles:['ops','admin','auditor','tenant_admin','guest']},null,2));
    console.log(JSON.stringify({passed:true,checks:checks.length,screenshots:shots.length,paid_calls:0}));
  }catch(error){if(ws?.readyState===1){try{await shot('failure.png');fs.writeFileSync(path.join(p.reportDir,'failure.json'),JSON.stringify({checks,error:error.message}));}catch{}}console.error(error.message);process.exitCode=1;}
  finally{if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}browser?.kill();for(const q of pending.values())clearTimeout(q.timer);}
});
