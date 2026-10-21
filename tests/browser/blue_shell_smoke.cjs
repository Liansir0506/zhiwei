/* Real Edge layout checks against the actual static application. API fixtures are
   local to this test, never connected to a database, model or production service. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),cp=require('node:child_process');
const motion=process.argv.includes('--motion'),info=process.argv.includes('--info')||motion,core=process.argv.includes('--core')||info,fixture=info?require('./blue_info_fixture.cjs'):core?require('./blue_core_fixture.cjs'):null;
const root=path.resolve(__dirname,'../..'),out=path.join(root,'.runtime/test/reports/'+(motion?'ui-blue-p3-20261021':info?'ui-blue-p2-20261021':core?'ui-blue-p1-20261021':'ui-blue-p0-20261021'));
fs.mkdirSync(out,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let role='ops',browser,ws,serial=0,base;
const pending=new Map(),errors=[],checks=[],missing=[],mutations=[];
const alarm={alarm_id:'UI-ALM-001',alarm_code:'ALM-AMF-REG',device_id:'AMF-02',device_name:'AMF-02',device_type:'AMF',severity:'critical',severity_label:'紧急',status:'open',status_label:'未处理',occurred_at:'2026-10-21T10:20:00',title:'AMF 注册成功率下降'};
const server=http.createServer((req,res)=>{
 const u=new URL(req.url,'http://localhost'),send=(code,data)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
 if(fixture?.handle(req,res,u))return;
 if(req.method!=='GET'){mutations.push(u.pathname);send(405,{detail:'UI fixture is read-only'});return;}
 if(u.pathname==='/'||u.pathname.startsWith('/static/')){
  const file=path.resolve(root,u.pathname==='/'?'static/index.html':'.'+decodeURIComponent(u.pathname));
  if(!file.startsWith(path.join(root,'static')+path.sep)){res.writeHead(404);res.end();return;}
  if(!fs.existsSync(file)){missing.push(u.pathname);res.writeHead(404);res.end();return;}
  res.writeHead(200,{'Content-Type':({'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml'})[path.extname(file)]+'; charset=utf-8','Cache-Control':'no-store'});res.end(fs.readFileSync(file));return;
 }
 if(u.pathname==='/health'){send(200,{status:'degraded',components:{fixture:'UI acceptance only'},environment:'test',generation_enabled:false,agent_enabled:false});return;}
 if(u.pathname==='/admin/capabilities'){send(200,{roles:[role],tenant_id:'default',can_manage_keys:role==='admin',can_read_ops:true,can_read_eval:role==='admin',can_read_permissions:role==='admin',can_read_business:true});return;}
 if(u.pathname==='/kb/versions'){send(200,{active_version:'ui-fixture',versions:[],can_preview_staged:false});return;}
 if(u.pathname==='/kb/recommendations'){send(200,{items:[],kb_version:'ui-fixture'});return;}
 if(u.pathname==='/dashboard/summary'){send(200,{overview:{alarms:{open:1,critical_open:1,total:1},devices:{total:250,online:248,online_rate:99.2},qa:{total:0},knowledge:{assets:0,locations:0,versions:{active:1}}},device_types:['AMF'],alarm_trend:{labels:['10.19','10.20','10.21'],values:[1,2,1]},generated_at:'2026-10-21T10:30:00',demo_notice:'仅用于界面布局验收的接口样例；未连接实际业务服务。'});return;}
 if(u.pathname==='/alerts'){send(200,{items:[alarm],page:1,pages:1,total:1,page_size:20,summary:{total:1,active:1,critical:1,window:1,cleared:0},device_types:['AMF'],scope:'UI fixture',generated_at:'2026-10-21T10:30:00'});return;}
 send(503,{detail:'依赖未启动：此测试只验收界面及受限/失败状态'});
});
async function call(method,params={}){return new Promise((resolve,reject)=>{const id=++serial,timer=setTimeout(()=>{pending.delete(id);reject(new Error('timeout '+method))},15000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});}
async function ev(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
async function check(name,expression){const value=await ev(expression);checks.push({name,pass:!!value});if(!value)console.log('FAIL',name);}
async function viewport(width,height=900){await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<761});await sleep(200);}
async function tab(name){await ev(`switchTab('${name}')`);await sleep(300);}
async function shot(name){await sleep(350);fs.writeFileSync(path.join(out,name+'.png'),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));}
const rect=`(()=>{const r=n=>{const b=document.querySelector(n).getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height}};return {sidebar:r('aside.workspace-sidebar'),brand:r('.brand-lockup'),top:r('header.top'),search:r('#globalSearchForm'),main:r('#mainContent')}})()`;
(async()=>{try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));base='http://127.0.0.1:'+server.address().port;
 const port=19688,profile=path.join(out,'edge-profile-'+Date.now());
 browser=cp.spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--user-data-dir='+profile,'--remote-debugging-port='+port,'--remote-debugging-address=127.0.0.1','about:blank'],{windowsHide:true,stdio:'ignore'});
 let targets;for(let i=0;i<80;i++){try{targets=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();if(targets.length)break}catch{}await sleep(150)}
 ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 ws.onmessage=e=>{const m=JSON.parse(e.data),p=pending.get(m.id);if(p){clearTimeout(p.timer);pending.delete(m.id);m.error?p.reject(new Error(m.error.message)):p.resolve(m.result)}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text)};
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});
 await call('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('api_key','ui-fixture-no-real-access')`});
 await viewport(1440);await call('Page.navigate',{url:base});await sleep(1800);
 await check('all styles in head; no inline stylesheet or layout patch',`!document.querySelector('style')&&!document.querySelector('body link[rel=stylesheet]')&&!document.querySelector('link[href*="layout-fix"]')`);
 await check('brand and SVG navigation present',`document.querySelectorAll('.nav-icon svg use').length===11`);
 await check('ops retains operations/integrations; evaluation and credentials stay hidden',`['eval','perm'].every(t=>document.querySelector('.nav-item[data-tab="'+t+'"]').hidden)&&['ops','business'].every(t=>!document.querySelector('.nav-item[data-tab="'+t+'"]').hidden)`);
 const records={};
 for(const width of [1280,1440,1920]){
  await viewport(width);const baseline=await ev(rect);records[width]={};
  for(const name of ['dashboard','alerts','agent','qa','query','kb','docs','gk','versions','ops','eval','perm','business','lab']){
   await tab(name);const r=await ev(rect);records[width][name]=r;
   checks.push({name:`${width} ${name}: shell geometry stable`,pass:Math.abs(r.sidebar.w-baseline.sidebar.w)<.5&&r.brand.h===88&&r.top.h===68&&r.top.x===baseline.top.x&&r.top.w===baseline.top.w&&r.search.x===baseline.search.x});
   await check(`${width} ${name}: active page visible`,`document.querySelector('.tabview.active').getBoundingClientRect().height>100`);
   await check(`${width} ${name}: no viewport/header overflow`,`(()=>{const h=document.querySelector('header.top');return document.documentElement.scrollWidth<=innerWidth&&h.scrollWidth<=h.clientWidth+1})()`);
  }
 }
 await viewport(1440);await tab('dashboard');await shot('01-dashboard');await tab('alerts');await check('alarm list uses live application table renderer',`document.querySelectorAll('#alertsTable tbody tr').length===1`);await shot('02-alerts');
 await tab('query');await shot('03-query');await tab('qa');await shot('04-qa');
 await ev(`$('burgerR').click()`);const opened=await ev(rect);await check('evidence panel leaves global topbar width intact',`document.querySelector('header.top').getBoundingClientRect().width===${opened.top.w}`);await shot('05-evidence');
 await tab('query');await ev(`const marker=document.createElement('div');marker.id='layoutScrollProbe';marker.style.height='2000px';$('view-query').append(marker);$('view-query').scrollTop=420`);await tab('qa');await check('previous page scroll captured before navigation',`window.__tabScroll.query===420`);await tab('query');await check('previous page scroll restored',`$('view-query').scrollTop===420`);await ev(`$('layoutScrollProbe').remove()`);
 await ev(`$('burger').click()`);await sleep(240);await check('explicit collapse removes sidebar from keyboard order',`document.querySelector('aside.workspace-sidebar').inert&&$('burger').getAttribute('aria-expanded')==='false'`);await ev(`$('burger').click()`);await sleep(240);
 await ev(`$('sidebarResizer').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))`);await check('keyboard resize retained',`Math.round(document.querySelector('aside.workspace-sidebar').getBoundingClientRect().width)===224`);await tab('qa');await check('custom width persists across pages',`Math.round(document.querySelector('aside.workspace-sidebar').getBoundingClientRect().width)===224`);await call('Page.reload');await sleep(800);await check('custom width restored on reload',`Math.round(document.querySelector('aside.workspace-sidebar').getBoundingClientRect().width)===224`);await ev(`$('sidebarResizer').dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}))`);
 await ev(`$('keyBtn').click()`);await check('credential dialog remains operable',`$('keyDlg').open&&$('keyInput').getBoundingClientRect().width>100`);await shot('06-dialog');await ev(`$('keyDlg').close()`);
 await tab('query');await ev(`$('queryBody').insertAdjacentHTML('beforeend','<div id="componentProbe" class="surface"><button class="ui-btn">次操作</button><button class="ui-btn primary">主操作</button><input class="ui-input" value="控件示例"><select class="ui-select"><option>选择范围</option></select><table class="data-table"><tbody><tr><td>内容</td></tr></tbody></table></div>')`);
 await check('shared control height 40px',`[...$('componentProbe').querySelectorAll('button,input,select')].every(n=>Math.abs(n.getBoundingClientRect().height-40)<1)`);
 await check('shared title/card/brand tokens',`getComputedStyle(document.querySelector('#view-query h1')).fontSize==='26px'&&getComputedStyle($('componentProbe')).borderRadius==='14px'&&getComputedStyle(document.documentElement).getPropertyValue('--brand').includes('--color-brand-500')===false`);await ev(`$('componentProbe').remove()`);
 role='admin';fixture?.setRole?.(role);await ev(`GovernanceWorkspace.credentialsChanged()`);await sleep(450);await check('admin retains real governance navigation',`['ops','eval','perm','business'].every(t=>!document.querySelector('.nav-item[data-tab="'+t+'"]').hidden)`);
 await viewport(1280,600);await tab('qa');await check('short viewport keeps fixed brand and footer visible',`document.querySelector('.brand-lockup').getBoundingClientRect().height===88&&document.querySelector('.side-foot').getBoundingClientRect().bottom<=600`);await shot('07-short-viewport');
 await viewport(1024,768);await tab('qa');await ev(`$('burgerR').click()`);await sleep(260);await check('narrow desktop evidence opens drawer',`document.querySelector('#view-qa aside.right').getBoundingClientRect().width>300&&getComputedStyle(document.querySelector('#view-qa aside.right')).position==='fixed'`);await ev(`Workspace.closeDrawer()`);
 await viewport(390,844);
 for(const name of ['dashboard','alerts','agent','qa','query','kb','docs','gk','versions','ops','eval','perm','business','lab']){await tab(name);await check('390 '+name+' fits viewport',`document.documentElement.scrollWidth<=innerWidth&&document.querySelector('header.top').scrollWidth<=document.querySelector('header.top').clientWidth+1`)}
 await tab('qa');await shot('08-mobile');await ev(`$('burger').click()`);await sleep(230);await check('mobile navigation opens and scrolls locally',`document.body.classList.contains('mobile-menu')&&getComputedStyle(document.querySelector('.side-nav')).overflowY==='auto'`);await shot('09-mobile-navigation');await ev(`document.querySelector('.nav-item[data-tab="query"]').click()`);await check('navigation closes mobile drawer and updates title',`!document.body.classList.contains('mobile-menu')&&document.body.dataset.tab==='query'`);
 await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await check('reduced motion honored',`parseFloat(getComputedStyle(document.querySelector('.tabview.active')).animationDuration)<.01`);
 if(core)await require('./blue_core_checks.cjs')({ev,check,tab,shot,viewport,sleep,state:fixture.state});
 if(info)await require('./blue_info_checks.cjs')({ev,check,tab,shot,viewport,sleep,state:fixture.state,setRole:async value=>{role=value;fixture.setRole(value);await ev(`GovernanceWorkspace.credentialsChanged();KnowledgeWorkspace.credentialsChanged();VersionWorkspace.credentialsChanged()`);await sleep(300);}});
 if(motion)await require('./blue_motion_checks.cjs')({ev,check,tab,shot,viewport,sleep,call,state:fixture.state});
 checks.push({name:'no browser JavaScript exceptions',pass:errors.length===0},{name:'no missing static assets',pass:missing.length===0},{name:'no unexpected business mutations during UI checks',pass:mutations.length===0});
 fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({time:new Date().toISOString(),scope:core?'Actual static application in Edge with isolated stateful in-memory API fixtures; no actual DB/device/model. Not backend or answer-quality acceptance.':'Actual static application in Edge with read-only API fixtures. Not a backend/answer-quality acceptance.',checks,errors,missing,mutations,geometry:records},null,2));
 console.log(JSON.stringify({passed:checks.filter(x=>x.pass).length,failed:checks.filter(x=>!x.pass),errors,missing,mutations,out},null,2));if(checks.some(c=>!c.pass))process.exitCode=1;
 }catch(e){console.error(e);process.exitCode=1;try{await shot('failure');fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:String(e),errors,checks,state:fixture?.state,visible:await ev(`document.querySelector('.tabview.active')?.innerText.slice(0,6000)`)},null,2));}catch{}}finally{if(ws){try{await call('Browser.close')}catch{}ws.close()}await sleep(250);if(browser?.exitCode===null)browser.kill();server.closeAllConnections();server.close();}
})();
