// Real Edge/API: execute the bounded no-model plan through product controls.
const fs=require('fs'),path=require('path'),cp=require('child_process');
let input='';process.stdin.on('data',chunk=>input+=chunk);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),pending=new Map(),checks=[],shots=[];let browser,ws,sequence=0;
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>reject(new Error('CDP timeout')),15000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error('Browser expression failed');return r.result.value;};
  const until=async(expression,label,attempts=300)=>{for(let i=0;i<attempts;i++){if(await evaluate(expression))return;await pause(100)}throw new Error('Timed out: '+label);};
  const assert=(value,label)=>{if(!value)throw new Error(label);checks.push(label);};
  const shot=async name=>{fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));shots.push(name);};
  const role=async name=>{await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.keys[name])});window.dispatchEvent(new StorageEvent('storage',{key:'api_key'}));`);await until(`!document.getElementById('capabilitiesStatus').textContent.includes('正在')`,'capabilities');};
  const open=async()=>{await evaluate(`switchTab('eval')`);await until(`!document.getElementById('evalBody').hidden`,'evaluation index');};
  const select=async(value)=>{await evaluate(`document.getElementById('evalVersion').value=${JSON.stringify(value)};document.getElementById('evalVersion').dispatchEvent(new Event('change'));`);await until(`!document.getElementById('evalSla').textContent.includes('正在')`,'report selection');};
  try{
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+path.join(p.reportDir,'edge-profile'),'--remote-debugging-port=19338','--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
    let targets;for(let i=0;i<100;i++){try{targets=await(await fetch('http://127.0.0.1:19338/json/list')).json();if(targets.length)break}catch{}await pause(100)}
    if(!targets?.length)throw new Error('Edge unavailable');ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});ws.onmessage=e=>{const r=JSON.parse(e.data),t=pending.get(r.id);if(t){clearTimeout(t.timer);pending.delete(r.id);r.error?t.reject(new Error('CDP failed')):t.resolve(r.result)}};
    await call('Network.enable');await call('Network.setBlockedURLs',{urls:['*api.deepseek.com*']});
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:p.loginUrl});await until(`!!document.getElementById('open')`,'private launcher');await evaluate(`document.getElementById('open').click()`);await until(`typeof switchTab==='function'`,'platform');
    await evaluate(`(()=>{const real=window.fetch.bind(window);window.evalWrites=[];window.evalRequests=[];window.fetch=(url,options={})=>{const method=(options.method||'GET').toUpperCase();window.evalRequests.push(String(url));if(method!=='GET'){window.evalWrites.push(String(url));if(!['/admin/eval/plans','/admin/eval/jobs'].includes(String(url)))throw new Error('Unexpected write forbidden');}return real(url,options);};})()`);
    await role('admin');await open();
    if(p.verifySnapshot){assert(await evaluate(`document.getElementById('evalSnapshot').textContent.includes('按报告显式知识版本归属')`),'Report detail retains the index read time and version scope');assert((await evaluate('window.evalWrites')).length===0,'Snapshot verification is read only');await shot('evaluation-snapshot.png');fs.writeFileSync(path.join(p.reportDir,'snapshot-verification.json'),JSON.stringify({passed:true,checks,screenshots:shots,paid_calls:0},null,2));console.log(JSON.stringify({passed:true,checks:checks.length,paid_calls:0}));return;}
    assert(await evaluate(`document.getElementById('evalVersion').value===${JSON.stringify(p.version)}`),'Default selection is actual ACTIVE knowledge version');
    assert(await evaluate(`document.getElementById('evalIndex').textContent.includes('缺少已验证的独立评测')`),'Current independent quality gap is explicit');
    await evaluate(`(()=>{const list=document.getElementById('evalReport');list.value=[...list.options].find(x=>x.textContent.includes('结构门禁')).value;list.dispatchEvent(new Event('change'));})()`);await until(`document.getElementById('evalGate').textContent.includes('sop_integrity')`,'current gate selected');
    assert(await evaluate(`document.getElementById('evalGate').textContent.includes('sop_integrity')&&document.getElementById('evalSla').textContent.includes('入库结构门禁')`),'Current structural gate has its own type and reported sample counts');
    assert(await evaluate(`document.getElementById('evalCards').textContent.includes('未评测')`),'Gate records are not converted into QA pass denominators');await shot('current-gate.png');
    await select('v4-foundation-r3');
    assert(await evaluate(`document.getElementById('evalSla').textContent.includes('reports/eval_')&&document.getElementById('evalStatus').textContent.includes('历史版本')`),'Historical report source and historical version attribution are visible');
    assert(await evaluate(`!document.getElementById('evalPrepare')`),'Historical version cannot create current regression plan');await shot('historical-report.png');
    await select('');assert(await evaluate(`document.getElementById('evalSla').textContent.includes('版本缺失或冲突')&&document.getElementById('evalStatus').textContent.includes('归属不明')`),'Unversioned reports stay unattributed despite version-like filenames');await shot('unattributed-report.png');
    // Inject only a report read failure; original metadata/identities remain real.
    await evaluate(`window.evalFetch=window.fetch;window.fetch=(url,options={})=>String(url).startsWith('/admin/eval/reports/')?Promise.resolve(new Response('{}',{status:503})):window.evalFetch(url,options);`);
    await select(p.version);assert(await evaluate(`document.getElementById('evalSla').textContent.includes('服务暂不可用')`),'Report read failure has a visible explanation');
    await evaluate(`window.fetch=window.evalFetch`);await open();
    await evaluate(`document.getElementById('evalPrepare').click()`);await until(`!!document.getElementById('evalExecute')`,'plan prepared');
    assert(await evaluate(`document.getElementById('evalJobStatus').textContent.includes('31题')&&document.getElementById('evalJobStatus').textContent.includes('no_llm')`),'Bound plan exposes 31 cases and zero-model scope before execution');await shot('prepared-plan.png');
    await evaluate(`document.getElementById('evalExecute').click()`);await until(`!!document.getElementById('evalViewResult')`,'real regression completed',1500);
    assert(await evaluate(`document.getElementById('evalJobStatus').textContent.includes('completed')&&document.getElementById('evalJobStatus').textContent.includes('31/31')`),'Real product plan execution reaches durable 31/31 progress');
    await evaluate(`document.getElementById('evalViewResult').click()`);await until(`!document.getElementById('evalBody').hidden&&document.getElementById('evalSla').textContent.includes('current-readable-faq-self-regression')`,'new report indexed');
    assert(await evaluate(`document.getElementById('evalCards').textContent.includes('31/31')&&document.getElementById('evalStatus').textContent.includes('当前版本')`),'New report has current version and actual 31-case boolean denominators');
    assert(await evaluate(`document.getElementById('evalIndex').textContent.includes('缺少已验证的独立评测')&&document.getElementById('evalSla').textContent.includes('没有文档生成')`),'Successful self regression retains independent and generation quality gaps');
    assert(await evaluate(`document.getElementById('evalWorkflow').textContent.includes('completed')&&document.getElementById('evalWorkflow').textContent.includes('31/31')`),'Refresh recovers completed task history');await shot('current-regression.png');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await pause(150);
    assert(!await evaluate(`document.documentElement.scrollWidth>innerWidth`),'Evaluation workspace fits 390px without horizontal overflow');await shot('evaluation-mobile.png');
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await role('auditor');await open();assert(await evaluate(`!document.getElementById('evalPrepare')&&document.getElementById('evalWorkflow').textContent.includes('只读')`),'Actual auditor can read reports and jobs but has no execution control');await shot('auditor-read-only.png');
    await role('ops');await evaluate(`switchTab('eval')`);await until(`document.getElementById('evalAccess').textContent.includes('当前账号无法查看')`,'ops restricted');
    assert(await evaluate(`document.getElementById('evalBody').hidden&&!document.getElementById('evalSla').textContent&&!document.getElementById('evalWorkflow').textContent`),'Downgrade clears evaluation report data and pending controls');
    const writes=await evaluate(`window.evalWrites`);assert(writes.length===2,'Only plan preparation and single bounded job were submitted');
    fs.writeFileSync(path.join(p.reportDir,'browser-result.json'),JSON.stringify({passed:true,checks,screenshots:shots,paid_calls:0,submitted_writes:writes,actual_case_count:31,injected_faults:['report detail HTTP503']},null,2));
    console.log(JSON.stringify({passed:true,checks:checks.length,screenshots:shots.length,paid_calls:0}));
  }catch(error){if(ws?.readyState===1){try{await shot('failure.png');fs.writeFileSync(path.join(p.reportDir,'failure.json'),JSON.stringify({checks,error:error.message}));}catch{}}console.error(error.message);process.exitCode=1;}
  finally{if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}browser?.kill();for(const q of pending.values())clearTimeout(q.timer);}
});
