// Real product upload, real staged-file failure, manual retry and BGE ingestion.
const fs=require('fs'),path=require('path'),cp=require('child_process');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),pending=new Map(),checks=[],shots=[];let browser,ws,seq=0,restore;
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>reject(new Error('CDP timeout')),15000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error('Browser expression failed');return r.result.value;};
  const until=async(expression,label,n=400)=>{for(let i=0;i<n;i++){if(await evaluate(expression))return;await pause(100)}throw new Error('Timed out: '+label);};
  const assert=(yes,label)=>{if(!yes)throw new Error(label);checks.push(label);};
  const shot=async name=>{fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));shots.push(name);};
  const command=action=>new Promise((resolve,reject)=>cp.execFile(p.python,['-m','scripts.workspace_ingest_worker',action],{cwd:p.project,windowsHide:true,timeout:140000},(error,stdout)=>error?reject(new Error('Dedicated Worker '+action+' failed')):resolve(stdout)));
  const request=async url=>{const r=await fetch(p.url+url,{headers:{'X-API-Key':p.keys.admin}});if(!r.ok)throw new Error('Verification read HTTP '+r.status);return r.json();};
  const role=async name=>{await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.keys[name])});window.dispatchEvent(new StorageEvent('storage',{key:'api_key'}));`);await until(`!document.getElementById('capabilitiesStatus').textContent.includes('正在')`,'capabilities');};
  const open=async()=>{await evaluate(`switchTab('docs')`);await until(`!!document.getElementById('ingestWorker').textContent`,'workspace');};
  const upload=async(name,source,bytes)=>{
    await evaluate(`(()=>{window.lastUpload=null;const d=new DataTransfer();d.items.add(new File([Uint8Array.from(atob(${JSON.stringify(bytes)}),c=>c.charCodeAt(0))],${JSON.stringify(name)}));document.getElementById('fileInput').files=d.files;document.getElementById('uploadSource').value=${JSON.stringify(source)};document.getElementById('uploadForm').requestSubmit();})()`);
    await until(`!!window.lastUpload`,'upload response');const result=await evaluate(`window.lastUpload`);
    await until(`!!document.querySelector('[data-upload-id="${result.id}"]')`,'upload card');return result;
  };
  try{
    if(!p.snapshotOnly)await command('stop');
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+path.join(p.reportDir,'edge-profile'),'--remote-debugging-port=19339','--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
    let targets;for(let i=0;i<100;i++){try{targets=await(await fetch('http://127.0.0.1:19339/json/list')).json();if(targets.length)break}catch{}await pause(100)}
    if(!targets?.length)throw new Error('Edge unavailable');ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl);
    await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});ws.onmessage=e=>{const r=JSON.parse(e.data),t=pending.get(r.id);if(t){clearTimeout(t.timer);pending.delete(r.id);r.error?t.reject(new Error('CDP failed')):t.resolve(r.result)}};
    await call('Network.enable');await call('Network.setBlockedURLs',{urls:['*api.deepseek.com*']});await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:p.loginUrl});await until(`!!document.getElementById('open')`,'private launcher');await evaluate(`document.getElementById('open').click()`);await until(`typeof switchTab==='function'`,'platform');
    await evaluate(`(()=>{const real=window.fetch.bind(window);window.ingestWrites=[];window.fetch=async(url,o={})=>{const method=(o.method||'GET').toUpperCase();if(method!=='GET'){if(!String(url).startsWith('/admin/ingest-workspace/'))throw new Error('Unexpected mutation');window.ingestWrites.push(String(url));}const r=await real(url,o);if(r.ok&&method==='POST'){const data=await r.clone().json();if(String(url).endsWith('/candidates'))window.lastCandidate=data;else if(String(url).includes('/uploads?'))window.lastUpload=data;}return r;};})()`);
    await role('admin');await open();
    assert(await evaluate(`!document.getElementById('candidateForm').hidden`),'Real default administrator has candidate creation');
    assert(await evaluate(`document.getElementById('candidateCompatibility').textContent.includes('历史候选')`),'Historical and sealed candidates explicitly incompatible');
    if(p.snapshotOnly){
      assert(await evaluate(`document.getElementById('ingestWorker').textContent.includes('ready')&&document.getElementById('ingestWorker').textContent.includes('配置匹配')`),'Persistent dedicated Worker remains ready with matching binding');
      await evaluate(`window.ingestRealFetch=window.fetch;window.fetch=(u,o={})=>String(u)==='/admin/ingest-workspace'?Promise.resolve(new Response('{}',{status:503})):window.ingestRealFetch(u,o);document.getElementById('jobRefresh').click();`);
      await until(`document.getElementById('ingestWorker').textContent.includes('无法确认')`,'workspace read failure');
      assert(await evaluate(`document.getElementById('candidateForm').hidden&&document.getElementById('uploadForm').hidden&&!document.getElementById('candidateCompatibility').textContent`),'Workspace HTTP503 explains uncertainty and clears stale write controls');await shot('workspace-unavailable.png');
      await evaluate(`window.fetch=window.ingestRealFetch`);await open();
      assert(await evaluate(`!document.getElementById('candidateForm').hidden&&document.getElementById('ingestWorker').textContent.includes('ready')`),'Refresh restores real compatible workspace after read failure');await shot('final-workspace.png');
      assert(!await evaluate(`!!document.querySelector('[data-compatible="false"] [data-action="retry"]')`),'Incompatible historical tasks have no retry control');
      await evaluate(`document.querySelector('[data-upload-id="${p.approvedUpload}"] [data-action="ledger"]').click()`);
      await until(`document.getElementById('ingestLedger').textContent.includes('1片段')`,'read-only ledger');
      assert(await evaluate(`document.getElementById('ingestLedger').getBoundingClientRect().top<innerHeight`),'Ledger detail scrolls into view after product click');await shot('ledger-detail.png');
      await role('guest');await open();assert(await evaluate(`document.getElementById('uploadNotice').textContent.includes('仅可查阅')&&document.getElementById('uploadForm').hidden`),'Guest sees explicit read restriction instead of endless permission loading');
      assert((await evaluate('window.ingestWrites')).length===0,'Supplementary browser checks are read only');
      fs.writeFileSync(path.join(p.reportDir,'snapshot-verification.json'),JSON.stringify({passed:true,checks,screenshots:shots,paid_calls:0,injected_faults:['GET workspace HTTP503 only']},null,2));
      console.log(JSON.stringify({passed:true,snapshot_checks:checks.length,paid_calls:0}));return;
    }
    await evaluate(`document.getElementById('candidateLabel').value='第4项真实上传与失败重试验收';document.getElementById('candidateForm').requestSubmit()`);
    await until(`!!window.lastCandidate&&document.getElementById('uploadVersion').value===window.lastCandidate.version`,'compatible candidate created');
    const candidate=await evaluate(`window.lastCandidate.version`);
    assert(await evaluate(`!document.getElementById('uploadSubmit').disabled`),'Created compatible STAGED candidate is writable');
    const fresh=await upload('manual_pending_demo.md','unreviewed-new-demo',Buffer.from('# 新增待审核资料\n此正文尚未批准，不进入知识库。').toString('base64'));
    assert(!fresh.job_id&&fresh.admission_status==='pending','New unreviewed source is quarantined without a queue job');
    assert(await evaluate(`document.querySelector('[data-upload-id="${fresh.id}"]').textContent.includes('仅暂存')`),'Pending boundary is visible in product');
    const record=await upload(p.filename,p.sourceId,p.fileBase64);
    assert(!!record.job_id&&record.admission_status==='inherited_approved','Byte-identical reviewed source inherits existing identity and admission');
    assert(await evaluate(`document.getElementById('ingestWorker').textContent.includes('stopped')`),'Stopped dedicated Worker is visible while job stays queued');await shot('queued-and-pending.png');
    const staged=path.resolve(p.workerRoot,'uploads',record.id,p.filename),root=path.resolve(p.workerRoot)+path.sep;
    if(!staged.startsWith(root))throw new Error('Fault target escaped workspace');
    const held=staged+'.fault-held';fs.renameSync(staged,held);restore=()=>{if(fs.existsSync(held))fs.renameSync(held,staged);};
    await command('start');let detail;
    for(let i=0;i<90;i++){detail=await request('/admin/ingest-workspace/uploads/'+record.id);if(detail.job?.status==='failed')break;await pause(1000)}
    assert(detail.job?.status==='failed'&&detail.job.attempt_count===1,'Real staged-file removal produces durable failed attempt 1');
    fs.writeFileSync(path.join(p.reportDir,'first-failure.json'),JSON.stringify(detail,null,2));restore();restore=null;
    await until(`!!document.querySelector('[data-upload-id="${record.id}"] [data-action="retry"]')`,'manual retry control');await shot('real-failure.png');
    await evaluate(`document.querySelector('[data-upload-id="${record.id}"] [data-action="retry"]').click()`);
    for(let i=0;i<240;i++){detail=await request('/admin/ingest-workspace/uploads/'+record.id);if(['success','failed'].includes(detail.job?.status)&&detail.job.attempt_count===2)break;await pause(1000)}
    assert(detail.job?.status==='success'&&detail.job.attempt_count===2,'Product manual retry completes real BGE ingestion on attempt 2');
    assert(detail.ledger.some(x=>x.filename===p.filename&&x.status==='done'&&x.chunk_count===1),'Real candidate SQL ledger records one completed FAQ fragment');
    await until(`document.querySelector('[data-upload-id="${record.id}"]').textContent.includes('入库完成')`,'success card');
    await evaluate(`document.querySelector('[data-upload-id="${record.id}"] [data-action="ledger"]').click()`);
    await until(`document.getElementById('ingestLedger').textContent.includes('1片段')`,'ledger details');
    assert(await evaluate(`document.getElementById('ingestLedger').textContent.includes(${JSON.stringify(record.sha256)})`),'Ledger exposes file checksum and stable source identity');await shot('completed-ledger.png');
    const duplicate=await upload(p.filename,p.sourceId,p.fileBase64);assert(duplicate.created===false&&duplicate.job_id===record.job_id,'Repeated identical product upload returns the original job');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await pause(200);
    assert(!await evaluate(`document.documentElement.scrollWidth>innerWidth`),'Upload workspace fits 390px without horizontal overflow');await shot('upload-mobile.png');
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await role('auditor');await open();assert(await evaluate(`document.getElementById('candidateForm').hidden&&document.getElementById('uploadForm').hidden&&!document.querySelector('#ingestJobs [data-action="retry"]')`),'Auditor sees real status and ledger with no write controls');
    await role('ops');await open();assert(await evaluate(`document.getElementById('candidateForm').hidden&&document.getElementById('uploadForm').hidden&&document.getElementById('fileInput').files.length===0&&document.getElementById('uploadSource').value===''`),'Credential downgrade clears upload data and write controls');await shot('ops-read-only.png');
    fs.writeFileSync(path.join(p.reportDir,'browser-result.json'),JSON.stringify({passed:true,checks,screenshots:shots,candidate,approved_upload:record.id,pending_upload:fresh.id,job_id:record.job_id,paid_calls:0,submitted_writes:await evaluate('window.ingestWrites'),injected_faults:['Actual uploaded staged file temporarily removed; restored before manual retry']},null,2));
    console.log(JSON.stringify({passed:true,checks:checks.length,candidate,job_id:record.job_id,paid_calls:0}));
  }catch(error){if(ws?.readyState===1){try{await shot('failure.png');fs.writeFileSync(path.join(p.reportDir,'failure.json'),JSON.stringify({checks,error:error.message}));}catch{}}console.error(error.message);process.exitCode=1;}
  finally{restore?.();if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}browser?.kill();for(const q of pending.values())clearTimeout(q.timer);}
});
