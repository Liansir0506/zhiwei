// Isolated Edge/CDP acceptance with real API keys, evidence and domain fixtures.
const fs = require('fs'), path = require('path'), cp = require('child_process');
let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', async () => {
  const p = JSON.parse(input), pending = new Map(), screenshots = [], checks = [];
  let sequence = 0, ws, browser;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout')); }, 15000);
    pending.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('browser expression: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };
  const until = async (expression, label) => {
    for (let i = 0; i < 150; i++) { if (await evaluate(expression)) return; await pause(100); }
    throw new Error(label + ' timed out');
  };
  const assert = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const size = async width => { await call('Emulation.setDeviceMetricsOverride', { width, height: 960, deviceScaleFactor: 1, mobile: false }); await pause(150); };
  const shot = async name => { const s = await call('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(p.reportDir, name), Buffer.from(s.data, 'base64')); screenshots.push(name); };
  const credentials = async key => {
    await evaluate(`document.getElementById('keyInput').value=${JSON.stringify(key)};document.getElementById('keySave').click();`);
  };
  try {
    browser = cp.spawn(p.edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      `--user-data-dir=${p.profile}`, `--remote-debugging-port=${p.debugPort}`, '--remote-debugging-address=127.0.0.1', 'about:blank'],
      { windowsHide: true, stdio: 'ignore' });
    let targets;
    for (let i = 0; i < 100; i++) {
      try { targets = await (await fetch(`http://127.0.0.1:${p.debugPort}/json/list`)).json(); if (targets.length) break; } catch {}
      await pause(100);
    }
    if (!targets?.length) throw new Error('isolated Edge unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    ws.onmessage = e => { const data = JSON.parse(e.data), item = pending.get(data.id); if (!item) return;
      clearTimeout(item.timer); pending.delete(data.id); data.error ? item.reject(new Error('CDP failure')) : item.resolve(data.result); };
    await size(1440); await call('Page.navigate', { url: p.url });
    await until('typeof ask === "function" && !!window.Workspace', 'workspace load');
    await credentials(p.keys.admin);
    await until(`[...document.getElementById('qaVersion').options].some(o=>o.value==='v4-protocol-dev-r1')`, 'real version list');
    assert(await evaluate(`document.getElementById('businessContent').textContent.includes('尚未关联') && document.getElementById('evidenceList').textContent.includes('还没有')`), 'honest empty states');
    await shot('empty-1440.png');
    await evaluate(`document.getElementById('contextId').value=${JSON.stringify(p.alarm)};document.getElementById('contextForm').requestSubmit();`);
    await until(`document.getElementById('businessContent').textContent.includes('fixture-1.0')`, 'real alarm context');
    assert(await evaluate(`document.getElementById('businessContent').textContent.includes('模拟系统来源') && !document.getElementById('businessContent').querySelector('img')`), 'real context and escaped labels');
    await evaluate(`ask('你好')`);
    await until(`!document.getElementById('send').disabled && loadMessages(sessionId).some(m=>m.role==='assistant'&&m.status==='ANSWERED')`, 'real SSE answer');
    const saved = await evaluate(`loadMessages(sessionId).find(m=>m.role==='assistant')`);
    assert(Boolean(saved.messageId) && saved.text.includes('您好'), 'real SSE and persistent message');
    await evaluate(`document.querySelector('.msg.assistant .fb button').click()`);
    await until(`document.querySelector('.msg.assistant .fb').textContent.includes('反馈已记录')`, 'real feedback POST');
    checks.push('feedback persisted through real HTTP');

    // A stored protocol excerpt is rendered in a controlled terminal frame. Evidence reads remain real HTTP.
    await evaluate(`document.getElementById('qaVersion').value='v4-protocol-dev-r1';document.getElementById('qaVersion').dispatchEvent(new Event('change'));`);
    await evaluate(`window._realFetch=window.fetch.bind(window);window.uiCalls=[];window.uiReply='evidence';
      window.fetch=(url,options)=>{if(String(url)==='/qa/sse'){
        uiCalls.push(JSON.parse(options.body).query);const nl=String.fromCharCode(10),encoder=new TextEncoder();
        const source=${JSON.stringify(p.evidence)};const answer='【原文摘要】'+source.text.slice(0,480)+'\\n【引用】[1]';
        const final=uiReply==='evidence'?{answer,status:'DEGRADED',degraded:true,kb_version:'v4-protocol-dev-r1'}:
          uiReply==='clarify'?{answer:'请补充设备与适用版本',status:'NEEDS_CLARIFICATION',kb_version:'v4-protocol-dev-r1'}:
          {answer:'该问题超出运维知识范围',status:'OUT_OF_SCOPE',kb_version:'v4-protocol-dev-r1'};
        const frames=[['meta',{kb_version:'v4-protocol-dev-r1'}],['token',{text:'不得保留的暂态草稿'}],
          ['sources',{sources:[source]}],['done',final]].map(([event,data])=>'event: '+event+nl+'data: '+JSON.stringify(data)+nl+nl);
        return Promise.resolve(new Response(new ReadableStream({start(c){frames.forEach(f=>c.enqueue(encoder.encode(f)));c.close();}}),
          {headers:{'Content-Type':'text/event-stream'}}));}return _realFetch(url,options);};ask('HTTP 503 的含义与适用条件是什么？');`);
    await until(`!document.getElementById('send').disabled && document.getElementById('evidenceCount').textContent==='1'`, 'terminal evidence list');
    await evaluate(`document.querySelector('#evidenceList button').click()`);
    await until(`document.getElementById('evidenceDetail').textContent.includes('503 表示')`, 'real original detail');
    assert(await evaluate(`document.getElementById('evidenceDetail').textContent.includes('权限校验') && !document.querySelectorAll('.answer')[1].textContent.includes('暂态草稿')`), 'verified original and final answer replaces draft');
    // Restore real context after knowledge-selection reset.
    await evaluate(`document.getElementById('contextId').value=${JSON.stringify(p.alarm)};document.getElementById('contextForm').requestSubmit();`);
    await until(`document.getElementById('businessContent').textContent.includes('fixture-1.0')`, 'screenshot context');
    for (const width of [1440,1280]) {
      await size(width);
      assert(await evaluate(`document.documentElement.scrollWidth<=${width} && document.querySelector('aside.right').getBoundingClientRect().width>250`), `desktop ${width} no overflow and evidence visible`);
      await shot(`answer-${width}.png`);
    }
    await size(390); await evaluate(`document.getElementById('conversationTab').click()`);
    assert(await evaluate(`document.documentElement.scrollWidth<=390 && document.getElementById('send').getBoundingClientRect().right<=390`), 'mobile composer reachable without overflow');
    await shot('answer-390.png');
    await evaluate(`document.getElementById('evidenceTab').click()`);
    assert(await evaluate(`getComputedStyle(document.querySelector('aside.right')).display!=='none' && document.body.classList.contains('context-open')`), 'mobile evidence drawer opens');
    await shot('evidence-390.png'); await evaluate(`document.getElementById('contextClose').click()`); await size(1440);

    await evaluate(`uiReply='clarify';ask('条件不充分的故障问题');`);
    await until(`!document.getElementById('send').disabled && loadMessages(sessionId).at(-1).status==='NEEDS_CLARIFICATION'`, 'clarification terminal');
    assert(await evaluate(`document.getElementById('evidenceCount').textContent==='0' && !document.getElementById('evidenceDetail').textContent`), 'clarification clears stale evidence');
    await evaluate(`uiReply='refuse';ask('域外问题');`);
    await until(`!document.getElementById('send').disabled && loadMessages(sessionId).at(-1).status==='OUT_OF_SCOPE'`, 'refusal terminal');
    assert(await evaluate(`!loadMessages(sessionId).at(-1).sources.length && document.getElementById('evidenceCount').textContent==='0'`), 'refusal strips provisional sources');

    // Explicit retry must repeat the failed question once; never replay automatically.
    await evaluate(`window.failCount=0;window.fetch=(url,options)=>String(url)==='/qa/sse'?(failCount++,Promise.resolve(new Response('',{status:429}))):_realFetch(url,options);ask('需要手动重试的问题');`);
    await until(`!document.getElementById('send').disabled && [...document.querySelectorAll('.err')].at(-1)?.textContent.includes('服务繁忙')`, '429 visible');
    await pause(300); assert(await evaluate('failCount===1'), 'no automatic SSE retry');
    await evaluate(`[...document.querySelectorAll('.err')].at(-1).querySelector('button').click()`);
    await until(`failCount===2 && !document.getElementById('send').disabled`, 'manual retry once');
    checks.push('explicit retry only');
    await evaluate('window.fetch=_realFetch');
    await evaluate(`document.getElementById('qaVersion').value='';document.getElementById('transportMode').value='ws';document.getElementById('transportMode').onchange();`);
    await until(`wsAlive && ws?.readyState===WebSocket.OPEN`, 'real WebSocket compatibility connection');
    const previousCount = await evaluate(`loadMessages(sessionId).filter(m=>m.role==='assistant').length`);
    await evaluate(`ask('你好')`);
    await until(`!document.getElementById('send').disabled && loadMessages(sessionId).filter(m=>m.role==='assistant').length===${previousCount+1}`, 'real WebSocket greeting');
    assert(await evaluate(`loadMessages(sessionId).at(-1).text.includes('您好')`), 'legacy WebSocket direct answer remains usable');
    await evaluate(`document.getElementById('transportMode').value='sse';document.getElementById('transportMode').onchange();`);
    await evaluate(`window.fetch=async(url,options)=>{const r=await _realFetch(url,options);if(String(url)==='/kb/versions'&&options.headers['X-API-Key']===${JSON.stringify(p.keys.admin)})await new Promise(resolve=>setTimeout(resolve,500));return r;};health();`);
    await credentials(p.keys.ops); await pause(900);
    await until(`[...document.getElementById('qaVersion').options].every(o=>!o.value)`, 'ops version list');
    checks.push('delayed admin version list cannot overwrite ordinary user permissions');
    await evaluate('window.fetch=_realFetch');
    assert(await evaluate(`!document.getElementById('evidenceDetail').textContent`), 'key change clears originals');
    await evaluate(`Workspace.setSources([${JSON.stringify(p.evidence)}],'v4-protocol-dev-r1','ANSWERED');document.querySelector('#evidenceList button').click()`);
    await until(`document.getElementById('evidenceDetail').textContent.includes('无权访问')`, 'ops denied staged evidence');
    checks.push('ordinary user cannot preview staged original');
    await credentials(p.keys.foreign);
    await evaluate(`document.getElementById('contextKind').value='device';document.getElementById('contextId').value=${JSON.stringify(p.device)};document.getElementById('contextForm').requestSubmit();`);
    await until(`document.getElementById('businessContent').textContent.includes('授权范围')`, 'cross tenant denied');
    assert(await evaluate(`!document.getElementById('businessContent').textContent.includes('fixture-1.0')`), 'cross tenant does not leak device');
    await credentials(p.keys.admin);
    await until(`[...document.getElementById('qaVersion').options].some(o=>o.value==='v4-protocol-dev-r1')`, 'admin restored');
    await evaluate(`window.fetch=async(url,options)=>{const r=await _realFetch(url,options);if(String(url).startsWith('/kb/evidence/'))await new Promise(resolve=>setTimeout(resolve,500));return r;};
      Workspace.setSources([${JSON.stringify(p.evidence)}],'v4-protocol-dev-r1','ANSWERED');document.querySelector('#evidenceList button').click();`);
    await credentials('invalid-workspace-key'); await pause(900);
    assert(await evaluate(`!document.getElementById('evidenceDetail').textContent.includes('503 表示') && !document.getElementById('businessContent').textContent.includes('fixture-1.0')`), 'in flight original cannot return after credentials change');
    await evaluate('window.fetch=_realFetch');
    await credentials(p.keys.admin);
    await evaluate('document.getElementById("newConversation").click()');
    assert(await evaluate(`!!document.querySelector('.welcome') && document.getElementById('evidenceCount').textContent==='0' && document.getElementById('businessContent').textContent.includes('尚未关联')`), 'new session clears evidence and context');

    await call('Page.navigate',{url:p.url+'/admin'});
    await until(`document.getElementById('refreshAt')?.textContent.includes('刷新于')`, 'admin page authenticated');
    assert(await evaluate(`document.getElementById('env').textContent==='TEST' && document.getElementById('kbv').textContent.includes('STAGED')`), 'admin page uses shared credential and real data');
    await evaluate(`localStorage.setItem('api_key','invalid-workspace-key');load();`);
    await until(`document.getElementById('adminStatus').textContent.includes('有效的 API Key')`, 'admin unauthorized');
    assert(await evaluate(`document.getElementById('kbv').textContent==='数据不可用'`), 'admin clears stale protected data');
    console.log(JSON.stringify({passed:true,checks,screenshots,paid_model_calls:0,
      answer_fixture:'controlled terminal frames; real protocol original reads',context_fixture:'temporary own MySQL device and alarm'}));
  } catch(error) {
    if (ws?.readyState===1) {
      try { await shot('failure.png'); fs.writeFileSync(path.join(p.reportDir,'failure.json'),JSON.stringify({checks,error:error.message,adminStatus:await evaluate(`document.getElementById('adminStatus')?.textContent||''`)})); } catch {}
    }
    console.error(error.message); process.exitCode=1;
  }
  finally {
    if(ws?.readyState===1){try{await call('Browser.close');}catch{}ws.close();}
    browser?.kill();for(const item of pending.values())clearTimeout(item.timer);
  }
});
