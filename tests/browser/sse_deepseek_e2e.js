// One opt-in, real browser -> HTTP SSE -> DeepSeek -> authorized citation check.
const fs = require('fs'), cp = require('child_process');
let input = '';
process.stdin.on('data', part => input += part);
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
  const until = async (expression, label, count = 600) => {
    for (let i = 0; i < count; i++) {
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
    await until('typeof ask === "function" && transportMode === "sse"', 'SSE reload');
    await until(`[...document.getElementById('qaVersion').options].some(o=>o.value===${JSON.stringify(p.version)})`,
      'STAGED version selector');
    await evaluate(`document.getElementById('qaVersion').value=${JSON.stringify(p.version)};
      window.stage5={calls:0, firstTokenMs:null, started:performance.now()};
      const originalFetch=window.fetch.bind(window); window.fetch=(url,options)=>{
        if(String(url)==='/qa/sse') stage5.calls++;
        return originalFetch(url,options);};
      const originalEvent=window.handleChatEvent; window.handleChatEvent=function(name,data){
        if(name==='delta' && stage5.firstTokenMs===null)
          stage5.firstTokenMs=Math.round(performance.now()-stage5.started);
        return originalEvent(name,data);};
      ask(${JSON.stringify(p.question)});`);
    await until(`!document.getElementById('send').disabled &&
      loadMessages(sessionId).some(m=>m.role==='assistant')`, 'real DeepSeek answer');
    const outcome = await evaluate(`({calls:stage5.calls, firstTokenMs:stage5.firstTokenMs,
      elapsedMs:Math.round(performance.now()-stage5.started),
      last:loadMessages(sessionId).filter(m=>m.role==='assistant').at(-1),
      refs:[...document.querySelectorAll('.msg.assistant')].at(-1)?.querySelectorAll('.answer .ref').length})`);
    if (outcome.calls !== 1 || outcome.last?.status !== 'ANSWERED' ||
        outcome.last?.kb !== p.version || !outcome.last?.sources?.length || !outcome.refs)
      throw new Error('real model answer, version, or citation absent: ' + JSON.stringify({
        calls:outcome.calls,status:outcome.last?.status,kb:outcome.last?.kb,
        sources:outcome.last?.sources?.length,refs:outcome.refs}));
    await evaluate(`[...document.querySelectorAll('.msg.assistant')].at(-1)
      .querySelector('.answer .ref').click()`);
    await until(`[...document.querySelectorAll('.msg.assistant')].at(-1)
      .querySelector('.chunkview')?.textContent.includes('503 表示')`, 'real citation reread');
    const citation = await evaluate(`({text:[...document.querySelectorAll('.msg.assistant')].at(-1)
      .querySelector('.chunkview')?.textContent, version:document.getElementById('kbVer').textContent})`);
    if (!citation.text.includes(p.version) || !citation.version.includes('v1'))
      throw new Error('citation version or unchanged active version not confirmed');
    const shot = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(p.screenshot, Buffer.from(shot.data, 'base64'));
    console.log(JSON.stringify({passed:true, calls:outcome.calls, status:outcome.last.status,
      kbVersion:outcome.last.kb, firstTokenMs:outcome.firstTokenMs, elapsedMs:outcome.elapsedMs,
      sourceCount:outcome.last.sources.length, citationReread:true, activeVersion:'v1'}));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
  finally {
    if (ws?.readyState === 1) { try { await call('Browser.close'); } catch {} ws.close(); }
    browser?.kill(); for (const item of pending.values()) clearTimeout(item.timer);
  }
});
