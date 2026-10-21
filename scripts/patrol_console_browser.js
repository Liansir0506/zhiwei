// 全页巡检（运维视角）：逐页加载/操作/截图，收集问题清单。柔性策略——单点失败不中断，记录为问题继续。
// 用法：node scripts/patrol_console_browser.js <reportDir> [baseUrl]
const fs = require('fs'), cp = require('child_process'), path = require('path');
const root = path.resolve(process.argv[2]);
const base = process.argv[3] || 'http://127.0.0.1:8010';
const project = path.resolve(__dirname, '..');
const key = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
const port = Number(new URL(base).port) || 80;
const debugPort = 19660 + (port % 100);
const url = base + '/#local_demo_key=' + encodeURIComponent(key);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, ws, serial = 0;
const pending = new Map(), requests = [], requestUrls = new Map(), consoleErrors = [];
const report = {base, startedAt: new Date().toISOString(), pages: [], problems: [], external: [], consoleErrors: [], loadingFailures: []};

function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 30000);
    pending.set(id, {resolve, reject, timer});
    ws.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (result.exceptionDetails) throw new Error('eval failed: ' + expression.slice(0, 60));
  return result.result.value;
}
async function waitFor(expression, timeoutMs = 25000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { if (await evaluate(expression)) return true; } catch {}
    await delay(200);
  }
  return false;
}
async function clickTab(tab) { await evaluate(`(()=>{document.querySelector('.nav-item[data-tab="${tab}"]').click();return true;})()`); }
async function fullShot(name) {
  const metrics = await call('Page.getLayoutMetrics');
  const size = metrics.cssContentSize || metrics.contentSize;
  const shot = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true,
    clip: {x: 0, y: 0, width: Math.min(1440, Math.ceil(size.width)), height: Math.min(7000, Math.ceil(size.height)), scale: 1}});
  fs.writeFileSync(path.join(root, name), Buffer.from(shot.data, 'base64'));
}
async function viewShot(name) {
  const shot = await call('Page.captureScreenshot', {format: 'png'});
  fs.writeFileSync(path.join(root, name), Buffer.from(shot.data, 'base64'));
}
async function pageText(tab, chars = 500) {
  return evaluate(`(()=>{const v=document.getElementById('view-${tab}');return v?v.innerText.replace(/\\s+/g,' ').slice(0,${chars}):'';})()`);
}
async function visit(tab, readyExpr, label, timeoutMs = 25000) {
  const t0 = Date.now();
  await clickTab(tab);
  const ready = await waitFor(readyExpr, timeoutMs);
  await delay(700);
  const elapsedMs = Date.now() - t0;
  const text = await pageText(tab);
  const entry = {tab, ready, elapsedMs, text};
  if (!ready) report.problems.push({tab, kind: 'load-timeout', detail: label + ' not ready within ' + timeoutMs + 'ms'});
  const kw = ['加载失败', '读取失败', '不可用', '未启用', '请稍后重试', '失败'];
  const hit = kw.filter(k => text.includes(k));
  if (hit.length) entry.noticeKeywords = hit;
  return entry;
}

