const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const out=__dirname, profile=path.join(out,'edge-profile');
const port=19686, sleep=ms=>new Promise(r=>setTimeout(r,ms));
const browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--user-data-dir='+profile,'--remote-debugging-port='+port,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
let ws, id=0; const pending=new Map(), errors=[], requests=[], checks=[];
async function call(method,params={}) { return new Promise((resolve,reject)=>{const n=++id;const timer=setTimeout(()=>{pending.delete(n);reject(new Error('Timeout '+method))},15000);pending.set(n,{resolve,reject,timer});ws.send(JSON.stringify({id:n,method,params}));}); }
async function run(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function click(selector){await run(`document.querySelector(${JSON.stringify(selector)}).click()`);await sleep(290);}
async function shot(name){await run(`$('toast').classList.remove('show')`);await sleep(400);const r=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(out,name+'.png'),Buffer.from(r.data,'base64'));}
async function check(name,expr){const value=await run(expr);checks.push({name,pass:!!value});if(!value)throw new Error('Check failed: '+name);}
(async()=>{try{
 let tabs;for(let n=0;n<50;n++){try{tabs=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();if(tabs.length)break}catch{}await sleep(200)}
 ws=new WebSocket(tabs[0].webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 ws.onmessage=e=>{const v=JSON.parse(e.data);if(v.id){const p=pending.get(v.id);if(p){clearTimeout(p.timer);pending.delete(v.id);v.error?p.reject(v.error):p.resolve(v.result)}}else if(v.method==='Runtime.exceptionThrown')errors.push(v.params.exceptionDetails);else if(v.method==='Network.requestWillBeSent')requests.push(v.params.request.url)};
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
 const navigation=await call('Page.navigate',{url:pathToFileURL(path.resolve(out,'../preview.html')).href});await sleep(1200);
 if(!await run(`!!document.querySelector('#content')`)){console.log(JSON.stringify({navigation,location:await run(`location.href`),body:await run(`document.body.innerText.slice(0,1200)`)}));await shot('navigation-error');}
 await check('initial render',`document.querySelector('#content').innerText.includes('工作台')`);await shot('01-dashboard');
 const widths=[];for(const page of ['home','alerts','chat','query','knowledge']){await run(`go('${page}')`);await sleep(230);widths.push(await run(`document.querySelector('.sidebar').getBoundingClientRect().width`));await check(page+' desktop fits',`document.documentElement.scrollWidth<=innerWidth`)}
 checks.push({name:'sidebar stable between all primary pages',pass:new Set(widths).size===1,widths});
 await run(`go('alerts')`);await click('[data-alarm="0"]');await click('[data-action="flownext"]');await shot('02-alert-workflow');
 await click('[data-action="flownext"]');await click('[data-action="flownext"]');await click('[data-action="flownext"]');
 await check('incomplete recovery cannot close ticket',`state.flow===3&&!state.completed`);
 await run(`$('handlingNote').value='复核链路后观察';$('metricValue').value='注册成功率99.8%，KPI记录';$('observeMinutes').value='15';$('verdict').value='fail'`);await click('[data-action="flownext"]');
 await check('failed recovery stays processing',`state.flow===3&&!state.completed`);
 await run(`$('verdict').value='pass'`);await click('[data-action="flownext"]');await check('complete recovery advances',`state.flow===4&&state.completed`);
 await run(`go('chat')`);await shot('03-chat');await click('[data-prompt]');await check('suggestion fills composer',`$('chatInput').value.length>0`);await run(`$('chatForm').requestSubmit()`);await sleep(850);await check('answer visible',`state.chat.length===2&&$('chatMain').innerText.includes('先核对范围')`);await click('[data-action="sources"]');await check('source opens drawer',`$('drawer').classList.contains('open')`);await run(`document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);await check('escape closes drawer',`!$('drawer').classList.contains('open')`);
 await run(`go('query')`);await click('[data-tabvalue="自定义查询"]');await run(`$('queryText').value='近7天未处理紧急告警';$('queryForm').requestSubmit()`);await check('readable plan before execute',`state.queryReady&&!state.queryDone&&$('content').innerText.includes('确认查询条件')`);await shot('04-query');await click('[data-action="execute"]');await check('query results visible',`state.queryDone&&$('content').innerText.includes('2 条')`);
 await run(`go('knowledge')`);await shot('05-knowledge');await click('[data-tabvalue="上传资料"]');await click('[data-action="samplefile"]');await click('[data-action="uploadnext"]');await shot('06-upload-review');await click('[data-action="uploadnext"]');await check('upload ends pending approval',`state.uploadStep===2&&$('content').innerText.includes('等待审核')`);
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 for(const page of ['home','alerts','chat','query','knowledge']){await run(`go('${page}')`);await sleep(240);await check(page+' mobile fits',`document.documentElement.scrollWidth<=innerWidth`)}
 await run(`go('home')`);await shot('07-mobile');
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await check('reduced motion',`parseFloat(getComputedStyle(document.querySelector('#content')).animationDuration)<.01`);
 checks.push({name:'no JavaScript exceptions',pass:errors.length===0},{name:'no outbound HTTP requests',pass:requests.every(u=>!/^https?:/.test(u))});
 const report={checkedAt:new Date().toISOString(),scope:'Offline design prototype only; no production/backend acceptance',checks,errors,requests};fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));if(checks.some(c=>!c.pass))process.exitCode=1;
 }catch(e){console.error(e);process.exitCode=1}finally{if(ws){try{await call('Browser.close')}catch{}ws.close()}await sleep(400);if(browser.exitCode===null)browser.kill();}
})();
