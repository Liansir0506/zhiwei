// Real authenticated source-panel preview; scripted answer is explicitly a UI probe.
const fs = require('fs'), path = require('path'), cp = require('child_process');
let input = ''; process.stdin.on('data', d => input += d);
process.stdin.on('end', async () => {
  const p = JSON.parse(input), pending = new Map();
  let sequence = 0, ws, browser;
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence, timer = setTimeout(() => reject(new Error('CDP timeout')), 15000);
    pending.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error('preview browser expression failed');
    return result.result.value;
  };
  const until = async (expression, message) => {
    for (let i = 0; i < 100; i++) { if (await evaluate(expression)) return; await pause(100); }
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
    if (!targets?.length) throw new Error('isolated Edge unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    ws.onmessage = e => { const data = JSON.parse(e.data), item = pending.get(data.id); if (!item) return;
      clearTimeout(item.timer); pending.delete(data.id); data.error ? item.reject(new Error('CDP failure')) : item.resolve(data.result); };
    await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false });
    await call('Page.navigate', { url: p.url });
    await until('typeof newAssistant === "function"', 'preview page did not load');
    await evaluate(`localStorage.setItem('api_key', ${JSON.stringify(p.key)});
      window.previewNode=newAssistant(); renderAnswer(previewNode.body,'项目自编/模拟资料原文预览（浏览器验证，无模型生成） [1] [2] [3] [4] [5]');
      addMeta(previewNode.root,{route:'retrieval',intent:'knowledge',source:'ui_probe',score:1},${JSON.stringify(p.sources)},null,false,[],${JSON.stringify(p.version)});
      previewNode.root.querySelector('details.src').open=true;`);
    await until(`previewNode.root.querySelectorAll('.chunkview').length===5 &&
      [...previewNode.root.querySelectorAll('.chunkview')].every(e=>e.textContent.includes('来源修订：'))`, 'real source panels did not finish loading');
    const bodies = await evaluate(`[...previewNode.root.querySelectorAll('.chunkview')].map(e=>e.textContent)`);
    if (!bodies.every((body,i)=>body.includes(p.sources[i].text)) || !bodies[0].includes('条记录') || !bodies[1].includes('条记录') ||
        !bodies[2].includes('行') || !bodies[3].includes('行') || !bodies[4].includes('行')) throw new Error('CSV/Markdown source text or locations missing');
    if (await evaluate(`document.body.innerText.includes(${JSON.stringify(p.key)}) ||
      [...document.querySelectorAll('input')].some(i=>i.value.includes(${JSON.stringify(p.key)}))`)) throw new Error('key must not appear in screenshots');
    const screenshots = [];
    for (const width of [1280, 390]) {
      await call('Emulation.setDeviceMetricsOverride', { width, height: 1100, deviceScaleFactor: 1, mobile: width < 500 });
      await evaluate('previewNode.root.scrollIntoView({block:"start"})'); await pause(100);
      if (await evaluate('document.documentElement.scrollWidth>window.innerWidth+2')) throw new Error('source preview horizontal overflow');
      const name = `preview-${width}.png`, shot = await call('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(p.reportDir, name), Buffer.from(shot.data, 'base64')); screenshots.push(name);
    }
    await evaluate(`previewNode.root.querySelector('details.src').open=false;localStorage.setItem('api_key','invalid-preview-key');`);
    await pause(100); await evaluate(`previewNode.root.querySelector('details.src').open=true;`);
    await until(`[...previewNode.root.querySelectorAll('.chunkview')].every(e=>e.textContent.includes('API Key'))`, 'invalid key did not clear source text');
    const cleared = await evaluate(`[...previewNode.root.querySelectorAll('.chunkview')].every(e=>!e.textContent.includes('来源修订：'))`);
    if (!cleared) throw new Error('stale approved source remained after invalid authentication');
    fs.writeFileSync(path.join(p.reportDir, 'browser.json'), JSON.stringify({ passed: true, status: p.status || 'STAGED', real_sources: 5,
      csv_panels: 2, markdown_panels: 3, screenshots, invalid_key_clears_original: true, paid_llm_calls: 0 }, null, 2));
    console.log(`${p.status || 'STAGED'} browser: five real sources, CSV/Markdown locations, two widths and invalid-key clearing passed`);
  } catch (error) { console.error(error.message); process.exitCode = 1; }
  finally {
    if (ws?.readyState === 1) { try { await call('Browser.close'); } catch {} ws.close(); }
    browser?.kill(); for (const item of pending.values()) clearTimeout(item.timer);
  }
});
