// 聚焦探测：评测页在选择版本/报告后到底发生了什么
const fs = require('fs'), cp = require('child_process'), path = require('path');
const project = path.resolve(__dirname, '..');
const adminKey = 'ui-audit-admin-20261019';
const base = 'http://127.0.0.1:8010';
const debugPort = 19631;
const delay = ms => new Promise(r => setTimeout(r, ms));
let browser, ws, serial = 0; const pending = new Map(); const logs = [];
function call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout ' + method)), 60000); pending.set(id, { resolve, reject, timer, method }); ws.send(JSON.stringify({ id, method, params })); }); }
async function ev(expression) { const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval fail: ' + JSON.stringify(r.exceptionDetails).slice(0, 200)); return r.result.value; }
(async () => {
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
  browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir=' + path.join(project, '.runtime/test/reports/frontend-audit-20261019/probe2-profile'), '--remote-debugging-port=' + debugPort, '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let targets; for (let i = 0; i < 100; i++) { try { targets = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json(); if (targets.length) break; } catch { } await delay(100); }
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = e => { const m = JSON.parse(e.data), task = pending.get(m.id); if (task) { clearTimeout(task.timer); pending.delete(m.id); m.error ? task.reject(new Error(m.error.message)) : task.resolve(m.result); } else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') logs.push((m.params.args || []).map(a => a.value || a.description || '').join(' ').slice(0, 200)); else if (m.method === 'Runtime.exceptionThrown') logs.push('EX: ' + (m.params.exceptionDetails?.exception?.description || '').slice(0, 200)); };
  await call('Runtime.enable'); await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: base }); await delay(1500);
  await ev("localStorage.setItem('api_key','" + adminKey + "')");
  await call('Page.reload'); await delay(3000);
  await ev("window.switchTab('eval')");
  for (let i = 0; i < 8; i++) {
    await delay(2500);
    const s = await ev(`(()=>({status:document.getElementById('evalStatus').textContent,
      bodyHidden:document.getElementById('evalBody').hidden,
      version:(document.getElementById('evalVersion')||{}).value||'',
      report:(document.getElementById('evalReport')||{}).value||'',
      reportOptions:(document.getElementById('evalReport')?.options.length)||0,
      summary:(document.getElementById('evalSummary')?.innerText||'').slice(0,120),
      sla:(document.getElementById('evalSla')?.innerText||'').slice(0,180)}))()`);
    console.log('T+' + ((i + 1) * 2.5) + 's', JSON.stringify(s));
    if (s.summary) break;
  }
  console.log('LOGS', JSON.stringify(logs.slice(0, 8)));
  ws.close(); browser.kill();
})().catch(e => { console.error('FAIL', e.message); try { browser.kill(); } catch { } process.exitCode = 1; });
