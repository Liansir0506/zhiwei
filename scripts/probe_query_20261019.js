/* 查询工作台实测：示例解析 → 计划预览 → 执行 → 导出 → 390 无溢出。 */
const fs = require('fs'), cp = require('child_process'), path = require('path');
const OUT = process.argv[2] || '.runtime/test/reports/resume-demo-20261019/p1';
const BASE = process.argv[3] || 'http://127.0.0.1:8011';
const PROFILE = path.resolve('.runtime/test/reports/resume-demo-20261019/p1-profile');
const delay = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, {recursive: true});
  fs.rmSync(PROFILE, {recursive: true, force: true});
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe']
    .find(p => fs.existsSync(p));
  const browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
    `--user-data-dir=${PROFILE}`, '--window-size=1440,1000', '--remote-debugging-port=9339', 'about:blank'],
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
  for (let i = 0; i < 30; i++) { try { targets = await httpGet('http://127.0.0.1:9339/json'); break; } catch { await delay(500); } }
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
  const waitFor = async (expression, label, attempts = 60) => {
    for (let i = 0; i < attempts; i++) {
      try { if (await evaluate(expression)) return true; } catch {}
      await delay(500);
    }
    console.log('wait-timeout:', label);
    return false;
  };

  await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
  await call('Network.setCacheDisabled', {cacheDisabled: true});
  const key = fs.readFileSync('.runtime/original-local/api-key.txt', 'utf-8').trim();
  const report = {steps: []};

  await call('Page.navigate', {url: BASE});
  for (let i = 0; i < 40; i++) { await delay(500); try { if (await evaluate("document.readyState==='complete' && location.href.startsWith('http')")) break; } catch {} }
  await delay(500);
  await evaluate(`localStorage.setItem('api_key', ${JSON.stringify(key)})`);
  await call('Page.reload');
  for (let i = 0; i < 40; i++) { await delay(500); try { if (await evaluate("document.readyState==='complete' && location.href.startsWith('http')")) break; } catch {} }
  await delay(1500);

  await evaluate("document.querySelector('.nav-item[data-tab=\"query\"]').click()");
  await waitFor("!!document.getElementById('queryInput')", 'query view');
  report.steps.push(['页面就绪', await evaluate("!!document.getElementById('queryInput')")]);
  report.steps.push(['示例 chips', await evaluate("document.querySelectorAll('.qexample').length")]);

  // 点击示例 → 自动解析
  await evaluate("[...document.querySelectorAll('.qexample')].find(b=>b.textContent==='最近的紧急告警').click()");
  await waitFor("document.querySelectorAll('#querySpecBox .qspec-table tbody tr').length >= 5", 'spec rendered');
  report.steps.push(['计划预览', await evaluate("document.querySelectorAll('#querySpecBox .qspec-table tbody tr').length")]);
  report.steps.push(['计划含级别过滤', await evaluate("/severity|级别/.test(document.getElementById('querySpecBox').innerText)")]);
  report.steps.push(['来源标记', await evaluate("document.getElementById('queryStatus').textContent.includes('规则解析') || document.getElementById('queryStatus').textContent.includes('模型解析')")]);
  await shot('p1-query-spec.png');

  // 执行
  await evaluate("document.getElementById('queryExecute').click()");
  await waitFor("document.querySelectorAll('#queryResult .qresult-table tbody tr').length > 0", 'result rows');
  const result = await evaluate("(()=>({rows:document.querySelectorAll('#queryResult .qresult-table tbody tr').length, status:document.getElementById('queryStatus').textContent}))()");
  report.steps.push(['执行结果', JSON.stringify(result)]);
  report.steps.push(['导出按钮', await evaluate("document.querySelectorAll('#queryResult .action-row button').length")]);
  await shot('p1-query-result.png');

  // 导出 CSV
  await call('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: path.resolve(OUT)}).catch(() => {});
  await evaluate("[...document.querySelectorAll('#queryResult .action-row button')].find(b=>b.textContent.includes('CSV')).click()");
  await delay(1500);
  report.steps.push(['导出CSV', fs.readdirSync(OUT).filter(f => f.endsWith('.csv')).length ? 'csv' : '(none)']);

  // 390 移动端
  await call('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  await delay(1000);
  report.steps.push(['390无横向溢出', await evaluate("document.documentElement.scrollWidth <= 391")]);
  await shot('p1-query-390.png');

  const passed = report.steps.filter(([, v]) => v === true || (typeof v === 'number' && v > 0) || (typeof v === 'string' && v !== '(none)')).length;
  console.log(JSON.stringify(report, null, 1));
  console.log('STEPS_OK=' + passed + '/' + report.steps.length);
  fs.writeFileSync(path.join(OUT, 'query-probe.json'), JSON.stringify(report, null, 1));
  ws.close();
  try { process.kill(-browser.pid); } catch {}
  process.exit(0);
})().catch(error => { console.error('PROBE_FAIL', error.message); process.exit(1); });
