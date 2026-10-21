// Local offline review, headless Edge; no model, login, publication or remote page loads.
const fs=require('fs'),path=require('path'),cp=require('child_process'),url=require('url');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
  const p=JSON.parse(input),checks=[],pending=new Map();let browser,ws,seq=0;
  const profile=path.join(p.reportDir,'edge-profile');fs.mkdirSync(profile,{recursive:true});
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const call=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++seq,timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout'));},15000);
    pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));
  });
  const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true});if(r.exceptionDetails)throw new Error('Page expression error');return r.result.value;};
  const check=(ok,label)=>{if(!ok)throw new Error(label);checks.push(label);};
  const shot=async name=>fs.writeFileSync(path.join(p.reportDir,name),Buffer.from((await call('Page.captureScreenshot',{captureBeyondViewport:false})).data,'base64'));
  try{
    browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
      '--headless=new','--incognito','--disable-gpu','--no-first-run','--no-default-browser-check',
      '--user-data-dir='+profile,'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','about:blank'
    ],{windowsHide:true,stdio:'ignore'});
    let port;
    for(let i=0;i<150;i++){try{port=Number(fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]);if(port)break;}catch{}await pause(100);}
    if(!port)throw new Error('Owned headless browser did not start');
    const pages=await (await fetch('http://127.0.0.1:'+port+'/json')).json();
    ws=new WebSocket(pages.find(x=>x.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    ws.onmessage=e=>{const data=JSON.parse(e.data),task=pending.get(data.id);if(task){clearTimeout(task.timer);pending.delete(data.id);data.error?task.reject(new Error('CDP rejected')):task.resolve(data.result);}};
    await call('Page.enable');await call('Runtime.enable');
    await call('Emulation.setDeviceMetricsOverride',{width:1365,height:980,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:url.pathToFileURL(p.reviewFile).href});
    for(let i=0;i<80;i++){if(await ev(`document.querySelectorAll('.family').length===20&&document.getElementById('matches')?.textContent.includes('28')`))break;await pause(100);}
    check(await ev(`document.querySelectorAll('.family').length===20&&document.querySelectorAll('.device').length===8`),'20 family specifications and 8 pending worksheets render');
    check(await ev(`document.querySelectorAll('.family li').length===200`),'All 200 source hashes are available in member details');
    check(await ev(`document.body.textContent.includes('LTE原文及元数据均为4G')&&!document.body.textContent.includes('10份LTE适用性冲突')`),'Actual LTE/4G declaration is preserved');
    check(await ev(`[...document.querySelectorAll('.device pre')].every(e=>e.textContent.includes('待提供')&&e.textContent.includes('不提供操作命令或统一阈值'))`),'Every worksheet preserves missing site evidence and action boundaries');
    await ev(`document.getElementById('search').value='LTE';document.getElementById('search').dispatchEvent(new Event('input'))`);
    check(await ev(`document.getElementById('matches').textContent==='匹配 1 / 28 个专题'&&[...document.querySelectorAll('.family:not([hidden])')].length===1`),'Search locates the exact LTE family');
    await ev(`document.querySelector('.family:not([hidden])').open=true`);await shot('support-desktop.png');
    await ev(`document.getElementById('search').value='not-a-real-topic';document.getElementById('search').dispatchEvent(new Event('input'))`);
    check(await ev(`document.getElementById('matches').textContent==='匹配 0 / 28 个专题'`),'No matches has an explicit zero result');
    await ev(`document.getElementById('search').value='BBU';document.getElementById('search').dispatchEvent(new Event('input'));document.querySelector('.device:not([hidden])').open=true`);
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    check(await ev(`document.documentElement.scrollWidth<=390&&document.body.textContent.includes('没有指定BBU厂家来源')`),'390px layout contains the BBU evidence gap without overflow');
    await shot('support-mobile.png');
    const links=await ev(`[...document.querySelectorAll('a')].map(x=>x.href)`);
    check(links.filter(x=>x.startsWith('file:')).every(x=>fs.existsSync(url.fileURLToPath(x))),'All local review artifacts resolve');
    fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:true,checks,screenshots:['support-desktop.png','support-mobile.png'],headless:true,paid_calls:0},null,2));
    console.log(JSON.stringify({passed:true,checks:checks.length,paid_calls:0}));
  }catch(e){fs.writeFileSync(path.join(p.reportDir,'browser-verification.json'),JSON.stringify({passed:false,checks,error:String(e.message)},null,2));process.exitCode=1;
  }finally{if(ws){try{await call('Browser.close');}catch{}ws.close();}if(browser)browser.kill();for(const task of pending.values())clearTimeout(task.timer);}
});
