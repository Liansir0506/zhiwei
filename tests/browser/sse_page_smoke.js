// Isolated Edge/CDP: real HTTP SSE direct answer, then controlled terminal cases.
const fs = require('fs'), cp = require('child_process');
let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', async () => {
  const p = JSON.parse(input), pending = new Map();
  let sequence = 0, ws, browser;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout')); }, 15000);
    pending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error('browser expression failed');
    return result.result.value;
  };
  const until = async (expression, label) => {
    for (let i = 0; i < 150; i++) {
      if (await evaluate(expression)) return;
      await pause(100);
    }
    throw new Error(label + ' timed out');
  };
  try {
    browser = cp.spawn(p.edge, ['--headless=new', '--disable-gpu', '--no-first-run',
      '--no-default-browser-check', `--user-data-dir=${p.profile}`,
      `--remote-debugging-port=${p.debugPort}`, '--remote-debugging-address=127.0.0.1', 'about:blank'],
      { windowsHide: true, stdio: 'ignore' });
    let targets;
    for (let i = 0; i < 100; i++) {
      try { targets = await (await fetch(`http://127.0.0.1:${p.debugPort}/json/list`)).json();
        if (targets.length) break; } catch {}
      await pause(100);
    }
    if (!targets?.length) throw new Error('isolated Edge unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    ws.onmessage = event => {
      const data = JSON.parse(event.data), item = pending.get(data.id);
      if (!item) return;
      clearTimeout(item.timer); pending.delete(data.id);
      data.error ? item.reject(new Error('CDP failure')) : item.resolve(data.result);
    };
    await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900,
      deviceScaleFactor: 1, mobile: false });
    await call('Page.navigate', { url: p.url });
    await until('typeof ask === "function"', 'page load');
    await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.key)});
      localStorage.setItem('chat_transport','sse'); location.reload();`);
    await until('typeof ask === "function" && transportMode === "sse"', 'SSE page reload');
    await evaluate(`window.sseCalls=0; window._realFetch=window.fetch.bind(window);
      window.fetch=(url,options)=>{if(String(url)==='/qa/sse')sseCalls++;return _realFetch(url,options)}; ask('你好');`);
    await until(`!document.getElementById('send').disabled &&
      loadMessages(sessionId).filter(m=>m.role==='assistant').length===1`, 'real SSE answer');
    const real = await evaluate(`({calls:sseCalls, mode:transportMode,
      status:loadMessages(sessionId).filter(m=>m.role==='assistant')[0].status,
      text:loadMessages(sessionId).filter(m=>m.role==='assistant')[0].text})`);
    if (real.calls !== 1 || real.mode !== 'sse' || real.status !== 'ANSWERED' || !real.text.includes('您好'))
      throw new Error('real SSE answer not shown exactly once');

    await evaluate(`window.fetch=(url,options)=>{if(String(url)==='/qa/sse'){
      sseCalls++;const nl=String.fromCharCode(10);const frames=[
      ['event: meta','data: {"kb_version":"v1"}','',''].join(nl),
      ['event: token','data: {"text":"暂态错误答案"}','',''].join(nl),
      ['event: sources','data: {"sources":[]}','',''].join(nl),
      ['event: done','data: {"answer":"请补充设备版本","status":"NEEDS_CLARIFICATION","kb_version":"v1"}','',''].join(nl)];
      const e=new TextEncoder(); return Promise.resolve(new Response(new ReadableStream({start(c){
        frames.forEach(f=>c.enqueue(e.encode(f)));c.close();}}),{headers:{'Content-Type':'text/event-stream'}}));}
      return _realFetch(url,options)}; ask('模拟澄清');`);
    await until(`!document.getElementById('send').disabled &&
      loadMessages(sessionId).filter(m=>m.role==='assistant').length===2`, 'final answer replacement');
    const final = await evaluate(`({calls:sseCalls, last:loadMessages(sessionId).filter(m=>m.role==='assistant').at(-1),
      bubble:[...document.querySelectorAll('.msg.assistant')].at(-1)?.innerText})`);
    if (final.calls !== 2 || final.last.status !== 'NEEDS_CLARIFICATION' ||
        final.last.text !== '请补充设备版本' || final.bubble.includes('暂态错误答案') ||
        final.last.sources?.length) throw new Error('provisional text or sources leaked into final answer');

    await evaluate(`window.fetch=(url,options)=>{if(String(url)==='/qa/sse'){
      sseCalls++;const nl=String.fromCharCode(10);const frames=[
      ['event: meta','data: {"kb_version":"v4-protocol-dev-r1"}','',''].join(nl),
      ['event: sources','data: '+JSON.stringify({sources:[${JSON.stringify(p.evidence)}]}),'',''].join(nl),
      ['event: done','data: '+JSON.stringify({answer:'【结论】查看已批准摘要。[1]',status:'ANSWERED',
        kb_version:'v4-protocol-dev-r1'}),'',''].join(nl)];
      const e=new TextEncoder();return Promise.resolve(new Response(new ReadableStream({start(c){
        frames.forEach(f=>c.enqueue(e.encode(f)));c.close();}}),{headers:{'Content-Type':'text/event-stream'}}));}
      return _realFetch(url,options)}; ask('模拟引用展示');`);
    await until(`!document.getElementById('send').disabled &&
      loadMessages(sessionId).filter(m=>m.role==='assistant').length===3`, 'SSE final citation');
    await evaluate(`document.querySelectorAll('.msg.assistant')[2].querySelector('.answer .ref').click()`);
    await until(`document.querySelectorAll('.msg.assistant')[2].querySelector('.chunkview')?.textContent.includes('503 表示')`,
      'real authorized citation view');

    await evaluate(`window.fetch=(url,options)=>{if(String(url)==='/qa/sse'){
      sseCalls++;const e=new TextEncoder(); return Promise.resolve(new Response(new ReadableStream({start(c){
        const nl=String.fromCharCode(10);c.enqueue(e.encode([
          'event: meta','data: {}','','event: token','data: {"text":"不能保存的暂态"}','',''].join(nl)));
        c.close();}}),{headers:{'Content-Type':'text/event-stream'}}));}
      return _realFetch(url,options)}; ask('模拟中断');`);
    await until(`!document.getElementById('send').disabled &&
      [...document.querySelectorAll('.msg.assistant')].at(-1)?.textContent.includes('未正常结束')`,
      'interrupted SSE error');
    const broken = await evaluate(`({calls:sseCalls,
      assistantCount:loadMessages(sessionId).filter(m=>m.role==='assistant').length,
      last:[...document.querySelectorAll('.msg.assistant')].at(-1)?.textContent})`);
    if (broken.calls !== 4 || broken.assistantCount !== 3 || broken.last.includes('不能保存的暂态'))
      throw new Error('interrupted SSE was saved or replayed');
    for (const [status, label] of [[401, '认证失败'], [404, '知识版本不可用'], [429, '服务繁忙']]) {
      await evaluate(`window.fetch=(url,options)=>{if(String(url)==='/qa/sse'){
        sseCalls++;return Promise.resolve(new Response('',{status:${status}}));}
        return _realFetch(url,options)}; ask('模拟 HTTP ${status}');`);
      await until(`!document.getElementById('send').disabled &&
        [...document.querySelectorAll('.err')].at(-1)?.textContent.includes(${JSON.stringify(label)})`,
        `HTTP ${status} visible failure`);
      const failure = await evaluate(`({status:document.getElementById('status').textContent,
        assistantCount:loadMessages(sessionId).filter(m=>m.role==='assistant').length})`);
      if (!failure.status.includes('未完成') || failure.assistantCount !== 3)
        throw new Error(`HTTP ${status} was shown as success or persisted`);
    }
    await evaluate(`window.fetch=(url,options)=>{if(String(url)==='/qa/sse'){
      sseCalls++;const nl=String.fromCharCode(10);const frame=[
        'event: meta','data: {}','','event: token','data: {"text":"不可保存的服务器草稿"}',
        '','event: error','data: {"status":"FAILED","code":"E500","message":"问答服务暂不可用"}',
        '',''].join(nl);const e=new TextEncoder();return Promise.resolve(new Response(
        new ReadableStream({start(c){c.enqueue(e.encode(frame));c.close();}}),
        {headers:{'Content-Type':'text/event-stream'}}));}
      return _realFetch(url,options)}; ask('模拟服务端错误');`);
    await until(`!document.getElementById('send').disabled &&
      [...document.querySelectorAll('.err')].at(-1)?.textContent.includes('问答服务暂不可用')`,
      'server error visible');
    const serverError = await evaluate(`({status:document.getElementById('status').textContent,
      assistantCount:loadMessages(sessionId).filter(m=>m.role==='assistant').length,
      last:[...document.querySelectorAll('.err')].at(-1)?.textContent})`);
    if (!serverError.status.includes('未完成') || serverError.assistantCount !== 3 ||
        serverError.last.includes('不可保存的服务器草稿'))
      throw new Error('server error draft persisted or status reset');
    await evaluate(`window.fetch=(url,options)=>{if(String(url)==='/qa/sse'){
      sseCalls++;const e=new TextEncoder();return Promise.resolve(new Response(
        new ReadableStream({start(c){c.enqueue(e.encode([
          'event: meta','data: {}','','event: token','data: {"text":"切换前草稿"}','',''
        ].join(String.fromCharCode(10))));}}),
        {headers:{'Content-Type':'text/event-stream'}}));}
      return _realFetch(url,options)}; ask('模拟主动切换');`);
    await until(`[...document.querySelectorAll('.msg.assistant')].at(-1)?.textContent.includes('切换前草稿')`,
      'pending draft before switch');
    await evaluate(`document.getElementById('transportMode').value='ws';
      document.getElementById('transportMode').onchange();`);
    await until(`[...document.querySelectorAll('.msg.assistant')].at(-1)?.textContent.includes('已取消显示')`,
      'cancelled draft on transport switch');
    const cancelled = await evaluate(`({last:[...document.querySelectorAll('.msg.assistant')].at(-1)?.textContent,
      assistantCount:loadMessages(sessionId).filter(m=>m.role==='assistant').length})`);
    if (cancelled.last.includes('切换前草稿') || cancelled.assistantCount !== 3)
      throw new Error('cancelled draft remained visible or persisted');
    await evaluate(`document.getElementById('transportMode').value='sse';
      document.getElementById('transportMode').onchange();`);
    const shot = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(p.screenshot, Buffer.from(shot.data, 'base64'));
    console.log('browser: real SSE, final overwrite, citation, disconnect and HTTP/server failures passed');
  } catch (error) { console.error(error.message); process.exitCode = 1; }
  finally {
    if (ws?.readyState === 1) { try { await call('Browser.close'); } catch {} ws.close(); }
    browser?.kill(); for (const item of pending.values()) clearTimeout(item.timer);
  }
});
