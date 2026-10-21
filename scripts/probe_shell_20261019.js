// 产品壳交互验收：搜索、通知、导览、账号
const fs = require('fs'), cp = require('child_process'), path = require('path');
const project = path.resolve(__dirname, '..');
const out = path.join(project, '.runtime/test/reports/resume-demo-20261019/p0');
const base = 'http://127.0.0.1:8011';
const port = 19642;
const delay = ms => new Promise(r => setTimeout(r, ms));
let browser, ws, serial = 0; const pending = new Map(); const errors = [];
function call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++serial, timer = setTimeout(() => reject(new Error('timeout ' + method)), 30000); pending.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params })); }); }
async function ev(expression) { const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval fail: ' + expression.slice(0, 100)); return r.result.value; }
async function shot(name) { fs.writeFileSync(path.join(out, name), Buffer.from((await call('Page.captureScreenshot')).data, 'base64')); }
(async () => {
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
  const profile = path.join(out, 'shell-profile'); fs.rmSync(profile, { recursive: true, force: true });
  browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir=' + profile, '--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let targets; for (let i = 0; i < 100; i++) { try { targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json(); if (targets.length) break; } catch { } await delay(100); }
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = e => { const m = JSON.parse(e.data), t = pending.get(m.id); if (t) { clearTimeout(t.timer); pending.delete(m.id); m.error ? t.reject(new Error(m.error.message)) : t.resolve(m.result); } else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push((m.params.args || []).map(a => a.value || a.description || '').join(' ').slice(0, 200)); else if (m.method === 'Runtime.exceptionThrown') errors.push('EX: ' + (m.params.exceptionDetails?.exception?.description || '').slice(0, 200)); };
  await call('Runtime.enable'); await call('Page.enable'); await call('Network.enable'); await call('Network.setCacheDisabled', { cacheDisabled: true });
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  const key = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
  await call('Page.navigate', { url: base }); await delay(1200);
  await ev("localStorage.setItem('api_key', " + JSON.stringify(key) + ")");
  await call('Page.reload'); await delay(3000);
  // Ctrl+K
  await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}))");
  console.log('shortcut', await ev("document.activeElement?.id"));
  // 搜索 ALM-AMF-REG
  await ev("document.getElementById('globalSearch').value='ALM-AMF-REG';document.getElementById('globalSearchForm').requestSubmit()");
  await delay(1800);
  console.log('search', await ev("(()=>({view:document.querySelector('.tabview.active')?.id,query:document.getElementById('alertsQuery')?.value,rows:document.querySelectorAll('#alertsTable .alert-table tbody tr').length,status:document.getElementById('alertsStatus')?.textContent}))()"));
  await shot('search-alerts-1440.png');
  // 通知
  await ev("document.getElementById('notifyBtn').click()"); await delay(1200);
  console.log('notify', await ev("(()=>({open:document.getElementById('notifyDlg')?.open,rows:document.querySelectorAll('#notifyDlg .notify-row').length}))()"));
  await shot('notify-1440.png');
  await ev("document.getElementById('notifyDlg')?.close()");
  // 导览
  await ev("document.getElementById('guideBtn').click()"); await delay(400);
  console.log('guide', await ev("(()=>({open:document.getElementById('guideDlg')?.open,steps:document.querySelectorAll('#guideDlg .guide-step').length}))()"));
  await ev("document.getElementById('guideDlg')?.close()");
  // 账号
  await ev("document.getElementById('avatarBtn').click()"); await delay(700);
  console.log('account', await ev("(()=>({open:document.getElementById('accountDlg')?.open,roles:[...document.querySelectorAll('#accountDlg .pill')].map(x=>x.textContent)}))()"));
  await shot('account-1440.png');
  console.log('errors', JSON.stringify(errors.slice(0, 6)));
  ws.close(); browser.kill();
})().catch(e => { console.error('FAIL', e.message); try { browser.kill(); } catch { } process.exitCode = 1; });
