// Real approved historical domain data and real knowledge reads; no UI fixtures.
const fs=require('fs'),path=require('path'),cp=require('child_process');
let input='';process.stdin.on('data',s=>input+=s);
process.stdin.on('end',async()=>{
 const p=JSON.parse(input),pending=new Map(),screenshots=[];let ws,browser,sequence=0;
 const pause=ms=>new Promise(r=>setTimeout(r,ms));
 const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout'))},15000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
 const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('browser expression failed');return r.result.value;};
 const until=async(expression,message)=>{for(let i=0;i<600;i++){if(await evaluate(expression))return;await pause(200)}throw Error(message);};
 const assert=async(expression,message)=>{if(!await evaluate(expression))throw Error(message)};
 try{
  browser=cp.spawn(p.edge,['--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check',`--user-data-dir=${p.profile}`,`--remote-debugging-port=${p.debugPort}`,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
  let targets;for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${p.debugPort}/json/list`)).json();if(targets.length)break}catch{}await pause(100)}
  if(!targets?.length)throw Error('Edge unavailable');ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});ws.onmessage=e=>{const d=JSON.parse(e.data),v=pending.get(d.id);if(!v)return;clearTimeout(v.timer);pending.delete(d.id);d.error?v.reject(Error('CDP failure')):v.resolve(d.result)};
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:1100,deviceScaleFactor:1,mobile:false});await call('Page.navigate',{url:p.url});
  await until('typeof AgentWorkspace === "object"','page unavailable');
  await evaluate(`localStorage.setItem('api_key',${JSON.stringify(p.key)});health();switchTab('agent');window.actualFetch=window.fetch;window.domainResponses=[];window.domainBodies=[];
   window.fetch=async(...args)=>{const response=await actualFetch(...args);if(args[0]==='/agent/diagnose'){domainBodies.push(JSON.parse(args[1].body));domainResponses.push(await response.clone().json())}return response};`);
  await until(`!document.getElementById('agentSubmit').disabled && document.getElementById('kbVer').textContent.includes(${JSON.stringify(p.version)})`,'capability unavailable');
  await evaluate(`document.getElementById('agentQuery').value='ALM-AMF-REG 常见原因';document.getElementById('agentAlarm').value='SIM-ALM-000001';document.getElementById('agentForm').requestSubmit()`);
  await until(`!document.getElementById('agentSubmit').disabled`,'diagnosis unfinished');
  await assert(`document.getElementById('agentResult').dataset.status==='ANSWERED' && domainResponses[0].observations.length===5 && domainResponses[0].mode==='domain'`,'actual domain answer missing');
  const n=await evaluate(`domainResponses[0].sources.length`);if(n<1||n>3)throw Error('missing actual sources');
  for(let i=0;i<n;i++){
   await evaluate(`document.querySelectorAll('#agentResult .evidence-card button')[${i}].click()`);
   await until(`document.querySelectorAll('#agentResult .evidence-card')[${i}].textContent.includes('来源修订：v4-foundation-r3') && document.querySelectorAll('#agentResult .evidence-card')[${i}].textContent.includes('已通过当前版本与权限校验')`,'authenticated source reread missing');
  }
  await evaluate(`window.observationSection=[...document.querySelectorAll('#agentResult .agent-card')].find(x=>x.querySelector('h3').textContent==='实体观测');[...observationSection.querySelectorAll('details')].forEach(x=>x.open=true)`);
  await assert(`observationSection.querySelectorAll('details').length===5 && observationSection.textContent.includes('SIM-DEV-0001') && observationSection.textContent.includes('SIM-ALM-000001') && observationSection.textContent.includes('SIM-OBS-000001') && observationSection.textContent.includes('SIM-TT-00001') && observationSection.textContent.includes('SIM-CHG-00001') && observationSection.textContent.includes('观测出处：') && observationSection.textContent.includes('记录时间：未知') && observationSection.textContent.includes('模拟快照') && observationSection.textContent.includes('UTC 保存')`,'snapshot provenance/timezone missing');
  await assert(`!['payload_json','attributes_json','labels_json','assignee_user_id','operator_user_id','[object Object]'].some(x=>observationSection.textContent.includes(x)) && domainBodies.every(b=>Object.keys(b).every(k=>['query','alarm_id','device_id'].includes(k)))`,'private fields or extra request context');
  await assert(`[...observationSection.querySelectorAll('dt')].find(x=>x.textContent==='cleared_at').parentElement.querySelector('dd').textContent==='未知'`,'null observation field must say unknown');
  for(const width of [1280,390]){
   await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:width<500});
   for(const [label,expression] of [['answer',`document.getElementById('view-agent').scrollTop=0`],['snapshot',`observationSection.scrollIntoView({block:'start'})`],['history',`observationSection.querySelectorAll('details')[3].scrollIntoView({block:'start'})`]]){
    await evaluate(expression);await pause(150);await assert('document.documentElement.scrollWidth<=innerWidth+2','horizontal overflow');
    await assert(`[...observationSection.querySelectorAll('.detail-list > div')].every(row=>{const label=row.querySelector('dt'),value=row.querySelector('dd'),range=document.createRange();range.selectNodeContents(label);return [...range.getClientRects()].every(r=>r.right<=value.getBoundingClientRect().left+1)})`,'observation label overlaps value');
    await assert(`!document.body.innerText.includes(${JSON.stringify(p.key)}) && ![...document.querySelectorAll('input')].some(x=>x.value.includes(${JSON.stringify(p.key)}))`,'key rendered');
    const shot=await call('Page.captureScreenshot',{format:'png'}),name=`domain-${label}-${width}.png`;fs.writeFileSync(path.join(p.reportDir,name),Buffer.from(shot.data,'base64'));screenshots.push(name);
   }
  }
  fs.writeFileSync(path.join(p.reportDir,'browser-real-response.json'),JSON.stringify(await evaluate('domainResponses[0]'),null,2));
  const result={passed:true,real_domain_data:true,real_knowledge_retrieval:true,observations:5,sources:n,fresh_source_reads:n,widths:[1280,390],screenshots,controlled_ui_probes:0,paid_llm_calls:0,independent_quality:false};
  fs.writeFileSync(path.join(p.reportDir,'browser.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(e){console.error(e.message);process.exitCode=1}
 finally{if(ws?.readyState===1){try{await call('Browser.close')}catch{}ws.close()}browser?.kill();for(const v of pending.values())clearTimeout(v.timer)}
});
