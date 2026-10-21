// 调试：确认 admin 凭据注入与版本/评测/权限页实际渲染
const fs = require('fs'), cp = require('child_process'), path = require('path');
const project = path.resolve(__dirname, '..');
const opsKey = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
const adminKey = 'ui-audit-admin-20261019';
const base = 'http://127.0.0.1:8010';
const debugPort = 19630;
const delay = ms => new Promise(r => setTimeout(r, ms));
let browser, ws, serial = 0; const pending = new Map(); const errors = [];
function call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout ' + method)), 30000); pending.set(id, { resolve, reject, timer, method }); ws.send(JSON.stringify({ id, method, params })); }); }
async function ev(expression) { const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval fail'); return r.result.value; }
(async () => {
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
  browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir=' + path.join(project, '.runtime/test/reports/frontend-audit-20261019/probe-profile'), '--remote-debugging-port=' + debugPort, '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let targets; for (let i = 0; i < 100; i++) { try { targets = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json(); if (targets.length) break; } catch {} await delay(100); }
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = e => { const m = JSON.parse(e.data), task = pending.get(m.id); if (task) { clearTimeout(task.timer); pending.delete(m.id); m.error ? task.reject(new Error(m.error.message)) : task.resolve(m.result); } else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push((m.params.args || []).map(a => a.value || a.description || '').join(' ')); };
  await call('Runtime.enable'); await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: base }); await delay(2000);
  await ev("localStorage.setItem('api_key', " + JSON.stringify(adminKey) + ")");
  await call('Page.reload'); await delay(4000);
  const info = await ev(`(async()=>{
    const key=localStorage.getItem('api_key')||'';
    const caps=await (await fetch('/admin/capabilities',{headers:{'X-API-Key':key}})).json();
    const vcaps=await (await fetch('/admin/version-workspace/capabilities',{headers:{'X-API-Key':key}})).json();
    return {keyLen:key.length,keyHead:key.slice(0,12),caps,vcaps,
      navVersionsHidden:document.querySelector('[data-tab="versions"]').hidden,
      dashboardErr:document.getElementById('dashboardBody').innerText.slice(0,80)};})()`);
  console.log('INFO', JSON.stringify(info));
  await ev("window.switchTab('versions')"); await delay(6000);
  const v = await ev(`(()=>({status:document.getElementById('versionStatus').textContent,
    listText:(document.getElementById('versionList').innerText||'').slice(0,300),
    items:document.querySelectorAll('#versionList .version-item').length,
    summary:!!document.querySelector('#versionSummary .surface'),
    detail:(document.getElementById('versionDetail').innerText||'').slice(0,200)}))()`);
  console.log('VERSIONS', JSON.stringify(v));
  await ev("window.switchTab('eval')"); await delay(4000);
  console.log('EVAL', JSON.stringify(await ev(`(()=>({status:document.getElementById('evalStatus').textContent,bodyHidden:document.getElementById('evalBody').hidden,access:document.getElementById('evalAccess').innerText.slice(0,160)}))()`)));
  await ev("window.switchTab('perm')"); await delay(4000);
  console.log('PERM', JSON.stringify(await ev(`(()=>({status:document.getElementById('permStatus').textContent,rows:document.querySelectorAll('#permKeys table tbody tr').length,bodyHidden:document.getElementById('permBody').hidden,access:document.getElementById('permAccess').innerText.slice(0,160)}))()`)));
  console.log('ERRORS', JSON.stringify(errors.slice(0, 6)));
  ws.close(); browser.kill();
})().catch(e => { console.error('FAIL', e.message); try { browser.kill(); } catch {} process.exitCode = 1; });
