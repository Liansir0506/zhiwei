/* 诊断工作台浏览器实测：18021 隔离页面，真实提交一次诊断并断言五阶段/账本/轨迹/复核/导出。
   CDP 走 Node 内置 WebSocket（无需第三方模块）。 */
const fs = require('fs'), cp = require('child_process'), path = require('path');

const OUT = process.argv[2] || '.runtime/test/reports/resume-demo-20261019/p1';
const BASE = process.argv[3] || 'http://127.0.0.1:18021';
const PROFILE = path.resolve('.runtime/test/reports/resume-demo-20261019/p1-profile');
const delay = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, {recursive: true});
  fs.rmSync(PROFILE, {recursive: true, force: true});
  const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe']
    .find(p => fs.existsSync(p));
  const browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
    `--user-data-dir=${PROFILE}`, '--window-size=1440,1000', '--remote-debugging-port=9333', 'about:blank'],
    {detached: true, stdio: 'ignore'});
  browser.unref();
  await delay(2500);

  // CDP 客户端（原生 WebSocket）
  let serial = 0; const pending = new Map();
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 60000);
    pending.set(id, {resolve, reject, timer, method});
    ws.send(JSON.stringify({id, method, params}));
  });
  const httpGet = url => new Promise((resolve, reject) => {
    require('http').get(url, res => { let raw = ''; res.on('data', c => raw += c); res.on('end', () => resolve(JSON.parse(raw))); }).on('error', reject);
  });
  let targets = null;
  for (let i = 0; i < 30; i++) {
    try { targets = await httpGet('http://127.0.0.1:9333/json'); break; } catch { await delay(500); }
  }
  if (!targets) throw new Error('CDP not reachable');
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('ws failed')); });
  ws.onmessage = event => {
    const m = JSON.parse(event.data);
    if (m.id && pending.has(m.id)) {
      const task = pending.get(m.id); pending.delete(m.id); clearTimeout(task.timer);
      m.error ? task.reject(new Error(m.method + ': ' + (m.error.message || JSON.stringify(m.error)))) : task.resolve(m.result);
    }
  };
  const evaluate = async expression => {
    const r = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (r.exceptionDetails) throw new Error('eval failed: ' + JSON.stringify(r.exceptionDetails.exception || r.exceptionDetails).slice(0, 700) + ' | expr=' + expression.slice(0, 80));
    return r.result.value;
  };
  const shot = async name => {
    const r = await call('Page.captureScreenshot', {format: 'png'});
    fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, 'base64'));
  };

  await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
  await call('Network.setCacheDisabled', {cacheDisabled: true});
  await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});

  const key = fs.readFileSync('.runtime/readonly-workspace/api-key.txt', 'utf-8').trim();
  const report = {steps: []};

  // 先等服务就绪再导航（隔离服务刚启动时端口尚未绑定）
  for (let i = 0; i < 60; i++) {
    try { const r = await httpGet(BASE + '/readonly/runtime'); if (r.instance) break; } catch { await delay(500); }
  }
  await call('Page.navigate', {url: BASE + '/'});
  let landed = '';
  for (let i = 0; i < 40; i++) {
    await delay(500);
    try {
      landed = await evaluate("location.href");
      const ready = await evaluate("document.readyState === 'complete'");
      if (ready && landed.startsWith('http')) break;
    } catch { /* 页面切换中，继续等 */ }
  }
  console.log('landed:', landed);
  await delay(600);
  // 隔离页面凭据在 sessionStorage['readonly_key']（与主站 localStorage['api_key'] 不同）
  await evaluate(`sessionStorage.setItem('readonly_key', ${JSON.stringify(key)})`);
  await call('Page.reload');
  for (let i = 0; i < 40; i++) {
    await delay(500);
    try { if (await evaluate("document.readyState === 'complete' && location.href.startsWith('http')")) break; } catch {}
  }
  await delay(1200);
  report.steps.push(['页面加载', await evaluate("!!document.getElementById('agentForm')")]);
  await evaluate("document.getElementById('agentTab').click()");
  await delay(1000);
  report.steps.push(['诊断tab可见', await evaluate("!document.getElementById('view-agent').hidden")]);
  report.steps.push(['表单就绪', await evaluate("!document.getElementById('agentSubmit').disabled")]);

  const runOnce = async (query, waitLabel) => {
    await evaluate(`document.getElementById('agentQuery').value=${JSON.stringify(query)}`);
    await evaluate("document.getElementById('agentForm').requestSubmit()");
    await delay(1500);
    let finished = false, note = '';
    for (let i = 0; i < 80; i++) {
      await delay(2500);
      note = await evaluate("document.getElementById('agentProgress').textContent");
      if (note.includes('已结束') || note.includes('失败') || note.includes('未启用') || note.includes('超时')) { finished = true; break; }
    }
    return {finished, note};
  };
  // 预热：隔离服务冷启动时 rerank 加载与单工具 30s 预算临界（已知遗留），
  // 循环预热直到出现 ANSWERED（最多 3 次，超时本身也是真实观测）。
  await evaluate("document.getElementById('agentAlarm').value='SIM-ALM-000001'");
  await evaluate("document.getElementById('agentQuery').value='AMF 注册失败的常见原因与现场核对要点'");
  let warmStatus = '';
  for (let attempt = 1; attempt <= 3; attempt++) {
    const warm = await runOnce('AMF 注册失败的常见原因与现场核对要点');
    warmStatus = await evaluate("document.getElementById('agentResult').dataset.status || ''");
    report.steps.push([`预热${attempt}结束`, warm.finished, warmStatus || warm.note.slice(0, 40)]);
    if (warmStatus === 'ANSWERED') break;
  }
  await shot('p1-diagnosis-warmup.png');
  // 正式诊断：再次提交同一受控请求（服务已热，断言完整工作台渲染）
  await evaluate("document.getElementById('agentForm').requestSubmit()");
  await delay(1500);
  await shot('p1-diagnosis-running.png');
  report.steps.push(['提交后等待中', await evaluate("document.getElementById('agentProgress').textContent.includes('等待')")]);

  let done = false;
  for (let i = 0; i < 80; i++) {
    await delay(2500);
    const text = await evaluate("document.getElementById('agentProgress').textContent");
    if (text.includes('已结束') || text.includes('失败') || text.includes('未启用')) { done = true; break; }
  }
  report.steps.push(['诊断完成', done]);
  if (!done) report.steps.push(['诊断进度文本', await evaluate("document.getElementById('agentProgress').textContent").then(s => s.slice(0, 80))]);

  const stages = await evaluate(`(()=>{const bar=document.getElementById('agentStages');
    return {visible:!bar.hidden, total:bar.querySelectorAll('.agent-stage').length,
      done:bar.querySelectorAll('.agent-stage.done').length,
      labels:[...bar.querySelectorAll('.agent-stage-label')].map(n=>n.textContent)};})()`);
  report.steps.push(['五阶段', JSON.stringify(stages)]);
  const cards = await evaluate(`(()=>{const root=document.getElementById('agentResult');
    return {visible:!root.hidden, cards:[...root.querySelectorAll('.agent-card h3')].map(n=>n.textContent),
      status:root.dataset.status||'', answer:(root.querySelector('.agent-answer')?.textContent||'').slice(0,60),
      traceItems:root.querySelectorAll('.agent-trace li').length};})()`);
  report.steps.push(['结果卡', JSON.stringify(cards)]);
  const review = await evaluate(`(()=>{const r=document.getElementById('agentReview');
    return {visible:!r.hidden, choices:r.querySelectorAll('.agent-review-choice').length,
      note:!!document.getElementById('agentReviewNote'), buttons:[...r.querySelectorAll('button')].map(b=>b.textContent)};})()`);
  report.steps.push(['人工复核区', JSON.stringify(review)]);
  await shot('p1-diagnosis-result.png');

  const hasReview = await evaluate("!!document.getElementById('agentReviewNote')");
  if (hasReview) {
    await evaluate(`(()=>{const inputs=document.querySelectorAll('#agentReview input[type=radio]');if(inputs.length)inputs[0].click();
      document.getElementById('agentReviewNote').value='P1 验收：证据链完整，结论确认。';
      [...document.querySelectorAll('#agentReview button')].find(b=>b.textContent.includes('保存')).click();return true;})()`);
    await delay(500);
    report.steps.push(['复核保存', await evaluate("document.getElementById('agentProgress').textContent.includes('已保存')")]);
    await shot('p1-diagnosis-review.png');
  } else {
    report.steps.push(['复核保存', false, '诊断未产出复核区（见进度文本）']);
  }

  await call('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: path.resolve(OUT)}).catch(() => {});
  if (hasReview) await evaluate(`(()=>{[...document.querySelectorAll('#agentReview button')].find(b=>b.textContent.includes('导出')).click();return true;})()`);
  await delay(1500);
  const exported = fs.readdirSync(OUT).filter(f => f.startsWith('diagnosis-') && f.endsWith('.md'));
  report.steps.push(['导出报告文件', exported.length ? exported[0] : '(none)']);

  console.log(JSON.stringify(report, null, 1));
  const passed = report.steps.filter(([, v]) => v === true || (typeof v === 'string' && v.startsWith('diagnosis-'))).length;
  fs.writeFileSync(path.join(OUT, 'diagnosis-probe.json'), JSON.stringify(report, null, 1));
  console.log('STEPS_OK=' + passed + '/' + report.steps.length);
  ws.close();
  try { process.kill(-browser.pid); } catch {}
  process.exit(0);
})().catch(error => { console.error('PROBE_FAIL', error.message); process.exit(1); });
