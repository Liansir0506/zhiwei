// 真实 Edge 验收：总览看板 / 知识中心（含版本下拉与耗时）/ 业务与集成 / 文档与入库 / 只读诊断
// 用法：node scripts/verify_console_ui_browser.js <reportDir> <baseUrl>（baseUrl 默认 http://127.0.0.1:8010）
const fs = require('fs'), cp = require('child_process'), path = require('path');
const root = path.resolve(process.argv[2]);
const base = process.argv[3] || 'http://127.0.0.1:8010';
const project = path.resolve(__dirname, '..');
const state = JSON.parse(fs.readFileSync(path.join(project, '.runtime/original-local/state.json'), 'utf8'));
const key = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
const port = Number(new URL(base).port) || 80;
const debugPort = 19400 + (port % 100);
const url = base + '/#local_demo_key=' + encodeURIComponent(key);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, ws, serial = 0;
const pending = new Map(), requests = [];
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 25000);
    pending.set(id, {resolve, reject, timer}); ws.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (result.exceptionDetails) throw new Error('DOM evaluation failed: ' + expression.slice(0, 70));
  return result.result.value;
}
async function waitFor(expression, label, tries = 120) {
  for (let i = 0; i < tries; i++) { if (await evaluate(expression)) return true; await delay(100); }
  throw new Error('timed out waiting for ' + label);
}
async function clickTab(tab) { await evaluate(`(()=>{document.querySelector('.nav-item[data-tab="${tab}"]').click();return true;})()`); }
async function screenshot(name) {
  fs.writeFileSync(path.join(root, name), Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
}
async function timings() {
  return evaluate("(()=>{const list=performance.getEntriesByType('resource').filter(e=>e.name.includes('/kb/catalog')||e.name.includes('/dashboard/summary')||e.name.includes('/kb/version-scope'));" +
    "return list.map(e=>({path:new URL(e.name).pathname+e.search,ms:Math.round(e.duration)}));})()");
}
(async () => {
  try {
    fs.mkdirSync(root, {recursive: true});
    const edgePaths = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'];
    const edge = edgePaths.find(p => fs.existsSync(p));
    if (!edge) throw new Error('Edge unavailable');
    browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      '--user-data-dir=' + path.join(root, 'edge-profile'), '--remote-debugging-port=' + debugPort,
      '--remote-debugging-address=127.0.0.1', 'about:blank'], {windowsHide: true, stdio: 'ignore'});
    let targets;
    for (let i = 0; i < 100; i++) {
      try { targets = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json(); if (targets.length) break; } catch {}
      await delay(100);
    }
    if (!targets?.length) throw new Error('Edge unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {ws.onopen = resolve; ws.onerror = reject;});
    ws.onmessage = e => {
      const message = JSON.parse(e.data), task = pending.get(message.id);
      if (task) { clearTimeout(pending.get(message.id).timer); pending.delete(message.id);
        message.error ? task.reject(new Error('CDP failed')) : task.resolve(message.result); }
      else if (message.method === 'Network.requestWillBeSent') requests.push(message.params.request.url);
    };
    await call('Network.enable'); await call('Page.enable');
    await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
    await call('Page.navigate', {url});

    // 1) 落地页 = 总览看板
    await waitFor("document.querySelectorAll('#dashboardBody .dash-metric').length >= 6", 'dashboard metrics');
    await waitFor("document.querySelectorAll('#dashboardBody .dash-chart').length >= 1", 'dashboard charts');
    const dash = await evaluate(`(()=>{const body=document.getElementById('dashboardBody').textContent;
      return {metrics:document.querySelectorAll('#dashboardBody .dash-metric').length,
        charts:document.querySelectorAll('#dashboardBody .dash-chart').length,
        bars:document.querySelectorAll('#dashboardBody .dash-bars .brow').length,
        rows:document.querySelectorAll('#dashboardBody table.grid tr').length,
        activity:document.querySelectorAll('#dashboardBody .dash-activity li').length,
        hasDevices:body.includes('设备在线率'),hasKnowledge:body.includes('知识资产'),hasNotice:body.includes('演示数据窗口')};})()`);
    if (!dash.hasDevices || !dash.hasKnowledge || !dash.hasNotice) throw new Error('dashboard content missing: ' + JSON.stringify(dash));
    const chrome = await evaluate("(()=>{const qaOnly=['transportMode','exportBtn'];return {hidden:qaOnly.map(id=>getComputedStyle(document.getElementById(id)).display)," +
      "sparkInline:document.querySelectorAll('.dash-spark.inline').length," +
      "sparkPosition:getComputedStyle(document.querySelector('.dash-spark.inline')||document.body).position};})()");
    if (chrome.hidden.some(display => display !== 'none')) throw new Error('qa-only controls visible on dashboard: ' + chrome.hidden.join(','));
    if (chrome.sparkInline < 3 || chrome.sparkPosition !== 'static') throw new Error('perf sparklines not inline: ' + JSON.stringify(chrome));
    await screenshot('ui-dashboard.png');

    // 2) 知识中心：版本下拉 + 目录耗时
    await clickTab('kb');
    await waitFor("document.querySelectorAll('#kbResults .knowledge-card').length > 0", 'kb catalog');
    const kb = await evaluate(`(()=>{const options=[...document.getElementById('kbVersion').options].map(o=>({value:o.value,label:o.textContent,disabled:o.disabled}));
      return {options,noticeHidden:document.getElementById('kbVersionNotice').hidden,
        first:document.querySelector('#kbResults .knowledge-card h3')?.textContent||'',
        summary:document.getElementById('kbSummary').textContent};})()`);
    if (!kb.options.some(o => o.label.startsWith('当前激活：'))) throw new Error('active version option missing');
    if (!kb.options.some(o => o.disabled && o.label.includes('归档'))) throw new Error('archived versions not listed as disabled');
    await screenshot('ui-knowledge.png');

    // 3) 业务与集成：入口页
    await clickTab('business');
    await waitFor("document.querySelectorAll('#businessAccess .business-entry-card').length >= 3", 'business entry cards');
    const business = await evaluate("document.querySelectorAll('#businessAccess .business-entry-card').length + '|' + document.getElementById('businessStatus').textContent");
    await screenshot('ui-business.png');

    // 4) 文档与入库
    await clickTab('docs');
    await waitFor("document.getElementById('uploadNotice') && !document.getElementById('uploadNotice').textContent.includes('正在核对')", 'docs notice');
    await waitFor("document.querySelectorAll('#docsList .knowledge-card').length > 0 || document.querySelector('#docsList .empty-state')", 'docs list');
    await screenshot('ui-docs.png');

    // 5) 只读诊断 + 统一页头
    await clickTab('agent');
    await waitFor("document.querySelector('#view-agent .page-head h1') !== null", 'agent head');
    await clickTab('qa');
    await waitFor("getComputedStyle(document.getElementById('transportMode')).display !== 'none'", 'qa controls visible on qa tab');
    await clickTab('agent');
    const heads = await evaluate("(()=>{const tabs=['dashboard','qa','agent','kb','docs','business','versions','ops','eval','perm'];" +
      "return tabs.map(t=>{const v=document.getElementById('view-'+t);const h=v?.querySelector('.page-head h1, .workspace-heading h1');return t+':'+(h?h.textContent:'MISSING');});})()");
    if (heads.some(h => h.includes('MISSING'))) throw new Error('page head missing: ' + heads.join(','));
    await screenshot('ui-agent.png');

    const metrics = await timings();
    const catalog = metrics.filter(m => m.path.startsWith('/kb/catalog'));
    if (!catalog.length) throw new Error('no catalog timing captured');
    const slowest = Math.max(...catalog.map(m => m.ms));
    if (slowest > 4000) throw new Error('catalog too slow: ' + slowest + 'ms');
    const away = requests.filter(u => !u.startsWith(base + '/') && !u.startsWith('data:'));
    if (away.length) throw new Error('external request observed: ' + away.join(','));
    fs.writeFileSync(path.join(root, 'browser-result.json'), JSON.stringify({
      base, dashboard: dash, kb: {options: kb.options.length, summary: kb.summary, first: kb.first},
      business_cards: Number(business.split('|')[0]), chrome, heads,
      catalog_timings_ms: catalog.map(m => m.ms), dashboard_timings_ms: metrics.filter(m => m.path.startsWith('/dashboard')).map(m => m.ms),
      external_requests: away.length, local_requests: requests.length, state_instance: state.instance
    }, null, 2));
    console.log('Actual Edge UI verification passed: catalog max ' + slowest + 'ms; dashboard metrics ' + dash.metrics + '.');
  } finally {
    if (ws) ws.close();
    if (browser) {browser.kill(); await delay(800);}
  }
})().catch(error => {console.error(error.message); process.exitCode = 1;});
