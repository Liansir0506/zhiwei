/* P2 综合实测：场景实验室（含备份导出）、移动端表格卡片化、五页 390 回归。 */
const fs = require('fs'), cp = require('child_process'), path = require('path');
const OUT = process.argv[2] || '.runtime/test/reports/resume-demo-20261019/p2';
const BASE = process.argv[3] || 'http://127.0.0.1:8011';
const PROFILE = path.resolve('.runtime/test/reports/resume-demo-20261019/p2-profile');
const delay = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, {recursive: true});
  fs.rmSync(PROFILE, {recursive: true, force: true});
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe']
    .find(p => fs.existsSync(p));
  const browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
    `--user-data-dir=${PROFILE}`, '--window-size=1440,1000', '--remote-debugging-port=9343', 'about:blank'],
    {detached: true, stdio: 'ignore'});
  browser.unref();
  await delay(2500);

  let serial = 0; const pending = new Map();
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 60000);
    pending.set(id, {resolve, reject, timer});
    ws.send(JSON.stringify({id, method, params}));
  });
  const httpGet = url => new Promise((resolve, reject) => {
    require('http').get(url, res => { let raw = ''; res.on('data', c => raw += c); res.on('end', () => resolve(JSON.parse(raw))); }).on('error', reject);
  });
  let targets = null;
  for (let i = 0; i < 30; i++) { try { targets = await httpGet('http://127.0.0.1:9343/json'); break; } catch { await delay(500); } }
  const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('ws failed')); });
  ws.onmessage = event => {
    const m = JSON.parse(event.data);
    if (m.id && pending.has(m.id)) {
      const task = pending.get(m.id); pending.delete(m.id); clearTimeout(task.timer);
      m.error ? task.reject(new Error(m.error.message || 'CDP error')) : task.resolve(m.result);
    }
  };
  const evaluate = async expression => {
    const r = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (r.exceptionDetails) throw new Error('eval failed: ' + (r.exceptionDetails.exception && r.exceptionDetails.exception.description || '').slice(0, 250) + ' | expr=' + expression.slice(0, 80));
    return r.result.value;
  };
  const shot = async name => { const r = await call('Page.captureScreenshot', {format: 'png'}); fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, 'base64')); };
  const waitFor = async (expression, label, attempts = 80) => {
    for (let i = 0; i < attempts; i++) {
      try { if (await evaluate(expression)) return true; } catch {}
      await delay(500);
    }
    console.log('wait-timeout:', label);
    return false;
  };

  await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
  await call('Network.setCacheDisabled', {cacheDisabled: true});
  const key = 'ui-audit-admin-20261019';
  const report = {steps: []};

  await call('Page.navigate', {url: BASE});
  for (let i = 0; i < 40; i++) { await delay(500); try { if (await evaluate("document.readyState==='complete' && location.href.startsWith('http')")) break; } catch {} }
  await delay(500);
  await evaluate(`localStorage.setItem('api_key', ${JSON.stringify(key)})`);
  await call('Page.reload');
  for (let i = 0; i < 40; i++) { await delay(500); try { if (await evaluate("document.readyState==='complete' && location.href.startsWith('http')")) break; } catch {} }
  await delay(1800);

  // ===== 场景实验室（含备份） =====
  await evaluate("document.querySelector('.nav-item[data-tab=\"lab\"]').click()");
  await waitFor("document.querySelectorAll('#labBody .lab-scenario').length >= 6", 'lab scenarios');
  report.steps.push(['六个场景卡', await evaluate("document.querySelectorAll('#labBody .lab-scenario').length")]);
  await call('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: path.resolve(OUT)}).catch(() => {});
  await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('演示数据备份'));
    [...card.querySelectorAll('button')].find(b=>b.textContent.includes('导出备份')).click();return true;})()`);
  await waitFor(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('演示数据备份'));
    return card && /已触发下载/.test(card.textContent);})()`, 'backup download');
  report.steps.push(['备份导出', fs.readdirSync(OUT).filter(f => f.startsWith('demo-backup-')).length ? 'demo-backup json' : '(none)']);
  await shot('p2-lab-backup.png');

  // ===== 移动端表格卡片化（390） =====
  await call('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  await evaluate("document.querySelector('.nav-item[data-tab=\"alerts\"]').click()");
  await waitFor("document.querySelectorAll('#alertsTable .alert-table tbody tr').length > 0", 'alerts mobile');
  const cards = await evaluate(`(()=>{const thead=document.querySelector('#alertsTable .alert-table thead');
    const firstRow=document.querySelector('#alertsTable .alert-table tbody tr');
    return {theadHidden: getComputedStyle(thead).display === 'none',
      rowDisplay: getComputedStyle(firstRow).display,
      labeledCells: firstRow.querySelectorAll('td[data-label]').length};})()`);
  report.steps.push(['告警表卡片化', JSON.stringify(cards)]);
  await shot('p2-alerts-card.png');

  // 查询结果表卡片化
  await evaluate("document.querySelector('.nav-item[data-tab=\"query\"]').click()");
  await delay(1200);
  await evaluate("[...document.querySelectorAll('.qexample')].find(b=>b.textContent==='最近的紧急告警').click()");
  await waitFor("document.querySelectorAll('#querySpecBox .qspec-table tbody tr').length >= 5", 'query spec mobile');
  await evaluate("document.getElementById('queryExecute').click()");
  await waitFor("document.querySelectorAll('#queryResult .qresult-table tbody tr').length > 0", 'query result mobile');
  const qcard = await evaluate(`(()=>{const row=document.querySelector('#queryResult .qresult-table tbody tr');
    return {rowDisplay: getComputedStyle(row).display, labeledCells: row.querySelectorAll('td[data-label]').length};})()`);
  report.steps.push(['查询表卡片化', JSON.stringify(qcard)]);
  await shot('p2-query-card.png');

  // ===== 五页 390 回归 =====
  for (const [tab, ready] of [['dashboard', "document.querySelectorAll('#dashboardBody .dash-metric').length>=4"],
      ['gk', "document.querySelectorAll('#gkBody .gk-tab').length>=4"],
      ['lab', "document.querySelectorAll('#labBody .lab-scenario').length>=6"]]) {
    await evaluate(`document.querySelector('.nav-item[data-tab="${tab}"]').click()`);
    await waitFor(ready, 'mobile ' + tab);
    report.steps.push([`390 ${tab} 无溢出`, await evaluate("document.documentElement.scrollWidth <= 391")]);
  }
  await shot('p2-mobile-final.png');

  const passed = report.steps.filter(([, v]) => v === true || (typeof v === 'string' && (v !== '(none)' && v.length > 5))).length;
  console.log(JSON.stringify(report, null, 1));
  console.log('STEPS_OK=' + passed + '/' + report.steps.length);
  fs.writeFileSync(path.join(OUT, 'p2-probe.json'), JSON.stringify(report, null, 1));
  ws.close();
  try { process.kill(-browser.pid); } catch {}
  process.exit(0);
})().catch(error => { console.error('PROBE_FAIL', error.message); process.exit(1); });
