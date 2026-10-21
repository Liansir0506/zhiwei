// Actual Edge verification of the local status page; no login or QA request.
const fs = require('fs'), cp = require('child_process'), path = require('path');
const root = path.resolve(process.argv[2]), url = process.argv[3];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, ws, serial = 0;
const pending = new Map();
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout')), 15000);
    pending.set(id, {resolve, reject, timer}); ws.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {expression, returnByValue: true});
  if (result.exceptionDetails) throw new Error('DOM verification failed');
  return result.result.value;
}
(async () => {
  try {
    browser = cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
      ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
       '--user-data-dir=' + path.join(root, 'edge-profile'), '--remote-debugging-port=19336',
       '--remote-debugging-address=127.0.0.1', 'about:blank'], {windowsHide: true, stdio: 'ignore'});
    let targets;
    for (let i = 0; i < 100; i++) {
      try { targets = await (await fetch('http://127.0.0.1:19336/json/list')).json(); if (targets.length) break; } catch {}
      await delay(100);
    }
    if (!targets?.length) throw new Error('Edge unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {ws.onopen = resolve; ws.onerror = reject;});
    ws.onmessage = e => {const result = JSON.parse(e.data), task = pending.get(result.id); if (task) {
      clearTimeout(task.timer); pending.delete(result.id);
      result.error ? task.reject(new Error('CDP failed')) : task.resolve(result.result);
    }};
    await call('Network.enable');
    await call('Network.setBlockedURLs', {urls: ['http://*', 'https://*']});
    await call('Emulation.setDeviceMetricsOverride', {width: 1280, height: 720, deviceScaleFactor: 1, mobile: false});
    await call('Page.navigate', {url});
    for (let i = 0; i < 100; i++) {if (await evaluate("!!document.getElementById('freshness')")) break; await delay(100);}
    if (!await evaluate("!!document.getElementById('login') && !document.getElementById('login').hidden")) throw new Error('Fresh login entry missing');
    fs.writeFileSync(path.join(root, 'status-desktop.png'), Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
    await call('Emulation.setDeviceMetricsOverride', {width: 390, height: 760, deviceScaleFactor: 1, mobile: true});
    const overflow = await evaluate('document.documentElement.scrollWidth > innerWidth');
    if (overflow) throw new Error('Status page overflows mobile width');
    fs.writeFileSync(path.join(root, 'status-mobile.png'), Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
    if (!await evaluate("Date.now=()=>Date.parse(checked)+120000;freshness();document.getElementById('login').hidden&&document.getElementById('freshness').textContent.includes('90秒')")) throw new Error('Stale status was accepted');
    fs.writeFileSync(path.join(root, 'status-stale.png'), Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
    fs.writeFileSync(path.join(root, 'browser-result.json'), JSON.stringify({fresh_login: true, stale_login_hidden: true, mobile_overflow: false, real_edge: true, network_and_paid_calls: 0}, null, 2));
    console.log('Actual Edge desktop/mobile and stale observation warning passed.');
  } finally {
    if (ws) ws.close();
    if (browser) {browser.kill(); await delay(1000);}
  }
})().catch(error => {console.error(error.message); process.exitCode = 1;});
