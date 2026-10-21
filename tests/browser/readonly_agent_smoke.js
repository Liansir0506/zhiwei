// Real isolated API cases, followed by explicitly labelled transport/UI probes.
const fs = require('fs'), path = require('path'), cp = require('child_process');
let input = ''; process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const p = JSON.parse(input), pending = new Map(), real = [], probes = [], screenshots = [];
  let ws, browser, sequence = 0;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout')); }, 15000);
    pending.set(id, {resolve, reject, timer}); ws.send(JSON.stringify({id, method, params}));
  });
  const evaluate = async expression => {
    const data = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (data.exceptionDetails) throw new Error('browser expression failed'); return data.result.value;
  };
  const until = async (expression, message) => {
    for (let i = 0; i < 600; i++) { if (await evaluate(expression)) return; await pause(200); } throw new Error(message);
  };
  const assert = async (expression, message) => { if (!await evaluate(expression)) throw new Error(message); };
  const submit = async (query, alarm = '', device = '') => evaluate(`(()=>{
    document.getElementById('agentQuery').value=${JSON.stringify(query)};
    document.getElementById('agentAlarm').value=${JSON.stringify(alarm)};
    document.getElementById('agentDevice').value=${JSON.stringify(device)};
    document.getElementById('agentForm').requestSubmit();
  })()`);
  const done = () => until(`!document.getElementById('agentSubmit').disabled`, 'request did not finish');
  const screenshot = async (width, name, trace = false) => {
    await call('Emulation.setDeviceMetricsOverride', {width, height: 1100, deviceScaleFactor: 1, mobile: width < 500});
    await evaluate(`document.getElementById('view-agent').scrollTop=0`); await pause(100);
    if (trace) { await evaluate(`document.querySelector('.agent-trace').parentElement.scrollIntoView({block:'start'})`); await pause(100); }
    await assert('document.documentElement.scrollWidth<=innerWidth+2', 'horizontal overflow');
    await assert(`!document.body.innerText.includes(${JSON.stringify(p.key)}) && ![...document.querySelectorAll('input')].some(x=>x.value.includes(${JSON.stringify(p.key)}))`, 'key rendered');
    const shot = await call('Page.captureScreenshot', {format: 'png'});
    fs.writeFileSync(path.join(p.reportDir, name), Buffer.from(shot.data, 'base64')); screenshots.push(name);
  };
  try {
    browser = cp.spawn(p.edge, ['--headless=new', '--incognito', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      `--user-data-dir=${p.profile}`, `--remote-debugging-port=${p.debugPort}`, '--remote-debugging-address=127.0.0.1', 'about:blank'], {windowsHide: true, stdio: 'ignore'});
    let targets;
    for (let i = 0; i < 100; i++) { try { targets = await (await fetch(`http://127.0.0.1:${p.debugPort}/json/list`)).json(); if (targets.length) break; } catch {} await pause(100); }
    if (!targets?.length) throw new Error('Edge unavailable');
    ws = new WebSocket(targets.find(x => x.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    ws.onmessage = event => { const data = JSON.parse(event.data), item = pending.get(data.id); if (!item) return;
      clearTimeout(item.timer); pending.delete(data.id); data.error ? item.reject(new Error('CDP failure')) : item.resolve(data.result); };
    await call('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});
    await call('Page.navigate', {url: p.url}); await until('typeof AgentWorkspace === "object" && typeof ask === "function"', 'page unavailable');
    await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.key)}); health(); switchTab('agent');
      window.realFetch=window.fetch; window.agentResponses=[]; window.agentBodies=[];
      window.fetch=async(...args)=>{const response=await realFetch(...args);if(args[0]==='/agent/diagnose'){
        agentBodies.push(JSON.parse(args[1].body));agentResponses.push({http_status:response.status,response:await response.clone().json()});}return response;};`);
    await until(`!document.getElementById('agentSubmit').disabled && document.getElementById('kbVer').textContent.includes(${JSON.stringify(p.version)})`, 'capability unavailable');
    await submit('ALM-AMF-REG 常见原因'); await done();
    await assert(`document.getElementById('agentResult').dataset.status==='ANSWERED' && document.querySelectorAll('#agentResult .evidence-card').length===3 && document.querySelectorAll('.agent-trace li').length===7`, 'real knowledge result/trace missing');
    await assert(`!document.querySelector('#agentResult').textContent.includes('[object Object]') && document.getElementById('qaVersion').hidden`, 'preview leaked into agent view');
    for (let i = 0; i < 3; i++) {
      await evaluate(`document.querySelectorAll('#agentResult .evidence-card button')[${i}].click()`);
      await until(`document.querySelectorAll('#agentResult .evidence-card')[${i}].textContent.includes('来源修订：v4-foundation-r3') && document.querySelectorAll('#agentResult .evidence-card')[${i}].textContent.includes('已通过当前版本与权限校验')`, 'real source reread missing');
    }
    real.push('knowledge_answer_3_sources_7_events_and_authenticated_rereads');
    await screenshot(1280, 'agent-1280.png'); await screenshot(390, 'agent-390.png');
    await screenshot(1280, 'agent-trace-1280.png', true); await screenshot(390, 'agent-trace-390.png', true);
    // Controlled source failures and late source responses; no real withdrawal.
    await evaluate(`window.capturingFetch=window.fetch;
      window.fetch=(url,options)=>String(url).startsWith('/kb/evidence/')?Promise.resolve(new Response('{}',{status:404})):capturingFetch(url,options);
      document.querySelector('#agentResult .evidence-card button').click()`);
    await until(`document.querySelector('#agentResult .evidence-card .context-error')?.textContent.includes('引用已失效')`, 'source failure missing');
    await assert(`!document.querySelector('#agentResult .evidence-card').querySelector('.evidence-original')`, 'old source body retained on failure');
    await evaluate(`window.fetch=(url,options)=>String(url).startsWith('/kb/evidence/')?new Promise(resolve=>window.releaseSource=()=>resolve(new Response(JSON.stringify({text:'controlled-private-source',source_revision:'probe'}),{status:200}))):capturingFetch(url,options);
      document.querySelector('#agentResult .evidence-card button').click();
      document.getElementById('keyInput').value='invalid-agent-key';document.getElementById('keySave').click();releaseSource();`);
    await pause(100); await assert(`document.getElementById('agentResult').hidden && !document.body.innerText.includes('controlled-private-source')`, 'late source published after key reset');
    await evaluate(`window.fetch=capturingFetch;localStorage.setItem('api_key',${JSON.stringify(p.key)});health()`);
    await until(`!document.getElementById('agentSubmit').disabled`, 'source probe restore failed');
    probes.push('source_404_clears_body_and_late_source_after_credential_reset_discarded');
    for (const [query, alarm, status, reason] of [
      ['现场注册失败诊断', '', 'NEEDS_CLARIFICATION', 'missing_entity'],
      ['注册失败原因', 'synthetic-absent', 'NO_EVIDENCE', 'entity_unavailable'],
      ['请重启出现ALM-AMF-REG的网元', '', 'OUT_OF_SCOPE', 'unsafe_request'],
      ['ALM-AMF-REG SOP', '', 'NO_EVIDENCE', 'missing_safety_controls']]) {
      await submit(query, alarm); await done();
      await assert(`document.getElementById('agentResult').dataset.status===${JSON.stringify(status)} && document.getElementById('agentResult').textContent.includes(${JSON.stringify(reason)}) && !document.querySelector('#agentResult .evidence-card')`, 'real terminal result missing');
      real.push(status + '/' + reason);
    }
    const actual = await evaluate('agentResponses');
    fs.writeFileSync(path.join(p.reportDir, 'real-agent-responses.json'), JSON.stringify(actual, null, 2));
    await assert(`agentBodies.every(body=>Object.keys(body).every(key=>['query','alarm_id','device_id'].includes(key)))`, 'history/version supplied to agent');
    // Real role and key denial. The guest credential is cleaned by the owner harness.
    for (const [key, expected] of [[p.guestKey, '当前凭据无只读诊断权限'], ['invalid-agent-key', '有效的 API Key']]) {
      await evaluate(`AgentWorkspace.reset();localStorage.setItem('api_key',${JSON.stringify(key)});health()`);
      await until(`!document.getElementById('agentSubmit').disabled`, 'auth case not ready');
      await submit('注册失败原因'); await done();
      await assert(`document.getElementById('agentProgress').textContent.includes(${JSON.stringify(expected)}) && document.getElementById('agentResult').hidden`, 'auth denial not rendered');
    } real.push('real_403_and_401_clear_result');
    await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.key)});health();window.fetch=realFetch;`);
    await until(`!document.getElementById('agentSubmit').disabled`, 'valid credential not restored');
    // All following responses are controlled UI/transport probes, not data or quality evidence.
    await evaluate(`window.probeData={run_id:'controlled-ui',status:'ANSWERED',stop_reason:'evidence_ready',mode:'domain',kb_version:'UI-PROBE',
      answer:'<img src=x onerror="window.xss=true"> controlled text',sources:[],trace:[],budget:{calls_used:0,calls_remaining:10,elapsed_ms:2},limitations:[],
      observations:[{observation_ref:{call_id:'probe-call',entity_type:'device',entity_id:'PROBE-DEVICE'},fields:{device_name:'<script>probe</script>'},source_system:null,recorded_at:null,retrieved_at:'controlled',freshness:'unknown',synthetic:true}]};
      window.fetch=(url,options)=>url==='/agent/diagnose'?Promise.resolve(new Response(JSON.stringify(probeData),{status:200})):realFetch(url,options);`);
    await submit('受控 UI 探针'); await done();
    await assert(`document.getElementById('agentResult').textContent.includes('<img src=x') && !document.querySelector('#agentResult img') && !window.xss && document.getElementById('agentResult').textContent.includes('模拟快照') && document.getElementById('agentResult').textContent.includes('记录时间：未知')`, 'safe text/provenance probe failed');
    probes.push('escaped_text_and_unknown_snapshot_provenance');
    for (const status of ['FAILED', 'NO_EVIDENCE', 'OUT_OF_SCOPE', 'NEEDS_CLARIFICATION']) {
      await evaluate(`probeData.status=${JSON.stringify(status)};probeData.stop_reason='timeout';probeData.answer=${JSON.stringify(status === 'NEEDS_CLARIFICATION' ? '受控澄清问题' : '')};`); await submit('受控终态'); await done();
      await assert(`document.getElementById('agentResult').dataset.status===${JSON.stringify(status)} && !document.getElementById('agentResult').textContent.includes('PROBE-DEVICE') && !document.getElementById('agentResult').textContent.includes('<img src=x')`, 'terminal state leaked prior answer');
    } probes.push('failed_refused_clarification_clear_evidence_and_observations');
    for (const [code, text] of [[422,'输入不符合'],[429,'工作槽位已满'],[503,'未启用']]) {
      await evaluate(`window.fetch=(url,options)=>url==='/agent/diagnose'?Promise.resolve(new Response('{}',{status:${code}})):realFetch(url,options)`);
      await submit('受控 HTTP 错误'); await done(); await assert(`document.getElementById('agentProgress').textContent.includes(${JSON.stringify(text)}) && document.getElementById('agentResult').hidden`, 'HTTP error missing');
    } probes.push('http_422_429_503');
    for (const [expression, expected] of [
      ['Promise.reject(new TypeError("controlled network failure"))','网络连接中断'],
      ['Promise.resolve(new Response("malformed",{status:200}))','响应无法读取']]) {
      await evaluate(`window.fetch=(url,options)=>url==='/agent/diagnose'?${expression}:realFetch(url,options)`);
      await submit('受控连接错误'); await done(); await assert(`document.getElementById('agentProgress').textContent.includes(${JSON.stringify(expected)}) && document.getElementById('agentResult').hidden`, 'network/parse error not rendered');
    } probes.push('network_disconnect_and_invalid_json_no_retry');
    await evaluate(`window.probeCalls=0;window.fetch=(url,options)=>url==='/agent/diagnose'?(probeCalls++,new Promise(resolve=>window.releaseProbe=()=>resolve(new Response(JSON.stringify({...probeData,status:'ANSWERED'}),{status:200})))):realFetch(url,options);`);
    await submit('受控取消'); await evaluate(`document.getElementById('agentCancel').click();releaseProbe()`); await pause(100);
    await assert(`document.getElementById('agentResult').hidden && document.getElementById('agentProgress').textContent.includes('已取消等待') && probeCalls===1`, 'late cancel response or automatic retry');
    await submit('受控凭据变更');
    await evaluate(`document.getElementById('keyInput').value='invalid-agent-key';document.getElementById('keySave').click();releaseProbe()`); await pause(100);
    await assert(`document.getElementById('agentResult').hidden && document.getElementById('agentQuery').value==='' && probeCalls===2`, 'late credential response');
    await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.key)});health()`); await until(`!document.getElementById('agentSubmit').disabled`, 'restore after reset failed');
    await submit('受控离页'); await evaluate(`switchTab('qa');releaseProbe()`); await pause(100);
    await assert(`document.getElementById('agentResult').hidden && probeCalls===3`, 'late tab response'); probes.push('cancel_key_change_and_leave_discard_late_responses_no_retry');
    await evaluate(`switchTab('agent')`); await submit('受控新会话'); await evaluate(`document.getElementById('newBtn').click();releaseProbe()`); await pause(100);
    await assert(`document.getElementById('agentResult').hidden && document.getElementById('agentQuery').value==='' && probeCalls===4`, 'late new conversation response'); probes.push('new_conversation_discards_late_diagnosis');
    await evaluate(`switchTab('agent');window.originalSetTimeout=window.setTimeout;window.setTimeout=(fn,delay,...args)=>originalSetTimeout(fn,delay===105000?20:delay,...args);
      window.fetch=(url,options)=>url==='/agent/diagnose'?new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('controlled timeout','AbortError')))):realFetch(url,options);`);
    await submit('受控客户端超时'); await done(); await assert(`document.getElementById('agentProgress').textContent.includes('等待已超时') && document.getElementById('agentResult').hidden`, 'client timeout not cleared');
    await evaluate(`window.setTimeout=originalSetTimeout;window.fetch=realFetch;AgentWorkspace.capability(false)`);
    await assert(`document.getElementById('agentSubmit').disabled && document.getElementById('agentAvailability').textContent.includes('当前不可用')`, 'disabled capability still submitted');
    probes.push('accelerated_client_timeout_and_disabled_capability');
    // Existing real SSE conversation still works after diagnostic navigation.
    await evaluate(`health();switchTab('qa');ask('FAQ-SIM-0932')`);
    await until(`!activeSse && document.querySelectorAll('.msg.assistant [data-status="ANSWERED"]').length===1`, 'QA SSE regression');
    await evaluate(`ask('这个条目的验证指标是什么')`);
    await until(`!activeSse && document.querySelectorAll('.msg.assistant [data-status="ANSWERED"]').length===2`, 'QA follow-up regression');
    await assert(`[...document.querySelectorAll('.msg.assistant .answer')].at(-1).textContent.includes('SIM-FAQ-0932') && !document.getElementById('qaVersion').hidden`, 'QA object/version display regression');
    real.push('existing_sse_and_followup');
    const result = {passed:true, real_cases:real, controlled_ui_probes:probes, screenshots, paid_llm_calls:0, real_domain_data:false, independent_quality:false};
    fs.writeFileSync(path.join(p.reportDir,'browser.json'),JSON.stringify(result,null,2)); console.log(JSON.stringify(result));
  } catch (error) { console.error(error.message); process.exitCode=1; }
  finally { if(ws?.readyState===1){try{await call('Browser.close')}catch{}ws.close()} browser?.kill();for(const item of pending.values())clearTimeout(item.timer); }
});
