/* B1 实测：告警详情 → 处理时间线与三个动作 → 时间线刷新；实验室授权撤回硬化版。 */
const fs = require('fs'), cp = require('child_process'), path = require('path');
const OUT = process.argv[2] || '.runtime/test/reports/ops-recheck-20261019/b1';
const BASE = process.argv[3] || 'http://127.0.0.1:8011';
const PROFILE = path.resolve('.runtime/test/reports/ops-recheck-20261019/b1-profile');
const delay = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, {recursive: true});
  fs.rmSync(PROFILE, {recursive: true, force: true});
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe']
    .find(p => fs.existsSync(p));
  const browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
    `--user-data-dir=${PROFILE}`, '--window-size=1440,1000', '--remote-debugging-port=9344', 'about:blank'],
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
  for (let i = 0; i < 30; i++) { try { targets = await httpGet('http://127.0.0.1:9344/json'); break; } catch { await delay(500); } }
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
    if (r.exceptionDetails) throw new Error('eval failed: ' + (r.exceptionDetails.exception && r.exceptionDetails.exception.description || '').slice(0, 250) + ' | expr=' + expression.slice(0, 90));
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

  // ===== 告警详情 → 处理时间线 =====
  await evaluate("document.querySelector('.nav-item[data-tab=\"alerts\"]').click()");
  await waitFor("document.querySelectorAll('#alertsTable tbody tr').length > 0", 'alerts list');
  await evaluate("document.querySelector('#alertsTable tbody tr .link-button, #alertsTable tbody tr .btn, #alertsTable tbody tr button').click()");
  await waitFor("!!document.querySelector('.alert-handling')", 'handling section');
  report.steps.push(['处理记录区渲染', await evaluate("!!document.querySelector('.alert-handling')")]);
  report.steps.push(['已有记录条数', await evaluate("document.querySelectorAll('.alert-handling-row').length")]);
  const actions = await evaluate("[...document.querySelectorAll('.alert-handling .action-row button')].map(b=>b.textContent)");
  report.steps.push(['三个动作按钮', JSON.stringify(actions)]);

  // 通过 UI 写三条记录
  for (const [label, note] of [['人工确认', 'B1 UI 验收：证据核对后确认'], ['记录处理', 'B1 UI 验收：已按处置建议执行'], ['恢复观察', 'B1 UI 验收：观察 30 分钟未复现']]) {
    await evaluate(`(()=>{const card=document.querySelector('.alert-handling');
      const input=card.querySelector('.ui-input'); input.value=${JSON.stringify(note)};
      [...card.querySelectorAll('.action-row button')].find(b=>b.textContent===${JSON.stringify(label)}).click(); return true;})()`);
    await waitFor(`(()=>{const card=document.querySelector('.alert-handling');
      return card && /已记录/.test(card.innerText);})()`, 'receipt ' + label, 20);
    await delay(800);
  }
  report.steps.push(['写入后时间线条数', await evaluate("document.querySelectorAll('.alert-handling-row').length")]);
  report.steps.push(['回执文本', await evaluate("(document.querySelector('.alert-handling .meta-line:last-of-type')||{}).textContent||''")]);
  await shot('b1-alert-handling.png');

  // ===== 实验室授权撤回（硬化版） =====
  await evaluate("document.querySelector('.nav-item[data-tab=\"lab\"]').click()");
  await waitFor("document.querySelectorAll('#labBody .lab-scenario').length >= 6", 'lab ready');
  await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('授权撤回'));
    [...card.querySelectorAll('button')].find(b=>b.textContent.includes('开始演练')).click();return true;})()`);
  await waitFor(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('授权撤回'));
    return card && card.querySelectorAll('.lab-step').length>=6;})()`, 'revoke hardened');
  const revokeSteps = await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('授权撤回'));
    return [...card.querySelectorAll('.lab-step')].map(s=>s.textContent.replace(/\\s+/g,' ').trim()).join(' | ');})()`);
  report.steps.push(['授权撤回轨迹', revokeSteps.slice(0, 320)]);
  await shot('b1-lab-revoke.png');

  const passed = report.steps.filter(([, v]) => v === true || (typeof v === 'string' && v.length > 3) || (typeof v === 'number')).length;
  console.log(JSON.stringify(report, null, 1));
  console.log('STEPS_OK=' + passed + '/' + report.steps.length);
  fs.writeFileSync(path.join(OUT, 'b1-probe.json'), JSON.stringify(report, null, 1));
  ws.close();
  try { process.kill(-browser.pid); } catch {}
  process.exit(0);
})().catch(error => { console.error('PROBE_FAIL', error.message); process.exit(1); });
