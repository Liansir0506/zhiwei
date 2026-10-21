// Two already exposed direct-answer questions. Owned headless Edge, no provider.
const fs=require('fs'),path=require('path'),cp=require('child_process');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),checks=[],answers=[],pending=new Map();let browser,ws,seq=0;
  const profile=path.join(p.reportDir,'edge-profile');fs.mkdirSync(profile,{recursive:true});
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout'));},15000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error('Page expression failed');return r.result.value;};
  const until=async(expr,label)=>{for(let i=0;i<300;i++){if(await ev(expr))return;await pause(100);}throw new Error(label+' timeout');};
  const check=(ok,label)=>{if(!ok)throw new Error(label);checks.push(label);};
  const shot=async name=>fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));
  try{
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
    let port;for(let i=0;i<150;i++){try{port=Number(fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]);if(port)break;}catch{}await pause(100);}if(!port)throw new Error('Owned browser did not start');
    const pages=await(await fetch('http://127.0.0.1:'+port+'/json')).json();ws=new WebSocket(pages.find(x=>x.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});ws.onmessage=e=>{const d=JSON.parse(e.data),t=pending.get(d.id);if(t){clearTimeout(t.timer);pending.delete(d.id);d.error?t.reject(new Error('CDP error')):t.resolve(d.result);}};
    await call('Page.enable');await call('Runtime.enable');await call('Emulation.setDeviceMetricsOverride',{width:1365,height:980,deviceScaleFactor:1,mobile:false});await call('Page.navigate',{url:p.url});
    await until(`typeof ask==='function'`,'page load');
    await ev(`localStorage.setItem('api_key',${JSON.stringify(p.key)});localStorage.setItem('chat_transport','sse');localStorage.setItem('session_id',${JSON.stringify(p.session)});location.reload()`);
    await until(`typeof ask==='function'&&transportMode==='sse'&&!document.getElementById('send').disabled`,'SSE ready');
    await until(`document.getElementById('kbVer').textContent.includes(${JSON.stringify(p.version)})`,'active version');
    check(await ev(`sessionId===${JSON.stringify(p.session)}&&document.getElementById('qaVersion').value===''`),'Owned development session and ACTIVE version');
    await ev(`window.devCalls=0;const devFetch=window.fetch.bind(window);window.fetch=(url,options)=>{if(String(url)==='/qa/sse')devCalls++;return devFetch(url,options);}`);
    for(let i=0;i<p.cases.length;i++){
      const c=p.cases[i];await ev(`ask(${JSON.stringify(c.query)})`);
      await until(`!document.getElementById('send').disabled&&loadMessages(sessionId).filter(m=>m.role==='assistant').length===${i+1}`,'direct answer');
      const outcome=await ev(`({last:loadMessages(sessionId).filter(m=>m.role==='assistant').at(-1),visible:[...document.querySelectorAll('.msg.assistant')].at(-1)?.textContent,status:document.getElementById('status').textContent,calls:devCalls})`);
      check(outcome.last?.status==='OUT_OF_SCOPE'&&outcome.last?.kb===p.version,'Case '+c.id+' scope status and version');
      check(outcome.last?.decision?.route==='direct_answer'&&!outcome.last?.sources?.length,'Case '+c.id+' direct route and no citations');
      check(outcome.last.text===c.expected&&outcome.visible.includes(c.expected)&&outcome.status==='SSE 就绪','Case '+c.id+' visible answer and completed SSE');
      answers.push({id:c.id,status:outcome.last.status,route:outcome.last.decision.route,text:outcome.last.text,kb:outcome.last.kb,source_count:outcome.last.sources.length});
    }
    check(await ev(`devCalls===2`),'Exactly two deliberate local SSE submissions');await shot('boundary-desktop.png');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    check(await ev(`document.documentElement.scrollWidth<=390`),'390px conversation has no page overflow');await shot('boundary-mobile.png');
    fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,answers,session:p.session,headless:true,paid_calls:0,independent_cases:0},null,2));
    console.log(JSON.stringify({passed:true,checks:checks.length,local_sse_calls:2,paid_calls:0}));
  }catch(e){fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:false,checks,answers,error:String(e.message)},null,2));process.exitCode=1;}
  finally{if(ws){try{await call('Browser.close');}catch{}ws.close();}if(browser)browser.kill();for(const t of pending.values())clearTimeout(t.timer);}
});
