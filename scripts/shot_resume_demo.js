// 参考 demo 多页截图（file://，本地单文件）
const fs = require('fs'), cp = require('child_process'), path = require('path');
const project = path.resolve(__dirname, '..');
const file = 'file:///C:/Users/16659/Desktop/项目/简历项目一年经验版/通信智能运维平台小demo.html';
const out = path.join(project, '.runtime/test/reports/resume-demo-20261019');
const port = 19640;
const delay = ms => new Promise(r => setTimeout(r, ms));
let browser, ws, serial = 0; const pending = new Map();
function call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++serial, timer = setTimeout(() => reject(new Error('timeout ' + method)), 30000); pending.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params })); }); }
async function ev(expression) { const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval fail'); return r.result.value; }
(async () => {
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
  const profile = path.join(out, 'profile');
  fs.rmSync(profile, { recursive: true, force: true });
  browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--allow-file-access-from-files', '--user-data-dir=' + profile, '--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let targets; for (let i = 0; i < 100; i++) { try { targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json(); if (targets.length) break; } catch { } await delay(100); }
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = e => { const m = JSON.parse(e.data), t = pending.get(m.id); if (t) { clearTimeout(t.timer); pending.delete(m.id); m.error ? t.reject(new Error(m.error.message)) : t.resolve(m.result); } };
  await call('Runtime.enable'); await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: file }); await delay(2500);
  const pages = ['alerts', 'diagnosis', 'knowledge', 'topology', 'query', 'tickets'];
  for (const page of pages) {
    await ev(`App.go(${JSON.stringify(page)})`);
    await delay(900);
    fs.writeFileSync(path.join(out, 'demo-' + page + '.png'), Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
    console.log('shot', page);
  }
  ws.close(); browser.kill();
})().catch(e => { console.error('FAIL', e.message); try { browser.kill(); } catch { } process.exitCode = 1; });
