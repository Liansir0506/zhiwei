/* 知识治理四 tab 浏览器实测：四 tab 渲染、缺口登记→已补→关闭闭环、导出 JSON、双宽度无溢出。 */
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
    `--user-data-dir=${PROFILE}`, '--window-size=1440,1000', '--remote-debugging-port=9335', 'about:blank'],
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
  for (let i = 0; i < 30; i++) { try { targets = await httpGet('http://127.0.0.1:9335/json'); break; } catch { await delay(500); } }
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
    if (r.exceptionDetails) throw new Error('eval failed: ' + (r.exceptionDetails.exception && r.exceptionDetails.exception.description || '').slice(0, 300) + ' | expr=' + expression.slice(0, 90));
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
  const clickTab = async label => {
    await evaluate(`[...document.querySelectorAll('#gkBody .gk-tab')].find(b=>b.textContent==='${label}').click()`);
    await waitFor(`(()=>{const p=document.querySelector('#gkBody .gk-panel');return p && p.childElementCount>0 && !/正在/.test(p.innerText||'');})()`, 'tab-' + label);
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

  // 切到知识治理
  await evaluate("document.querySelector('.nav-item[data-tab=\\'gk\\']').click()");
  for (let i = 0; i < 40; i++) { await delay(500); try { if (await evaluate("document.querySelectorAll('#gkBody .gk-tab').length >= 4")) break; } catch {} }
  await delay(1200);
  report.steps.push(['四tab渲染', await evaluate("document.querySelectorAll('#gkBody .gk-tab').length === 4")]);
  report.steps.push(['FAQ列表', await evaluate("document.querySelectorAll('#gkBody .gk-faq-row').length")]);
  report.steps.push(['回归网格或权限说明', await evaluate("document.querySelectorAll('#gkBody .gk-check').length > 0 || /仅管理员/.test(document.getElementById('gkBody').innerText)")]);
  await shot('p1-governance-qa.png');
  report.steps.push(['1440无横向溢出', await evaluate("document.documentElement.scrollWidth <= 1441")]);

  // 资料 tab
  await clickTab('资料');
  report.steps.push(['资料表格', await evaluate("document.querySelectorAll('#gkBody .gk-table tbody tr').length > 0")]);
  await shot('p1-governance-material.png');

  // 版本 tab
  await clickTab('版本');
  report.steps.push(['版本表格', await evaluate("document.querySelectorAll('#gkBody .gk-table tbody tr').length > 0")]);
  await shot('p1-governance-version.png');

  // 缺口 tab：登记 → 标记已补 → 发起回归按钮出现 → 关闭
  await clickTab('缺口');
  await evaluate(`(()=>{document.getElementById('gkGapTitle').value='P1 验收缺口：SMF 会话保持失败场景缺少处置依据';
    document.getElementById('gkGapTheme').value='ALM-SMF-PDU';
    document.querySelector('.gk-gap-form button[type=submit]').click();return true;})()`);
  await waitFor("[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')].some(tr=>tr.textContent.includes('SMF 会话保持'))", 'gap registered');
  report.steps.push(['缺口登记', await evaluate("[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')].some(tr=>tr.textContent.includes('SMF 会话保持'))")]);
  await evaluate(`(()=>{const rows=[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')];
    const row=rows.find(tr=>tr.textContent.includes('SMF 会话保持'));if(row)[...row.querySelectorAll('button')].find(b=>b.textContent==='标记已补').click();return !!row;})()`);
  await waitFor("[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')].some(tr=>tr.textContent.includes('SMF 会话保持')&&tr.textContent.includes('已补待回归'))", 'gap supplied');
  report.steps.push(['标记已补', await evaluate("[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')].some(tr=>tr.textContent.includes('SMF 会话保持')&&tr.textContent.includes('已补待回归'))")]);
  await evaluate(`(()=>{const rows=[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')];
    const row=rows.find(tr=>tr.textContent.includes('SMF 会话保持'));if(row)[...row.querySelectorAll('button')].find(b=>b.textContent==='发起回归').click();return !!row;})()`);
  await delay(1000);
  report.steps.push(['发起回归跳评测', await evaluate("document.querySelector('.tabview.active')?.id === 'view-eval' || document.getElementById('view-eval')?.classList.contains('active')")]);
  // 回缺口 tab 关闭
  await evaluate("document.querySelector('.nav-item[data-tab=\\'gk\\']').click()");
  await waitFor("document.querySelectorAll('#gkBody .gk-tab').length >= 4", 'gk re-enter');
  await clickTab('缺口');
  await evaluate(`(()=>{const rows=[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')];
    const row=rows.find(tr=>tr.textContent.includes('SMF 会话保持'));if(row)[...row.querySelectorAll('button')].find(b=>b.textContent==='关闭').click();return !!row;})()`);
  await waitFor("[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')].some(tr=>tr.textContent.includes('SMF 会话保持')&&tr.textContent.includes('已关闭'))", 'gap closed');
  report.steps.push(['缺口关闭', await evaluate("[...document.querySelectorAll('#gkBody .gk-gap-block table tbody tr')].some(tr=>tr.textContent.includes('SMF 会话保持')&&tr.textContent.includes('已关闭'))")]);
  await shot('p1-governance-gap.png');

  // 导出
  await call('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: path.resolve(OUT)}).catch(() => {});
  await evaluate(`(()=>{[...document.querySelectorAll('#gkBody .gk-gap-block button')].find(b=>b.textContent.includes('导出')).click();return true;})()`);
  await delay(1500);
  report.steps.push(['导出JSON', fs.readdirSync(OUT).filter(f => f === 'knowledge-gaps.json').length ? 'knowledge-gaps.json' : '(none)']);

  // 390 移动端
  await call('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  await delay(1200);
  report.steps.push(['390无横向溢出', await evaluate("document.documentElement.scrollWidth <= 391")]);
  await shot('p1-governance-390.png');

  const passed = report.steps.filter(([, v]) => v === true || (typeof v === 'string' && v !== '(none)') || (typeof v === 'number' && v > 0)).length;
  console.log(JSON.stringify(report, null, 1));
  console.log('STEPS_OK=' + passed + '/' + report.steps.length);
  fs.writeFileSync(path.join(OUT, 'governance-probe.json'), JSON.stringify(report, null, 1));
  ws.close();
  try { process.kill(-browser.pid); } catch {}
  process.exit(0);
})().catch(error => { console.error('PROBE_FAIL', error.message); process.exit(1); });
