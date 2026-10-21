// Isolated Edge/CDP smoke; no browser packages and no existing user profile.
const fs = require('fs'), cp = require('child_process');
let input = ''; process.stdin.on('data', d => input += d);
process.stdin.on('end', async () => {
  const p = JSON.parse(input), pending = new Map(); let sequence = 0, ws, browser;
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout')); }, 15000);
    pending.set(id, {resolve, reject, timer}); ws.send(JSON.stringify({id, method, params}));
  });
  try {
    browser = cp.spawn(p.edge, ['--headless=new', '--disable-gpu', '--no-first-run',
      '--no-default-browser-check', `--user-data-dir=${p.profile}`, `--remote-debugging-port=${p.debugPort}`,
      '--remote-debugging-address=127.0.0.1', 'about:blank'], {windowsHide:true, stdio:'ignore'});
    let targets;
    for (let i=0;i<100;i++) {
      try { targets = await (await fetch(`http://127.0.0.1:${p.debugPort}/json/list`)).json(); if(targets.length) break; } catch {}
      await pause(100);
    }
    if (!targets?.length) throw new Error('isolated browser unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve,reject) => {ws.onopen=resolve;ws.onerror=reject;});
    ws.onmessage = e => {const data=JSON.parse(e.data), item=pending.get(data.id); if(!item)return;
      clearTimeout(item.timer);pending.delete(data.id);data.error?item.reject(new Error('CDP failure')):item.resolve(data.result);};
    const evaluate = async expression => {
      const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
      if(result.exceptionDetails) throw new Error('browser script failed'); return result.result.value;
    };
    await call('Emulation.setDeviceMetricsOverride',{width:1280,height:1100,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:p.url});
    for(let i=0;i<100;i++){if(await evaluate('typeof newAssistant === "function"'))break;await pause(100);}
    await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.key)});`);
    const sources=p.evidence.map(e=>({...e,doc_id:e.chunk_id,doc_type:'manual',score:0.8}));
    await evaluate(`window.smokeNode=newAssistant(); renderAnswer(smokeNode.body,'【结论】三格式原文查看验收（模拟数据，不调用生成模型） [1] [2] [3]');
      addMeta(smokeNode.root,{route:'retrieval',intent:'knowledge',source:'fixture',score:1},${JSON.stringify(sources)},null,false,[],${JSON.stringify(p.version)});
      smokeNode.root.querySelector('details.src').open=true;`);
    let body;
    for(let i=0;i<150;i++) {
      body=await evaluate(`Array.from(smokeNode.root.querySelectorAll('.chunkview')).map(e=>e.textContent)`);
      if(body.length===3 && body.every(t=>t.includes('Unique')))break;await pause(100);
    }
    if(!body?.every(t=>t.includes('Unique'))) throw new Error('authorized source panel did not load');
    if(!body[0].includes('行')||!body[1].includes('页')||!body[2].includes('段'))throw new Error('location labels missing');
    await evaluate('smokeNode.root.scrollIntoView({block:"start"})');
    const shot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(p.screenshot,Buffer.from(shot.data,'base64'));
    await evaluate(`window.flowNode=newAssistant();renderAnswer(flowNode.body,'流程子引用验收 [1]');
      addMeta(flowNode.root,{route:'retrieval',intent:'procedure_query',source:'fixture',score:1},[${JSON.stringify(p.flow)}],null,false,[],${JSON.stringify(p.version)});
      flowNode.root.querySelector('button').click();`);
    let flowText='';
    for(let i=0;i<100;i++){flowText=await evaluate(`flowNode.root.querySelector('.chunkview').textContent`);
      if(/步骤\s*2[:：]/.test(flowText))break;await pause(100);}
    if(!/步骤\s*1[:：]/.test(flowText)||!/步骤\s*2[:：]/.test(flowText))throw new Error('workflow children did not expand: '+flowText.slice(0,300));
    // Reopen with invalid credentials: previous original must not remain visible.
    await evaluate(`smokeNode.root.querySelector('details.src').open=false; localStorage.setItem('api_key','invalid-smoke-key');`);
    await pause(100);await evaluate(`smokeNode.root.querySelector('details.src').open=true;`);
    for(let i=0;i<100;i++){body=await evaluate(`Array.from(smokeNode.root.querySelectorAll('.chunkview')).map(e=>e.textContent)`);
      if(body.every(t=>t.includes('API Key')))break;await pause(100);}
    if(body.some(t=>t.includes('Unique'))||!body.every(t=>t.includes('API Key')))throw new Error('stale text after auth failure');
    console.log('browser: three formats, workflow children and auth-failure clearing passed');
  } catch(error) { console.error(error.message);process.exitCode=1; }
  finally {
    if(ws?.readyState===1) {try{await call('Browser.close');}catch{} ws.close();}
    browser?.kill();for(const item of pending.values())clearTimeout(item.timer);
  }
});
