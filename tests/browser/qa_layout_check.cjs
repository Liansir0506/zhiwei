// Targeted visual geometry checks; local message examples, no QA/model requests.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const root=path.resolve(__dirname,'../..'),out=path.join(root,'.runtime/test/reports/qa-layout-20261021');fs.mkdirSync(out,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));let browser,ws,serial=0;const pending=new Map(),checks=[],errors=[];
async function call(method,params={}){return new Promise((resolve,reject)=>{const id=++serial,t=setTimeout(()=>{pending.delete(id);reject(Error(method+' timeout'))},15000);pending.set(id,{resolve,reject,t});ws.send(JSON.stringify({id,method,params}));});}
async function ev(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
async function check(name,expr){checks.push({name,pass:!!await ev(expr)});}
(async()=>{try{
 const profile=path.join(out,'edge-profile-'+Date.now());browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--no-first-run','--disable-gpu','--user-data-dir='+profile,'--remote-debugging-port=19690','about:blank'],{windowsHide:true,stdio:'ignore'});
 let list;for(let n=0;n<70;n++){try{list=await(await fetch('http://127.0.0.1:19690/json/list')).json();if(list.length)break}catch{}await sleep(100)}
 ws=new WebSocket(list.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});ws.onmessage=e=>{const m=JSON.parse(e.data),p=pending.get(m.id);if(p){clearTimeout(p.t);pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result)}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text)};
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});
 const key=fs.readFileSync(path.join(root,'.runtime/original-local/api-key.txt'),'utf8').trim();await call('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('api_key',${JSON.stringify(key)})`});
 await call('Page.navigate',{url:'http://127.0.0.1:8010/#tab=qa'});for(let n=0;n<100;n++){if(await ev(`typeof newAssistant==='function'&&document.body.dataset.tab==='qa'`))break;await sleep(100)}
 await ev(`$('chat').replaceChildren();$('view-qa').classList.add('has-dialog');addUser('今天的告警应该如何排查？',true);const short=newAssistant();short.body.textContent='先核对告警事件、设备与发生时间，再查看同一时间窗口内的指标与近期变更。';addMeta(short.root,{route:'direct_answer'},[],null,false,[],'local-demo-release-r1','ANSWERED');addUser('请展开说明，并列出需要补充的证据。',true);const long=newAssistant();long.body.innerHTML='<h3>建议的核查顺序</h3>'+Array.from({length:12},(_,i)=>'<p>'+String(i+1)+'. 核对设备标识、告警时间和影响范围，结合相关指标与变更记录区分已确认事实和待验证假设。缺少设备型号或版本时，先补充资料，再决定下一步检查。</p>').join('');addMeta(long.root,{route:'retrieval'},[],null,false,[],'local-demo-release-r1','ANSWERED');$('chat').scrollTop=0;`);
 const geometry=[];
 for(const [width,height] of [[1440,960],[1920,1080],[2560,1440],[1280,600],[390,844]]){
  await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<761});await sleep(350);
  const r=await ev(`(()=>{const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,height:r.height}};return {answer:rect(document.querySelector('.msg.assistant')),user:rect(document.querySelector('.msg.user')),input:rect($('f')),chat:rect($('chat')),header:rect(document.querySelector('header.top')),scrollHeight:$('chat').scrollHeight}})()`);geometry.push({width,height,...r});
  checks.push({name:width+' question and answer share input edges',pass:Math.abs(r.answer.left-r.input.left)<1&&Math.abs(r.answer.right-r.input.right)<1&&Math.abs(r.user.right-r.input.right)<1});
  await check(width+' messages keep natural height without overlap',`[...$('chat').children].every((n,i,a)=>i===0||n.getBoundingClientRect().top>=a[i-1].getBoundingClientRect().bottom+10)`);
  await check(width+' composer visible and no page overflow',`$('f').getBoundingClientRect().bottom<=innerHeight&&$('chat').clientHeight>100&&document.documentElement.scrollWidth<=innerWidth`);
  await check(width+' history can scroll locally when content exceeds viewport',`$('chat').scrollHeight>=$('chat').clientHeight&&getComputedStyle($('chat')).overflowY==='auto'&&$('view-qa').scrollHeight<=$('view-qa').clientHeight+1`);
  if([1440,1920,390].includes(width))fs.writeFileSync(path.join(out,width+'.png'),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
 }
 await check('empty composer has no internal scrollbar',`getComputedStyle($('q')).overflowY==='hidden'`);
 await ev(`$('q').value='补充信息。'.repeat(70);$('q').dispatchEvent(new Event('input'));$('burgerR').click()`);await sleep(300);await check('multiline input and evidence drawer preserve reading column',`$('f').getBoundingClientRect().bottom<=innerHeight&&document.documentElement.scrollWidth<=innerWidth&&getComputedStyle($('q')).overflowY==='auto'`);await ev(`Workspace.closeDrawer()`);
 checks.push({name:'no JavaScript exceptions',pass:errors.length===0});fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({scope:'Actual live static page with local representative short/long message DOM. No model requests or server-side conversation changes.',checks,geometry,errors},null,2));console.log(JSON.stringify({passed:checks.filter(c=>c.pass).length,failed:checks.filter(c=>!c.pass),errors,out},null,2));if(checks.some(c=>!c.pass))process.exitCode=1;
 }catch(e){console.error(e);process.exitCode=1}finally{if(ws){try{await call('Browser.close')}catch{}ws.close()}await sleep(150);if(browser?.exitCode===null)browser.kill();}
})();
