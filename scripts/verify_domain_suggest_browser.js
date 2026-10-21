// Real Edge verification of the workspace alarm/device suggestion combobox.
// Reads the owned runtime URL and local key file; drives keyboard and mouse selection.
const fs = require('fs'), cp = require('child_process'), path = require('path');
const root = path.resolve(process.argv[2]);
const project = path.resolve(__dirname, '..');
const state = JSON.parse(fs.readFileSync(path.join(project, '.runtime/original-local/state.json'), 'utf8'));
const key = fs.readFileSync(path.join(project, '.runtime/original-local/api-key.txt'), 'utf8').trim();
const url = state.url + '/#local_demo_key=' + encodeURIComponent(key);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, ws, serial = 0;
const pending = new Map(), requests = [];
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++serial, timer = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 20000);
    pending.set(id, {resolve, reject, timer}); ws.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (result.exceptionDetails) throw new Error('DOM evaluation failed: ' + expression.slice(0, 60));
  return result.result.value;
}
async function waitFor(expression, label, tries = 60) {
  for (let i = 0; i < tries; i++) { if (await evaluate(expression)) return true; await delay(100); }
  throw new Error('timed out waiting for ' + label);
}
async function typing(text) {
  await evaluate("document.getElementById('contextId').focus()");
  await evaluate("document.getElementById('contextId').value=''");
  await call('Input.insertText', {text});
  if (await evaluate("document.getElementById('contextId').value") !== text) {
    await evaluate("(()=>{const el=document.getElementById('contextId');el.value=" + JSON.stringify(text) +
                   ";el.dispatchEvent(new Event('input',{bubbles:true}));return el.value;})()");
  }
}
async function pressKey(type, name, code, vk) {
  await call('Input.dispatchKeyEvent', {type, key: name, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk});
}
async function screenshot(name) {
  fs.writeFileSync(path.join(root, name), Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
}
(async () => {
  try {
    fs.mkdirSync(root, {recursive: true});
    browser = cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
      ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
       '--user-data-dir=' + path.join(root, 'edge-profile'), '--remote-debugging-port=19337',
       '--remote-debugging-address=127.0.0.1', 'about:blank'], {windowsHide: true, stdio: 'ignore'});
    let targets;
    for (let i = 0; i < 100; i++) {
      try { targets = await (await fetch('http://127.0.0.1:19337/json/list')).json(); if (targets.length) break; } catch {}
      await delay(100);
    }
    if (!targets?.length) throw new Error('Edge unavailable');
    ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {ws.onopen = resolve; ws.onerror = reject;});
    ws.onmessage = e => {
      const message = JSON.parse(e.data), task = pending.get(message.id);
      if (task) {
        clearTimeout(task.timer); pending.delete(message.id);
        message.error ? task.reject(new Error('CDP failed: ' + message.method)) : task.resolve(message.result);
      } else if (message.method === 'Network.requestWillBeSent') {
        requests.push(message.params.request.url);
      }
    };
    await call('Network.enable');
    await call('Page.enable');
    await call('Emulation.setDeviceMetricsOverride', {width: 1280, height: 800, deviceScaleFactor: 1, mobile: false});
    await call('Page.navigate', {url});
    await waitFor("!!document.getElementById('contextId') && localStorage.getItem('api_key')", 'workspace and imported key');
    const away = requests.filter(u => !/^http:\/\/(127\.0\.0\.1|localhost):8010\//.test(u) && !u.startsWith('data:'));
    if (away.length) throw new Error('external request observed: ' + away.join(','));

    // 1) keyboard path: type -> dropdown -> ArrowDown -> Enter -> autofill + context lookup
    await typing('SIM-ALM-0000');
    await waitFor("!document.getElementById('contextSuggest').hidden", 'alarm suggestion list');
    const alarmOptions = await evaluate("Array.from(document.querySelectorAll('#contextSuggest .suggest-item strong')).map(n=>n.textContent)");
    if (!alarmOptions.length || !alarmOptions.every(t => t.includes('SIM-ALM-'))) throw new Error('unexpected alarm options');
    await screenshot('suggest-alarm-desktop.png');
    await pressKey('rawKeyDown', 'ArrowDown', 'ArrowDown', 40); await pressKey('keyUp', 'ArrowDown', 'ArrowDown', 40);
    if (await evaluate("document.getElementById('contextId').getAttribute('aria-activedescendant')") !== 'suggestOption0')
      throw new Error('ArrowDown did not activate first option');
    await pressKey('rawKeyDown', 'Enter', 'Enter', 13); await pressKey('keyUp', 'Enter', 'Enter', 13);
    await waitFor("document.getElementById('contextId').value && document.getElementById('contextSuggest').hidden", 'autofill from keyboard');
    const alarmId = await evaluate("document.getElementById('contextId').value");
    if (!alarmOptions[0].startsWith(alarmId)) throw new Error('Enter did not fill the highlighted alarm id');
    await waitFor("document.getElementById('businessContent').textContent.includes(" + JSON.stringify(alarmId) + ")", 'alarm context lookup');
    await screenshot('suggest-alarm-selected.png');

    // 2) mouse path on the device kind: switch kind, type, click first option by real coordinates
    await evaluate("(()=>{const s=document.getElementById('contextKind');s.value='device';s.dispatchEvent(new Event('change'));return s.value;})()");
    await typing('SIM-DEV-000');
    await waitFor("!document.getElementById('contextSuggest').hidden", 'device suggestion list');
    const deviceOptions = await evaluate("Array.from(document.querySelectorAll('#contextSuggest .suggest-item strong')).map(n=>n.textContent)");
    if (!deviceOptions.length || !deviceOptions.every(t => t.includes('SIM-DEV-'))) throw new Error('unexpected device options');
    const box = await evaluate("JSON.stringify(document.querySelector('#contextSuggest .suggest-item').getBoundingClientRect())");
    const rect = JSON.parse(box), x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    await call('Input.dispatchMouseEvent', {type: 'mousePressed', x, y, button: 'left', clickCount: 1});
    await call('Input.dispatchMouseEvent', {type: 'mouseReleased', x, y, button: 'left', clickCount: 1});
    await waitFor("document.getElementById('contextSuggest').hidden && document.getElementById('contextId').value.startsWith('SIM-DEV-')", 'mouse autofill');
    const deviceId = await evaluate("document.getElementById('contextId').value");
    await waitFor("document.getElementById('businessContent').textContent.includes(" + JSON.stringify(deviceId) + ")", 'device context lookup');
    await screenshot('suggest-device-selected.png');

    // 3) alarm-code matching, keyboard navigation bounds and escape
    await evaluate("(()=>{const s=document.getElementById('contextKind');s.value='alarm';s.dispatchEvent(new Event('change'));return s.value;})()");
    await typing('ALM-AMF');
    await waitFor("!document.getElementById('contextSuggest').hidden", 'code suggestion list');
    const codeOptions = await evaluate("Array.from(document.querySelectorAll('#contextSuggest .suggest-item strong')).map(n=>n.textContent)");
    if (!codeOptions.length || !codeOptions.some(t => t.includes('ALM-AMF'))) throw new Error('alarm code match missing');
    await pressKey('rawKeyDown', 'ArrowUp', 'ArrowUp', 38);
    const wrap = await evaluate("document.getElementById('contextId').getAttribute('aria-activedescendant')");
    if (!wrap) throw new Error('ArrowUp did not activate an option');
    await pressKey('rawKeyDown', 'Escape', 'Escape', 27); await pressKey('keyUp', 'Escape', 'Escape', 27);
    if (!await evaluate("document.getElementById('contextSuggest').hidden")) throw new Error('Escape did not close the list');

    // 4) mobile width: open the context drawer and keep the suggestion panel inside the viewport
    await evaluate("document.body.classList.add('context-open')");
    await call('Emulation.setDeviceMetricsOverride', {width: 390, height: 760, deviceScaleFactor: 1, mobile: true});
    await typing('SIM-ALM-0000');
    await waitFor("!document.getElementById('contextSuggest').hidden", 'mobile suggestion list');
    const mobile = await evaluate("(()=>{const r=document.querySelector('#contextSuggest').getBoundingClientRect();"
      + "return JSON.stringify({left:r.left,right:r.right,width:r.width,inner:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth+1});})()");
    const mobileBox = JSON.parse(mobile);
    if (mobileBox.overflow || mobileBox.right > mobileBox.inner + 1 || mobileBox.left < -1)
      throw new Error('suggestion panel overflows mobile viewport: ' + mobile);
    await screenshot('suggest-alarm-mobile.png');

    fs.writeFileSync(path.join(root, 'browser-result.json'), JSON.stringify({
      keyboard_selection: alarmId, mouse_selection: deviceId, arrow_up_wrap: wrap, options: alarmOptions.length + '/' + deviceOptions.length + '/' + codeOptions.length,
      escape_closes: true, mobile_overflow: false, mobile_box: mobileBox, external_requests: away.length,
      local_requests: requests.length, real_edge: true, assert_version: 'local-demo-release-r1'
    }, null, 2));
    console.log('Actual Edge combobox verification passed: keyboard ' + alarmId + ', mouse ' + deviceId + '.');
  } finally {
    if (ws) ws.close();
    if (browser) {browser.kill(); await delay(1000);}
  }
})().catch(error => {console.error(error.message); process.exitCode = 1;});
