'use strict';
(()=>{
 const tab=document.getElementById('trainingTab'),view=document.getElementById('view-training'),status=document.getElementById('trainingStatus'),host=document.getElementById('trainingFrameHost');
 let epoch=0,controller=null,verifiedKey='',checkingKey='',permitted=false;
 function clear(){epoch++;controller?.abort();controller=null;host.replaceChildren();status.textContent='';document.getElementById('trainingLaunch').disabled=false;}
 function restrict(){clear();verifiedKey='';checkingKey='';permitted=false;tab.hidden=true;view.hidden=true;}
 async function sync(){
  const key=apiKey();
  if(!allowed||document.getElementById('businessTab').hidden){restrict();return;}
  if(key===verifiedKey||key===checkingKey)return;
  restrict();checkingKey=key;const token=epoch;
  try{
   const response=await fetch('/training/capabilities',{headers:authHeaders(),cache:'no-store'});
   const data=response.ok?await response.json():null;
   if(token!==epoch||apiKey()!==key||!allowed)return;
   permitted=data?.can_train_amf===true;verifiedKey=key;checkingKey='';tab.hidden=!permitted;
  }catch{if(token===epoch){checkingKey='';tab.hidden=true;}}
 }
 const previous=window.switchTab;
 window.switchTab=function(name){clear();view.hidden=true;previous(name);if(name==='training'&&permitted&&verifiedKey===apiKey()&&allowed)view.hidden=false;};
 tab.onclick=()=>window.switchTab('training');
 document.getElementById('trainingBack').onclick=()=>{window.switchTab('business');document.getElementById('businessTab').focus();};
 document.getElementById('trainingLaunch').onclick=async()=>{
  clear();if(!permitted||verifiedKey!==apiKey()||!allowed)return;
  const key=apiKey(),token=epoch;controller=new AbortController();status.textContent='正在准备演练…';document.getElementById('trainingLaunch').disabled=true;
  try{
   const response=await fetch('/training/amf',{headers:authHeaders(),cache:'no-store',signal:controller.signal});
   if(!response.ok)throw Error(response.status===401?'登录已失效，请重新设置凭据。':response.status===403?'当前凭据不能进入此训练模块。':'演练暂不可用，请重试。');
   const text=await response.text();if(token!==epoch||apiKey()!==key||view.hidden)return;
   const bar=document.createElement('div');bar.className='training-session-bar';const title=document.createElement('strong');title.textContent='AMF流程演练';const back=document.createElement('button');back.id='trainingClose';back.type='button';back.textContent='返回业务列表';back.onclick=()=>window.switchTab('business');bar.append(title,back);
   const frame=document.createElement('iframe');frame.title='AMF注册失败流程演练';frame.setAttribute('sandbox','allow-scripts allow-downloads');frame.referrerPolicy='no-referrer';frame.addEventListener('load',()=>{if(token===epoch&&host.contains(frame))host.scrollIntoView({block:'start',behavior:'smooth'});},{once:true});frame.srcdoc=text;host.replaceChildren(bar,frame);
   status.textContent='演练已打开。离开页签将清空本次进度。';
  }catch(error){if(token===epoch&&error.name!=='AbortError')status.textContent=error.message;}
  finally{if(token===epoch)document.getElementById('trainingLaunch').disabled=false;}
 };
 document.getElementById('isolatedKeyForm').addEventListener('submit',restrict,true);
 document.getElementById('isolatedRefresh').addEventListener('click',restrict,true);
 window.addEventListener('storage',e=>{if(e.key==='readonly_key'||e.key===null)restrict();});
 const observer=new MutationObserver(sync);observer.observe(document.getElementById('businessTab'),{attributes:true,attributeFilter:['hidden']});observer.observe(document.getElementById('isolatedStatus'),{childList:true});sync();
})();
