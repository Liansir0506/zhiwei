// 前端体验验收（2026-10-19）：1440/1280/390 多宽度、ops/admin 两种角色、逐页交互与状态检查。
// 用法：node scripts/verify_frontend_20261019.js <reportDir> [baseUrl]
const fs = require('fs'), cp = require('child_process'), path = require('path');
const reportRoot = path.resolve(process.argv[2]);
const base = process.argv[3] || 'http://127.0.0.1:8010';
const project = path.resolve(__dirname, '..');
const opsKey = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
const adminKey = process.env.ZHIWEI_AUDIT_ADMIN_KEY;
if (!adminKey) throw new Error('Set ZHIWEI_AUDIT_ADMIN_KEY to a temporary administrator credential; revoke it after the audit.');
const delay = ms => new Promise(r => setTimeout(r, ms));
const debugPort = 19620;
let browser, ws, serial = 0;
const pending = new Map();
const consoleErrors = [];
const result = { base, generatedAt: new Date().toISOString(), widths: {}, interactions: {}, contrast: {}, errors: [] };

function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 30000);
    pending.set(id, { resolve, reject, timer, method });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  if (typeof expression !== 'string') throw new Error('non-string expression: ' + String(expression).slice(0, 80));
  try {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error('DOM evaluation failed: ' + expression.slice(0, 120));
    return result.result.value;
  } catch (error) {
    throw new Error(error.message + ' | expr=' + expression.slice(0, 160));
  }
}
async function waitFor(expression, label, tries = 200) {
  for (let i = 0; i < tries; i++) { if (await evaluate(expression)) return true; await delay(100); }
  throw new Error('timed out waiting for ' + label);
}
async function shot(name) {
  fs.writeFileSync(path.join(reportRoot, name), Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
}
async function setViewport(width, height) {
  await call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width <= 500 });
}
async function setKey(key) {
  await call('Page.navigate', { url: base });
  await delay(1500);
  await evaluate("localStorage.setItem('api_key', " + JSON.stringify(key) + ")");
  await call('Page.reload');
  await waitFor("document.querySelectorAll('#dashboardBody .dash-metric').length >= 4", 'dashboard after key switch', 300);
}
async function switchTab(tab) {
  // 导航/重载期间执行上下文会被销毁，evaluate 可能偶发失败：重试而不是直接中断整轮验收
  let lastError = null;
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      await evaluate(`(()=>{window.switchTab(${JSON.stringify(tab)});return true;})()`);
      await delay(500);
      return;
    } catch (error) {
      lastError = error;
      await delay(1500);
    }
  }
  throw lastError;
}
function viewReady(tab) {
  return `(()=>{const v=document.getElementById('view-${tab}');return v && v.classList.contains('active');})()`;
}
const READY = {
  dashboard: "document.querySelectorAll('#dashboardBody .dash-metric').length>=4 || /失败|失效|无权/.test(document.getElementById('dashboardBody')?.innerText||'')",
  qa: "!!document.querySelector('#view-qa .welcome') || document.querySelectorAll('#chat .msg').length>0",
  kb: "document.querySelectorAll('#kbResults .knowledge-card').length>0 || document.querySelectorAll('#kbResults .empty, #kbResults .notice').length>0",
  docs: "(document.getElementById('candidateState')?.textContent||'尚未核对')!=='尚未核对'",
  business: "/受限|未启用|本页|正在读取/.test(document.getElementById('businessStatus')?.textContent||'') || !document.getElementById('businessControls')?.hidden",
  agent: "!document.getElementById('agentRestricted')?.hidden || !document.getElementById('agentWorkspace')?.hidden",
  ops: "!document.getElementById('opsBody')?.hidden || /无法查看|暂无权限|受限/.test(document.getElementById('opsAccess')?.innerText||'')",
  eval: "!document.getElementById('evalBody')?.hidden || /无法查看|暂无权限/.test(document.getElementById('evalAccess')?.innerText||'')",
  perm: "!document.getElementById('permBody')?.hidden || /无法查看|暂无权限/.test(document.getElementById('permAccess')?.innerText||'')",
  versions: "document.querySelectorAll('#versionList .version-item').length>0 || /当前库版本状态|无可用版本|当前账号受限|只读摘要/.test(document.getElementById('versionStatus')?.textContent||'')",
  alerts: "document.querySelectorAll('#alertsTable .alert-table tbody tr').length>0 || !!document.querySelector('#alertsTable .alert-recover')",
  query: "!!document.getElementById('queryInput') && document.querySelectorAll('.qexample').length>0",
  gk: "document.querySelectorAll('#gkBody .gk-tab').length>=4",
  lab: "document.querySelectorAll('#labBody .lab-scenario').length>=6"
};
function readyExpr(tab) { return '(()=>(' + (READY[tab] || 'true') + '))()'; }
async function bodyMetrics() {
  return evaluate(`(()=>({bodyFont:getComputedStyle(document.body).fontSize,
    view:document.querySelector('.tabview.active')?.id||'',
    docOverflow:document.documentElement.scrollWidth-window.innerWidth,
    viewOverflow:(()=>{const v=document.querySelector('.tabview.active');if(!v)return 0;return Math.max(0,v.scrollWidth-v.clientWidth);})(),
    pageTitle:document.getElementById('pageTitle').textContent}))()`);
}