(async () => {
  try {
    fs.mkdirSync(root, {recursive: true});
    const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
    if (!edge) throw new Error('Edge unavailable');
    browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      '--user-data-dir=' + path.join(root, 'edge-profile'), '--remote-debugging-port=' + debugPort,
      '--remote-debugging-address=127.0.0.1', 'about:blank'], {windowsHide: true, stdio: 'ignore'});
    let targets;
    for (let i = 0; i < 100; i++) {
      try { targets = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json(); if (targets.length) break; } catch {}
      await delay(100);
    }
    if (!targets || !targets.length) throw new Error('Edge unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    ws.onmessage = e => {
      const message = JSON.parse(e.data), task = pending.get(message.id);
      if (task) { clearTimeout(task.timer); pending.delete(message.id);
        message.error ? task.reject(new Error('CDP failed: ' + JSON.stringify(message.error).slice(0, 80))) : task.resolve(message.result); return; }
      if (message.method === 'Network.requestWillBeSent') { requests.push(message.params.request.url); requestUrls.set(message.params.requestId, message.params.request.url); }
      if (message.method === 'Network.loadingFailed' && message.params.errorText !== 'net::ERR_ABORTED') {
        const u = requestUrls.get(message.params.requestId) || '';
        if (!u.includes('favicon')) report.loadingFailures.push({url: u.slice(0, 120), error: message.params.errorText});
      }
      if (message.method === 'Runtime.exceptionThrown') consoleErrors.push('exception: ' + String(message.params.exceptionDetails && message.params.exceptionDetails.text || '').slice(0, 160));
      if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') consoleErrors.push('log: ' + String(message.params.entry.text || '').slice(0, 160));
    };
    await call('Network.enable'); await call('Page.enable'); await call('Runtime.enable'); await call('Log.enable');
    await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
    await call('Page.navigate', {url});
    await delay(2500);

    // 1) 看板（默认落地）
    let entry = await visit('dashboard', "document.querySelectorAll('#dashboardBody .dash-metric').length >= 6", 'dashboard metrics', 30000);
    await evaluate('window.scrollTo(0, document.body.scrollHeight)');
    await delay(900);
    await fullShot('patrol-01-dashboard.png');
    report.pages.push(entry);

    // 2) 运维问答：真实提问
    entry = await visit('qa', "!!document.getElementById('q') && !!document.getElementById('f')", 'qa composer', 20000);
    const before = await evaluate("document.querySelectorAll('#chat .msg.assistant').length");
    await evaluate(`(()=>{const q=document.getElementById('q');q.value='AMF 注册失败常见原因有哪些？';q.dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('f').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));return true;})()`);
    const answered = await waitFor(`document.querySelectorAll('#chat .msg.assistant').length > ${before}`, 180000);
    let answerText = '', stable = 0;
    const qDeadline = Date.now() + 90000;
    while (Date.now() < qDeadline) {
      const now = await evaluate("(()=>{const a=document.querySelectorAll('#chat .msg.assistant');return a.length?a[a.length-1].innerText:'';})()");
      if (now && now === answerText && now.length > 30) { stable++; if (stable >= 3) break; } else { answerText = now; stable = 0; }
      await delay(1000);
    }
    entry.answered = answered;
    entry.answerChars = (answerText || '').length;
    entry.answerHead = (answerText || '').slice(0, 260).replace(/\s+/g, ' ');
    if (!answered) report.problems.push({tab: 'qa', kind: 'answer-timeout', detail: 'no assistant reply within 180s'});
    if (answered && entry.answerChars < 40) report.problems.push({tab: 'qa', kind: 'answer-too-short', detail: 'answer chars=' + entry.answerChars});
    await fullShot('patrol-02-qa.png');
    report.pages.push(entry);

    // 3) 只读诊断：真实查询
    entry = await visit('agent', "!!document.getElementById('agentQuery') && !!document.getElementById('agentForm')", 'agent form', 20000);
    const beforeAgent = await evaluate("((document.getElementById('agentResult')||{innerText:''}).innerText||'').length");
    await evaluate(`(()=>{const q=document.getElementById('agentQuery');q.value='AMF 注册失败';q.dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('agentForm').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));return true;})()`);
    const agentDone = await waitFor(`((document.getElementById('agentResult')||{innerText:''}).innerText||'').length > ${Math.max(40, beforeAgent + 30)}`, 150000);
    entry.agentAnswered = agentDone;
    if (!agentDone) report.problems.push({tab: 'agent', kind: 'query-timeout', detail: 'no result within 150s'});
    await fullShot('patrol-03-agent.png');
    report.pages.push(entry);

    // 4) 知识中心：目录 + 搜索
    entry = await visit('kb', "document.querySelectorAll('#kbResults .knowledge-card').length > 0", 'kb cards', 30000);
    entry.cardCount = await evaluate("document.querySelectorAll('#kbResults .knowledge-card').length");
    await fullShot('patrol-04-kb.png');
    await evaluate(`(()=>{const s=document.getElementById('kbSearch');s.value='AMF';const f=document.getElementById('kbFilter');f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));return true;})()`);
    await delay(2500);
    entry.searchCount = await evaluate("document.querySelectorAll('#kbResults .knowledge-card').length");
    entry.searchSummary = await evaluate("(document.getElementById('kbSummary')||{textContent:''}).textContent");
    await viewShot('patrol-04b-kb-search.png');
    report.pages.push(entry);

    // 5) 文档与入库
    entry = await visit('docs', "document.getElementById('uploadNotice') && !document.getElementById('uploadNotice').textContent.includes('正在核对')", 'docs notice', 30000);
    await fullShot('patrol-05-docs.png');
    report.pages.push(entry);

    // 6) 业务与集成
    entry = await visit('business', "document.querySelectorAll('#businessAccess .business-entry-card').length >= 3", 'business cards', 25000);
    await fullShot('patrol-06-business.png');
    report.pages.push(entry);

    // 7) 版本与发布
    entry = await visit('versions', "document.getElementById('view-versions').innerText.trim().length > 80", 'versions content', 30000);
    await fullShot('patrol-07-versions.png');
    report.pages.push(entry);

    // 8) 运行状态
    entry = await visit('ops', "document.getElementById('view-ops').innerText.trim().length > 80", 'ops content', 30000);
    await fullShot('patrol-08-ops.png');
    report.pages.push(entry);

    // 9) 评测记录
    entry = await visit('eval', "document.getElementById('view-eval').innerText.trim().length > 80", 'eval content', 30000);
    await fullShot('patrol-09-eval.png');
    report.pages.push(entry);

    // 10) 访问管理
    entry = await visit('perm', "document.getElementById('view-perm').innerText.trim().length > 80", 'perm content', 30000);
    await fullShot('patrol-10-perm.png');
    report.pages.push(entry);

    report.external = requests.filter(u => !u.startsWith(base + '/') && !u.startsWith('data:') && !u.startsWith('blob:') && !u.includes(':' + debugPort));
    report.consoleErrors = consoleErrors;
    report.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(root, 'patrol-result.json'), JSON.stringify(report, null, 2));
    console.log('patrol done. pages=' + report.pages.length + ' problems=' + report.problems.length +
      ' consoleErrors=' + consoleErrors.length + ' external=' + report.external.length);
  } finally {
    if (ws) ws.close();
    if (browser) { browser.kill(); await delay(800); }
  }
})().catch(error => { console.error('PATROL FAILED: ' + error.message); process.exitCode = 1; });
