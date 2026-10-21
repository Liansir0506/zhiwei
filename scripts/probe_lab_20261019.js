/* 场景实验室实测：授权撤回全流程 + 缓存失效 + 任务/版本引导 + 角色视角 + 390 无溢出。 */
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
    `--user-data-dir=${PROFILE}`, '--window-size=1440,1000', '--remote-debugging-port=9341', 'about:blank'],
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
  for (let i = 0; i < 30; i++) { try { targets = await httpGet('http://127.0.0.1:9341/json'); break; } catch { await delay(500); } }
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
  // 用管理员凭据（场景一/二需要 admin）
  const key = 'ui-audit-admin-20261019';
  const report = {steps: []};

  await call('Page.navigate', {url: BASE});
  for (let i = 0; i < 40; i++) { await delay(500); try { if (await evaluate("document.readyState==='complete' && location.href.startsWith('http')")) break; } catch {} }
  await delay(500);
  await evaluate(`localStorage.setItem('api_key', ${JSON.stringify(key)})`);
  await call('Page.reload');
  for (let i = 0; i < 40; i++) { await delay(500); try { if (await evaluate("document.readyState==='complete' && location.href.startsWith('http')")) break; } catch {} }
  await delay(1800);

  await evaluate("document.querySelector('.nav-item[data-tab=\"lab\"]').click()");
  await waitFor("document.querySelectorAll('#labBody .lab-scenario').length >= 5", 'lab scenarios');
  report.steps.push(['五个场景卡', await evaluate("document.querySelectorAll('#labBody .lab-scenario').length")]);
  await shot('p2-lab.png');

  // 场景一：授权撤回
  await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('授权撤回'));
    [...card.querySelectorAll('button')].find(b=>b.textContent.includes('开始演练')).click();return true;})()`);
  await waitFor(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('授权撤回'));
    return card && card.querySelectorAll('.lab-step').length>=5;})()`, 'revoke steps');
  const revoke = await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('授权撤回'));
    return [...card.querySelectorAll('.lab-step')].map(s=>s.textContent.replace(/\\s+/g,' ').trim()).join(' | ');})()`);
  report.steps.push(['授权撤回轨迹', revoke.slice(0, 260)]);
  await shot('p2-lab-revoke.png');

  // 场景二：缓存失效
  await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('缓存失效'));
    [...card.querySelectorAll('button')].find(b=>b.textContent.includes('开始演练')).click();return true;})()`);
  await waitFor(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('缓存失效'));
    return card && card.querySelectorAll('.lab-step').length>=4;})()`, 'cache steps');
  const cache = await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('缓存失效'));
    return [...card.querySelectorAll('.lab-step')].map(s=>s.textContent.replace(/\\s+/g,' ').trim()).join(' | ');})()`);
  report.steps.push(['缓存失效轨迹', cache.slice(0, 260)]);

  // 场景三/四：引导内容
  await waitFor(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('入库任务重试'));
    return card && (card.querySelector('table') || card.querySelector('.notice'));})()`, 'retry card');
  report.steps.push(['任务重试卡', await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('入库任务重试'));
    return !!(card.querySelector('table')||card.querySelector('.notice'));})()`)]);
  report.steps.push(['版本回滚卡', await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('版本回滚'));
    return /当前激活版本/.test(card.textContent);})()`)]);
  report.steps.push(['角色视角', await evaluate(`(()=>{const cards=[...document.querySelectorAll('#labBody .lab-scenario')];
    const card=cards.find(c=>c.textContent.includes('角色视角'));
    return card.querySelectorAll('.lab-role').length;})()`)]);

  // 390
  await call('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  await delay(1000);
  report.steps.push(['390无横向溢出', await evaluate("document.documentElement.scrollWidth <= 391")]);
  await shot('p2-lab-390.png');

  const passed = report.steps.filter(([, v]) => v === true || (typeof v === 'number' && v > 0) || (typeof v === 'string' && v.length > 10)).length;
  console.log(JSON.stringify(report, null, 1));
  console.log('STEPS_OK=' + passed + '/' + report.steps.length);
  fs.writeFileSync(path.join(OUT, 'lab-probe.json'), JSON.stringify(report, null, 1));
  ws.close();
  try { process.kill(-browser.pid); } catch {}
  process.exit(0);
})().catch(error => { console.error('PROBE_FAIL', error.message); process.exit(1); });
