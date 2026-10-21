// P0 视觉验收：总览 / 告警中心 / 详情 / 导览 / 移动端
const fs = require('fs'), cp = require('child_process'), path = require('path');
const project = path.resolve(__dirname, '..');
const out = path.join(project, '.runtime/test/reports/resume-demo-20261019/p0');
const base = 'http://127.0.0.1:8011';
const port = 19641;
const delay = ms => new Promise(r => setTimeout(r, ms));
let browser, ws, serial = 0; const pending = new Map(); const errors = [];
function call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++serial, timer = setTimeout(() => reject(new Error('timeout ' + method)), 40000); pending.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params })); }); }
async function ev(expression) { const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval fail: ' + expression.slice(0, 100)); return r.result.value; }
async function shot(name) { fs.writeFileSync(path.join(out, name), Buffer.from((await call('Page.captureScreenshot')).data, 'base64')); }
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
  const profile = path.join(out, 'profile'); fs.rmSync(profile, { recursive: true, force: true });
  browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir=' + profile, '--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let targets; for (let i = 0; i < 100; i++) { try { targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json(); if (targets.length) break; } catch { } await delay(100); }
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = e => { const m = JSON.parse(e.data), t = pending.get(m.id); if (t) { clearTimeout(t.timer); pending.delete(m.id); m.error ? t.reject(new Error(m.error.message)) : t.resolve(m.result); } else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push((m.params.args || []).map(a => a.value || a.description || '').join(' ').slice(0, 200)); else if (m.method === 'Runtime.exceptionThrown') errors.push('EX: ' + (m.params.exceptionDetails?.exception?.description || '').slice(0, 200)); };
  await call('Runtime.enable'); await call('Page.enable'); await call('Network.enable'); await call('Network.setCacheDisabled', { cacheDisabled: true });
  const key = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: base }); await delay(1500);
  await ev("localStorage.setItem('api_key', " + JSON.stringify(key) + ")");
  await call('Page.reload'); await delay(3500);
  await ev("window.switchTab('dashboard')"); await delay(1500);
  await shot('dashboard-1440.png');
  const dash = await ev(`(()=>({metrics:document.querySelectorAll('#dashboardBody .dash-metric').length,
    hero:!!document.querySelector('.dash-hero'), topo:!!document.querySelector('.topo-map'),
    alerts:document.querySelectorAll('.dash-table tbody tr').length, overflow:document.documentElement.scrollWidth-window.innerWidth}))()`);
  console.log('DASH', JSON.stringify(dash));
  await ev("window.switchTab('alerts')"); await delay(2000);
  await shot('alerts-1440.png');
  const alerts = await ev(`(()=>({stats:document.querySelectorAll('.alert-stat').length, rows:document.querySelectorAll('.alert-table tbody tr').length,
    status:document.getElementById('alertsStatus').textContent, overflow:document.documentElement.scrollWidth-window.innerWidth}))()`);
  console.log('ALERTS', JSON.stringify(alerts));
  const opened = await ev("(()=>{const b=document.querySelector('.alert-table tbody .link-button');if(!b)return false;b.click();return true;})()");
  await delay(1200);
  await shot('alert-detail-1440.png');
  console.log('DETAIL', opened, await ev("(()=>({open:document.getElementById('alertDlg')?.open, title:document.querySelector('.alert-dialog-head h2')?.textContent||''}))()"));
  await ev("document.getElementById('alertDlg')?.close(); window.openGuide?.();"); await delay(600);
  await shot('guide-1440.png');
  await ev("document.getElementById('guideDlg')?.close();");
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await call('Page.reload'); await delay(3000);
  await ev("window.switchTab('dashboard')"); await delay(1500);
  await shot('dashboard-390.png');
  const mobile = await ev(`(()=>({overflow:document.documentElement.scrollWidth-window.innerWidth,
    metrics:document.querySelectorAll('#dashboardBody .dash-metric').length, search:getComputedStyle(document.getElementById('globalSearchForm')).display}))()`);
  console.log('MOBILE', JSON.stringify(mobile));
  console.log('ERRORS', JSON.stringify(errors.slice(0, 8)));
  ws.close(); browser.kill();
})().catch(e => { console.error('FAIL', e.message); try { browser.kill(); } catch { } process.exitCode = 1; });