(async () => {
  try {
    fs.mkdirSync(reportRoot, { recursive: true });
    const edge = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(p => fs.existsSync(p));
    if (!edge) throw new Error('Edge unavailable');
    browser = cp.spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      '--user-data-dir=' + path.join(reportRoot, 'edge-profile'), '--remote-debugging-port=' + debugPort,
      '--remote-debugging-address=127.0.0.1', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
    let targets;
    for (let i = 0; i < 120; i++) { try { targets = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json(); if (targets.length) break; } catch {} await delay(100); }
    if (!targets?.length) throw new Error('Edge debugging unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    ws.onmessage = e => {
      const m = JSON.parse(e.data), task = pending.get(m.id);
      if (task) { clearTimeout(task.timer); pending.delete(m.id); m.error ? task.reject(new Error('CDP failed in ' + task.method + ': ' + (m.error.message || JSON.stringify(m.error)))) : task.resolve(m.result); return; }
      if (m.method === 'Runtime.exceptionThrown') consoleErrors.push('exception: ' + (m.params.exceptionDetails?.text || 'unknown'));
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
        consoleErrors.push('console.error: ' + (m.params.args || []).map(a => a.value || a.description || '').join(' ').slice(0, 240));
    };
    await call('Runtime.enable'); await call('Page.enable'); await call('Network.enable'); await call('Network.setCacheDisabled', { cacheDisabled: true }); await call('Network.enable'); await call('Network.setCacheDisabled', { cacheDisabled: true }); await call('Network.enable');

    // ---------------- 桌面 1440：ops 角色 ----------------
    await setViewport(1440, 900);
    await setKey(opsKey);
    result.widths['1440-ops'] = {};
    const tabs = ['dashboard', 'alerts', 'qa', 'query', 'kb', 'docs', 'gk', 'agent', 'business', 'versions', 'ops', 'eval', 'perm', 'lab'];
    for (const tab of tabs) {
      await switchTab(tab);
      await waitFor(viewReady(tab), 'view ' + tab);
      await waitFor(readyExpr(tab), 'ready ' + tab, 400);
      await delay(300);
      const metrics = await bodyMetrics();
      const text = await evaluate(`(document.querySelector('.tabview.active')?.innerText||'').slice(0,600)`);
      result.widths['1440-ops'][tab] = { ...metrics, text };
      if (metrics.docOverflow > 1 || metrics.viewOverflow > 1) result.errors.push(`1440 overflow ${tab}: ${metrics.docOverflow}/${metrics.viewOverflow}`);
      await shot(`1440-${tab}.png`);
    }

    // 具体断言与交互
    await switchTab('dashboard');
    await waitFor("document.querySelectorAll('#dashboardBody .dash-metric').length>=4", 'dashboard metrics');
    result.interactions.dashboard = await evaluate(`(()=>{
      const cards=[...document.querySelectorAll('#dashboardBody .dash-metric')];
      const labels=cards.map(c=>c.querySelector('.dash-metric-label')?.textContent.trim());
      const deviceSpots=cards[0]?.querySelectorAll('.dash-spark').length;
      const alarmSpots=cards[1]?.querySelectorAll('.dash-spark').length;
      const actions=cards.map(c=>!!c.querySelector('.dash-metric-action'));
      const rows=document.querySelectorAll('#dashboardBody table.dash-table tbody tr').length;
      return {labels,deviceSpots,alarmSpots,actions,rows,hero:!!document.querySelector('.dash-hero'),topo:!!document.querySelector('.topo-map')};})()`);
    if (result.interactions.dashboard.deviceSpots !== 0) result.errors.push('设备在线率仍绘制了复用趋势');
    if (result.interactions.dashboard.alarmSpots < 1) result.errors.push('活动告警未绘制真实趋势');
    if (!result.interactions.dashboard.rows) result.errors.push('待处置告警没有可点击行');
    // 点击待处置告警的“查看”→ 告警详情弹窗
    await evaluate("(()=>{const b=document.querySelector('#dashboardBody table.dash-table tbody tr .link-button');if(b)b.click();return !!b;})()");
    await delay(900);
    result.interactions.alarmDialog = await evaluate("(()=>{const host=document.getElementById('alertsDetail');const d=document.getElementById('alertDlg');const inline=!!host&&!host.hidden;return {open:inline||!!(d&&d.open),mode:inline?'inline':'dialog',title:document.querySelector('.alert-dialog-head h2')?.textContent||''};})()");
    if (!result.interactions.alarmDialog.open) result.errors.push('看板告警详情未打开');
    await evaluate("(()=>{const d=document.getElementById('alertDlg');if(d&&d.open)d.close();const back=[...document.querySelectorAll('#alertsDetail .ui-btn')].find(x=>x.textContent.includes('返回'));if(back)back.click();return true;})()");
    // 指标钻取 → 告警中心
    await evaluate("(()=>{const b=document.querySelectorAll('#dashboardBody .dash-metric')[1].querySelector('.dash-metric-action');b&&b.click();return true;})()");
    await delay(900);
    result.interactions.dashboardDrill = await evaluate("(()=>({view:document.querySelector('.tabview.active')?.id||'',hash:location.hash}))()");
    if (!String(result.interactions.dashboardDrill.view).includes('alerts')) result.errors.push('指标钻取未进入告警中心');
    // 告警中心：统计卡 + 表格 + 详情
    await waitFor(readyExpr('alerts'), 'alerts ready', 8000);
    result.interactions.alerts = await evaluate(`(()=>({stats:document.querySelectorAll('#alertsSummary .alert-stat').length,
      rows:document.querySelectorAll('#alertsTable .alert-table tbody tr').length,
      status:document.getElementById('alertsStatus').textContent}))()`);
    if (result.interactions.alerts.stats !== 4) result.errors.push('告警中心缺少汇总卡');
    if (!result.interactions.alerts.rows) result.errors.push('告警中心没有数据行');

    // 侧栏分组与入口收拢
    result.interactions.nav = await evaluate(`(()=>{
      const items=[...document.querySelectorAll('.nav-item')].filter(b=>!b.hidden).map(b=>b.textContent.trim());
      return {items, subnavItems:document.querySelectorAll('#knowledgeSubnav .subnav-item').length};
    })()`);
    for (const label of ['总览看板', '告警与资源', '诊断工作台', '运维问答', '业务查询', '知识管理']) {
      if (!result.interactions.nav.items.some(text => text.includes(label))) result.errors.push('侧栏缺少入口：' + label);
    }
    for (const stale of ['知识治理', '文档与入库', '版本与发布']) {
      if (result.interactions.nav.items.some(text => text.includes(stale))) result.errors.push('侧栏仍保留已收拢的入口：' + stale);
    }
    if (result.interactions.nav.subnavItems !== 4) result.errors.push('知识管理子页签不是 4 项');

    // 告警与资源 · 设备页签
    await evaluate("(()=>{const b=document.querySelector('#alertsSubtabs [data-alertstab=\"devices\"]'); if(b)b.click(); return true;})()");
    await waitFor("document.querySelectorAll('#devicesPane tbody tr').length > 0 || document.querySelector('#devicesPane .alert-empty')", 'devices pane', 120).catch(() => {});
    result.interactions.devices = await evaluate("(()=>({rows:document.querySelectorAll('#devicesPane tbody tr').length, stats:document.querySelectorAll('#devicesPane .alert-stat').length}))()");
    if (!result.interactions.devices.rows) result.errors.push('设备清单没有数据行');
    await shot('1440-devices.png');
    await evaluate("(()=>{const b=document.querySelector('#alertsSubtabs [data-alertstab=\"alerts\"]'); if(b)b.click(); return true;})()");
    await delay(400);

    // 全局搜索分类结果
    await evaluate("(()=>{const i=document.getElementById('globalSearch'); if(!i) return false; i.value='AMF'; document.getElementById('globalSearchForm').requestSubmit(); return true;})()");
    await waitFor("(()=>{const d=document.getElementById('searchDlg'); return d && d.open && d.querySelector('.search-group');})()", 'search results', 120).catch(() => {});
    result.interactions.search = await evaluate("(()=>{const d=document.getElementById('searchDlg'); return {open:!!(d&&d.open), groups:d?[...d.querySelectorAll('.search-group-head strong')].map(n=>n.textContent):[]};})()");
    if (!result.interactions.search.groups.length) result.errors.push('全局搜索没有分类结果');
    await shot('1440-search.png');
    await evaluate("(()=>{const d=document.getElementById('searchDlg'); if(d&&d.open)d.close(); return true;})()");
    await evaluate("(()=>{const b=document.querySelector('#alertsTable .alert-table tbody tr .link-button');if(b)b.click();return !!b;})()");
    await delay(700);
    result.interactions.alertDetail = await evaluate("(()=>{const host=document.getElementById('alertsDetail');const d=document.getElementById('alertDlg');const inline=!!host&&!host.hidden;return {open:inline||!!(d&&d.open),mode:inline?'inline':'dialog',title:document.querySelector('.alert-dialog-head h2')?.textContent||''};})()");
    if (!result.interactions.alertDetail.open) result.errors.push('告警详情未打开');
    await evaluate("(()=>{const d=document.getElementById('alertDlg');if(d&&d.open)d.close();const back=[...document.querySelectorAll('#alertsDetail .ui-btn')].find(x=>x.textContent.includes('返回'));if(back)back.click();return true;})()");

    // 知识中心：卡片层级 + 详情技术折叠
    await switchTab('kb');
    await waitFor("document.querySelectorAll('#kbResults .knowledge-card').length>0", 'kb cards', 300);
    result.interactions.knowledge = await evaluate(`(()=>{
      const card=document.querySelector('#kbResults .knowledge-card');
      const input=document.getElementById('kbSearch');
      return {cards:document.querySelectorAll('#kbResults .knowledge-card').length,
        tags:card?card.querySelectorAll('.knowledge-tags .pill').length:0,
        snippet:(card?.querySelector('.knowledge-snippet')?.textContent||'').length,
        radius:getComputedStyle(input).borderRadius, font:getComputedStyle(input).fontSize};})()`);
    if (!result.interactions.knowledge.tags) result.errors.push('知识卡片缺少类型/属性标签');
    if (result.interactions.knowledge.snippet > 130) result.errors.push('知识卡片摘要未截断');
    if (result.interactions.knowledge.radius !== '8px') result.errors.push('知识检索输入框未使用共享圆角');
    await evaluate("(()=>{const b=document.querySelector('#kbResults .knowledge-card .knowledge-actions button');if(b)b.click();return true;})()");
    await waitFor("!!document.querySelector('#kbDetail .knowledge-original') || !!document.querySelector('#kbDetail .context-error')", 'kb detail loaded', 800).catch(() => {});
    await delay(300);
    result.interactions.knowledgeDetail = await evaluate("(()=>({hasOriginal:!!document.querySelector('#kbDetail .knowledge-original'),techClosed:[...document.querySelectorAll('#kbDetail details.tech-details')].every(d=>!d.open),detailText:(document.getElementById('kbDetail')?.innerText||'').slice(0,120)}))()");
    if (!result.interactions.knowledgeDetail.hasOriginal) result.errors.push('知识详情未展示原文或错误原因');

    // 文档与入库：步骤、先决条件
    await switchTab('docs');
    await delay(1200);
    result.interactions.docs = await evaluate(`(()=>{
      const steps=[...document.querySelectorAll('#docsSteps li')].map(li=>({text:li.textContent.trim(),state:li.dataset.state||''}));
      const pre=document.getElementById('uploadPrereq');
      return {steps,prereqVisible:!!pre&&!pre.hidden,prereqText:pre?.textContent||'',
        fileField:!!document.querySelector('.ui-file'), candidateState:document.getElementById('candidateState')?.textContent||''};})()`);
    if (!result.interactions.docs.steps.length) result.errors.push('文档页缺少流程步骤');
    if (!result.interactions.docs.fileField) result.errors.push('文件选择未使用统一控件');

    // 只读诊断：不可用时仅受限面板
    await switchTab('agent');
    await delay(800);
    result.interactions.agent = await evaluate(`(()=>{const r=document.getElementById('agentRestricted'),w=document.getElementById('agentWorkspace');return {restrictedVisible:!!r&&!r.hidden,formWrapVisible:!!w&&!w.hidden,hasForm:!!document.getElementById('agentForm')};})()`);
    if (result.interactions.agent.restrictedVisible && result.interactions.agent.formWrapVisible) result.errors.push('诊断不可用时仍展示完整表单');

    // 业务与集成：受限面板与帮助
    await switchTab('business');
    await delay(800);
    result.interactions.business = await evaluate(`(()=>{const box=document.getElementById('businessAccess');const cmd=document.querySelector('#businessAccess .code-line');return {panel:!!box.querySelector('.surface'),help:!!box.querySelector('details'),command:cmd?cmd.textContent.trim():'',commandBg:cmd?getComputedStyle(cmd).backgroundColor:'',cards:box.querySelectorAll('.business-entry-card').length};})()`);
    if (result.interactions.business.commandBg === 'rgb(15, 23, 42)') result.errors.push('业务启动命令仍是黑底');
    if (result.interactions.business.cards > 2) result.errors.push('业务受限页仍是多列说明卡');

    // 全局：正文 14px；旧 pre.code 已改为浅色
    result.contrast.bodyFont = await evaluate("getComputedStyle(document.body).fontSize");
    result.contrast.preCodeBg = await evaluate("(()=>{const p=document.createElement('pre');p.className='code';document.body.append(p);const bg=getComputedStyle(p).backgroundColor;p.remove();return bg;})()");
    if (result.contrast.bodyFont !== '14px') result.errors.push('正文不是14px：' + result.contrast.bodyFont);
    if (result.contrast.preCodeBg === 'rgb(15, 23, 42)') result.errors.push('pre.code 仍是黑底');

    // ---------------- 管理员角色：版本 / 评测 / 权限 ----------------
    await setKey(adminKey);
    await setViewport(1440, 900);
    result.widths['1440-admin'] = {};
    for (const tab of ['versions', 'eval', 'perm']) {
      await switchTab(tab);
      await waitFor(viewReady(tab), 'admin view ' + tab);
      await waitFor(readyExpr(tab), 'admin ready ' + tab, 400);
      await delay(300);
      const metrics = await bodyMetrics();
      result.widths['1440-admin'][tab] = metrics;
      // 版本详情/评测报告需要真实加载完成后再截图；超时则保留当前状态。
      if (tab === 'versions') await waitFor("!!document.querySelector('#versionDetail h2')", 'version detail', 160).catch(() => {});
      if (tab === 'eval') await waitFor("!!document.querySelector('#evalSummary .surface')", 'eval summary', 120).catch(() => {});
      if (tab === 'perm') await waitFor("document.querySelectorAll('#permKeys table.data-table tbody tr').length>0", 'perm keys', 80).catch(() => {});
      await delay(200);
      await shot(`admin-1440-${tab}.png`);
      // 交互必须在本页仍处于激活状态时测量：切页会主动清空上一页 DOM。
      if (tab === 'versions') {
        result.interactions.versions = await evaluate(`(()=>{const items=document.querySelectorAll('#versionList .version-item');const details=[...document.querySelectorAll('#versionDetail details')];
          return {items:items.length,summary:!!document.getElementById('versionSummary')?.querySelector('.surface'),openDetails:details.filter(d=>d.open).length,totalDetails:details.length,
            versionText:(document.getElementById('versionDetail')?.innerText||'').slice(0,300)};})()`);
        if (!result.interactions.versions.items) result.errors.push('管理员版本列表为空');
        if (!result.interactions.versions.summary) result.errors.push('版本页缺少当前版本摘要');
        if (result.interactions.versions.openDetails > 0) result.errors.push('版本页技术详情默认展开');
        await evaluate("(()=>{const items=document.querySelectorAll('#versionList .version-item');if(items.length>1)items[1].click();return true;})()");
        await delay(3000);
        result.interactions.versionSelect = await evaluate("(()=>({selected:document.querySelector('#versionList .version-item[data-selected=\"true\"]')?.dataset.version||'',title:document.querySelector('#versionDetail h2')?.textContent||''}))()");
      }
      if (tab === 'eval') {
        result.interactions.eval = await evaluate(`(()=>({summary:!!document.getElementById('evalSummary')?.querySelector('.surface'),versionSelect:!!document.getElementById('evalVersion'),reportSelect:!!document.getElementById('evalReport'),gap:!!document.getElementById('evalGaps')?.textContent.trim()}))()`);
        if (!result.interactions.eval.versionSelect) result.errors.push('评测页缺少版本索引');
        if (!result.interactions.eval.summary) result.errors.push('评测页缺少报告摘要');
      }
      if (tab === 'perm') {
        result.interactions.perm = await evaluate(`(()=>{const body=document.getElementById('permBody');const first=body?.querySelector('.surface');
          return {firstIsKeys:!!first&&!!first.querySelector('#permKeys'),rows:document.querySelectorAll('#permKeys table.data-table tbody tr').length,
            matrixInDetails:!!document.querySelector('#permMatrix')?.closest('details'),issuePanel:!!document.getElementById('keyIssuePanel')&&!document.getElementById('keyIssuePanel').hidden};})()`);
        if (!result.interactions.perm.firstIsKeys) result.errors.push('访问管理未把凭据列表放在首位');
        if (!result.interactions.perm.matrixInDetails) result.errors.push('权限矩阵未折叠');
      }
    }

    // ---------------- 移动端 390：ops 角色 ----------------
    await setKey(opsKey);
    await setViewport(390, 844);
    result.widths['390-ops'] = {};
    for (const tab of ['dashboard', 'alerts', 'qa', 'kb', 'docs', 'business', 'agent', 'ops']) {
      await switchTab(tab);
      await waitFor(viewReady(tab), 'mobile view ' + tab);
      await waitFor(readyExpr(tab), 'mobile ready ' + tab, 400);
      await delay(250);
      const metrics = await bodyMetrics();
      result.widths['390-ops'][tab] = metrics;
      if (metrics.docOverflow > 1) result.errors.push(`390 文档横向溢出 ${tab}: ${metrics.docOverflow}`);
      if (metrics.viewOverflow > 1 && tab !== 'qa') result.errors.push(`390 视图内横向溢出 ${tab}: ${metrics.viewOverflow}`);
      await shot(`390-${tab}.png`);
      if (tab === 'kb') {
        await evaluate("(()=>{const i=document.getElementById('kbSearch');i.value='zzzz-no-such-knowledge-2026';document.getElementById('kbFilter').requestSubmit();return true;})()");
        await waitFor("(()=>{const n=document.querySelector('#kbResults .empty-state');return !!n && /没有符合|没有找到|无匹配/.test(n.textContent);})()", 'kb empty state', 120).catch(() => {});
        result.interactions.kbEmpty = await evaluate("(()=>{const n=document.querySelector('#kbResults .empty-state');return {empty:!!n && /没有符合|没有找到|无匹配/.test(n.textContent),text:(n?.textContent||'').slice(0,80)};})()");
        if (!result.interactions.kbEmpty.empty) result.errors.push('知识中心空态未显示');
        await evaluate("(()=>{const i=document.getElementById('kbSearch');i.value='';document.getElementById('kbFilter').requestSubmit();return true;})()");
        await waitFor("document.querySelectorAll('#kbResults .knowledge-card').length>0", 'kb restore', 120).catch(() => {});
      }
    }

    // ---------------- 错误与空态：无效凭据 ----------------
    await setViewport(1440, 900);
    await evaluate("localStorage.setItem('api_key','invalid-key-for-audit-000000000000000000')");
    await call('Page.reload');
    await delay(1200);
    await evaluate("(()=>{if(window.switchTab)window.switchTab('dashboard');return true;})()");
    await waitFor("(()=>{const t=document.getElementById('dashboardBody')?.innerText||'';return /失败|失效|无权|重新加载/.test(t);})()", 'invalid-key error state', 200).catch(()=>{});
    result.states = await evaluate(`(()=>({dashboardError:document.getElementById('dashboardBody')?.innerText.slice(0,200)||'',retry:!!document.querySelector('#dashboardBody button')}))()`);
    if (!/失败|失效|无权/.test(result.states.dashboardError)) result.errors.push('无效凭据未显示可理解的错误状态');

    result.consoleErrors = consoleErrors.filter(line => !/favicon|ERR_ABORTED|Failed to load resource/i.test(line));
    if (result.consoleErrors.length) result.errors.push('控制台错误：' + result.consoleErrors.slice(0, 3).join(' | '));
    fs.writeFileSync(path.join(reportRoot, 'frontend-verify.json'), JSON.stringify(result, null, 1));
    console.log(JSON.stringify({ ok: result.errors.length === 0, errors: result.errors, interactions: result.interactions, states: result.states }, null, 1));
    ws.close();
    browser.kill();
    process.exitCode = result.errors.length ? 1 : 0;
  } catch (error) {
    result.errors.push('FATAL: ' + error.message);
    fs.writeFileSync(path.join(reportRoot, 'frontend-verify.json'), JSON.stringify(result, null, 1));
    console.error('FAIL ' + error.message);
    try { ws && ws.close(); } catch {}
    try { browser && browser.kill(); } catch {}
    process.exitCode = 1;
  }
})();
