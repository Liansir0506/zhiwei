const fs = require('fs'), cp = require('child_process'), path = require('path');
const project = path.resolve(__dirname, '..');
const base = 'http://127.0.0.1:8010';
const port = 19632;
const delay = ms => new Promise(r => setTimeout(r, ms));
let browser, ws, serial = 0; const pending = new Map();
function call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++serial, timer = setTimeout(() => reject(new Error('timeout ' + method)), 30000); pending.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params })); }); }
async function ev(expression) { const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval fail'); return r.result.value; }
(async () => {
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
  browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir=' + path.join(project, '.runtime/test/reports/frontend-audit-20261019/probe3-profile'), '--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let targets; for (let i = 0; i < 100; i++) { try { targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json(); if (targets.length) break; } catch { } await delay(100); }
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = e => { const m = JSON.parse(e.data), t = pending.get(m.id); if (t) { clearTimeout(t.timer); pending.delete(m.id); m.error ? t.reject(new Error(m.error.message)) : t.resolve(m.result); } };
  await call('Runtime.enable'); await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  const key = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
  await call('Page.navigate', { url: base }); await delay(1500);
  await ev("localStorage.setItem('api_key', " + JSON.stringify(key) + ")");
  await call('Page.reload'); await delay(3500);
  const info = await ev(`(()=>{
    const vals=[...document.querySelectorAll('.brow .val')];
    return {count:vals.length, rows:vals.map(v=>{const cs=getComputedStyle(v);const r=v.getBoundingClientRect();return {text:v.textContent,width:Math.round(r.width),ws:cs.whiteSpace,flex:cs.flex,parent:Math.round(v.parentElement.getBoundingClientRect().width)};})};
  })()`);
  console.log(JSON.stringify(info));
  ws.close(); browser.kill();
})().catch(e => { console.error('FAIL', e.message); try { browser.kill(); } catch { } process.exitCode = 1; });
