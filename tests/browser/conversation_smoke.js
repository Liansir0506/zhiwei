// Real multi-turn SSE/WS context; no LLM. The review-notice case is a labelled UI probe.
const fs = require('fs'), path = require('path'), cp = require('child_process');
let input = ''; process.stdin.on('data', data => input += data);
process.stdin.on('end', async () => {
  const p = JSON.parse(input), pending = new Map();
  let sequence = 0, ws, browser;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence, timer = setTimeout(() => reject(new Error('CDP timeout')), 15000);
    pending.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error('browser expression failed');
    return result.result.value;
  };
  const until = async (expression, message) => {
    for (let i = 0; i < 300; i++) { if (await evaluate(expression)) return; await pause(200); }
    throw new Error(message);
  };
  try {
    browser = cp.spawn(p.edge, ['--headless=new', '--incognito', '--disable-gpu', '--no-first-run',
      '--no-default-browser-check', `--user-data-dir=${p.profile}`, `--remote-debugging-port=${p.debugPort}`,
      '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
    let targets;
    for (let i = 0; i < 100; i++) {
      try { targets = await (await fetch(`http://127.0.0.1:${p.debugPort}/json/list`)).json(); if (targets.length) break; } catch {}
      await pause(100);
    }
    if (!targets?.length) throw new Error('Edge unavailable');
    ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    ws.onmessage = e => { const data = JSON.parse(e.data), item = pending.get(data.id); if (!item) return;
      clearTimeout(item.timer); pending.delete(data.id); data.error ? item.reject(new Error('CDP failure')) : item.resolve(data.result); };
    await call('Page.navigate', { url: p.url });
    await until('typeof ask === "function"', 'page unavailable');
    await evaluate(`localStorage.setItem('api_key', ${JSON.stringify(p.key)}); health();`);
    await until(`document.getElementById('envBadge').textContent.includes('引用模式') &&
      document.getElementById('kbVer').textContent.includes(${JSON.stringify(p.version)})`, 'mode/ACTIVE missing');
    await evaluate(`ask('FAQ-SIM-0932')`);
    await until(`document.querySelector('.msg.assistant [data-status="ANSWERED"]') !== null`, 'real SSE answer missing');
    await until('!activeSse && !document.getElementById("send").disabled', 'first SSE did not finish');
    await evaluate(`ask('这个条目的验证指标是什么')`);
    await until(`document.querySelectorAll('.msg.assistant [data-status="ANSWERED"]').length===2`, 'follow-up SSE missing');
    await evaluate(`window.conversationNode=[...document.querySelectorAll('.msg.assistant')].at(-1); conversationNode.querySelector('details.src').open=true`);
    await until(`conversationNode.querySelector('.chunkview')?.textContent.includes('来源修订：')`, 'real FAQ source missing');
    if (!await evaluate(`conversationNode.querySelector('.answer').textContent.includes('SIM-FAQ-0932')`))
      throw new Error('FAQ identity mismatch');
    const screenshots = [];
    for (const width of [1280, 390]) {
      await call('Emulation.setDeviceMetricsOverride', { width, height: 1100, deviceScaleFactor: 1, mobile: width < 500 });
      await evaluate(`conversationNode.scrollIntoView({block:'start'})`); await pause(150);
      if (await evaluate('document.documentElement.scrollWidth>window.innerWidth+2')) throw new Error('horizontal overflow');
      if (await evaluate(`document.body.innerText.includes(${JSON.stringify(p.key)}) ||
        [...document.querySelectorAll('input')].some(e=>e.value.includes(${JSON.stringify(p.key)}))`)) throw new Error('key rendered');
      const shot = await call('Page.captureScreenshot', { format: 'png' }), name = `conversation-${width}.png`;
      fs.writeFileSync(path.join(p.reportDir, name), Buffer.from(shot.data, 'base64')); screenshots.push(name);
    }
    // Browser's native WebSocket uses the same authenticated backend entry.
    const stream = await evaluate(`(async()=>{
      const socket=new WebSocket(${JSON.stringify(p.url.replace('http:', 'ws:') + '/qa/ws?key=')}+encodeURIComponent(apiKey()));
      await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=()=>reject(new Error('ws auth failed'))});
      socket.send(JSON.stringify({query:'这个条目的验证指标是什么',session_id:${JSON.stringify(p.session)}}));
      return await new Promise((resolve,reject)=>{
        const events=[],timer=setTimeout(()=>{socket.close();reject(new Error('ws timeout'))},60000);
        socket.onmessage=event=>{const frame=JSON.parse(event.data);events.push(frame);
          if(frame.event==='end'||frame.event==='error'){clearTimeout(timer);socket.close();resolve(events)}};
      });
    })()`);
    const end = stream.find(frame => frame.event === 'end')?.data;
    if (!end || end.status !== 'ANSWERED' || end.answer_mode !== 'citation' || !end.answer.includes(p.expected))
      throw new Error('real WebSocket citation answer failed');
    if (!stream.find(frame => frame.event === 'sources')?.data.sources.length) throw new Error('WS sources missing');
    fs.writeFileSync(path.join(p.reportDir, 'websocket.json'), JSON.stringify(stream, null, 2));
    const noticeProbe=await evaluate(`(()=>{const original={text:'摘要原文：待人工审核。'},before=original.text;
      return {notice:EvidenceViewer.reviewNotice(original),original_unchanged:original.text===before,
      mandatory_sop_notice:EvidenceViewer.reviewNotice({text:'必须人工审批，异常时回滚。'})}})()`);
    if(!noticeProbe.original_unchanged || !noticeProbe.notice.includes('所示来源修订') ||
       noticeProbe.mandatory_sop_notice.includes('原文审核声明')) throw new Error('review notice semantics failed');
    if(!await evaluate(`conversationNode.querySelector('.chunkview').textContent.includes('访问状态：已通过当前版本与权限校验')`))
      throw new Error('real access-validation label missing');
    fs.writeFileSync(path.join(p.reportDir,'review-notice-probe.json'),JSON.stringify({type:'controlled_ui_probe',...noticeProbe},null,2));
    fs.writeFileSync(path.join(p.reportDir, 'browser.json'), JSON.stringify({ passed: true, real_sse: true, real_followup_sse: true,
      real_websocket: true, generation_enabled: false, status: 'ACTIVE', screenshots, paid_llm_calls: 0 }, null, 2));
    console.log('Real multi-turn SSE/WS context and authenticated source panels passed; review notice UI probe passed');
  } catch (error) { console.error(error.message); process.exitCode = 1; }
  finally {
    if (ws?.readyState === 1) { try { await call('Browser.close'); } catch {} ws.close(); }
    browser?.kill(); for (const item of pending.values()) clearTimeout(item.timer);
  }
});
