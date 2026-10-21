// 业务页“服务已启用”状态探测：读取标题、状态、入口链接与截图。
const fs = require('fs'), cp = require('child_process'), path = require('path');
const project = path.resolve(__dirname, '..');
const base = 'http://127.0.0.1:8010';
const port = 19633;
const delay = ms => new Promise(r => setTimeout(r, ms));
let browser, ws, serial = 0; const pending = new Map();
function call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++serial, timer = setTimeout(() => reject(new Error('timeout ' + method)), 40000); pending.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params })); }); }
async function ev(expression) { const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval fail: ' + expression.slice(0, 100)); return r.result.value; }
(async () => {
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
  const profile = path.join(project, '.runtime/test/reports/frontend-audit-20261019/business-profile');
  fs.rmSync(profile, { recursive: true, force: true });
  browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir=' + profile, '--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let targets; for (let i = 0; i < 100; i++) { try { targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json(); if (targets.length) break; } catch { } await delay(100); }
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = e => { const m = JSON.parse(e.data), t = pending.get(m.id); if (t) { clearTimeout(t.timer); pending.delete(m.id); m.error ? t.reject(new Error(m.error.message)) : t.resolve(m.result); } };
  await call('Runtime.enable'); await call('Page.enable'); await call('Network.enable'); await call('Network.setCacheDisabled', { cacheDisabled: true });
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  const key = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
  await call('Page.navigate', { url: base }); await delay(1500);
  await ev("localStorage.setItem('api_key', " + JSON.stringify(key) + ")");
  await call('Page.reload'); await delay(3000);
  await ev("window.switchTab('business')");
  await delay(2500);
  const info = await ev(`(()=>({
    h2: document.querySelector('#businessAccess h2')?.textContent||'',
    pill: document.querySelector('#businessAccess .pill')?.textContent||'',
    status: document.getElementById('businessStatus')?.textContent||'',
    link: document.querySelector('#businessAccess a')?.href||'',
    notice: document.querySelector('#businessAccess .notice')?.textContent?.slice(0,100)||''
  }))()`);
  console.log(JSON.stringify(info));
  fs.writeFileSync(path.join(project, '.runtime/test/reports/frontend-audit-20261019/browser/business-running.png'),
    Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
  ws.close(); browser.kill();
})().catch(e => { console.error('FAIL', e.message); try { browser.kill(); } catch { } process.exitCode = 1; });
