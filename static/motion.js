/* Motion is presentation only: it never submits, retries or approves an operation. */
window.UIMotion = (() => {
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),detailsRuns=new Map(),busyRuns=new WeakMap();
  const easing='cubic-bezier(.2,.7,.2,1)';
  function busy(node){
    if(!node)return ()=>{};
    const token={};busyRuns.set(node,token);node.setAttribute('aria-busy','true');
    return ()=>{if(busyRuns.get(node)===token)clearBusy(node);};
  }
  function clearBusy(node){if(node){busyRuns.delete(node);node.removeAttribute('aria-busy');}}
  function settle(details,run){
    if(detailsRuns.get(details)!==run)return;
    if(run.open&&!details.open)run.open=false;
    detailsRuns.delete(details);details.open=run.open;run.animation.cancel();
    if(!run.open&&details.contains(document.activeElement))details.querySelector('summary')?.focus();
  }
  document.addEventListener('click',event=>{
    if(!(event.target instanceof Element))return;
    const summary=event.target.closest('summary'),details=summary?.parentElement;
    if(!details||details.tagName!=='DETAILS'||event.defaultPrevented||event.button>0||reduced.matches||!details.closest('#mainContent,dialog'))return;
    if(event.target.closest('button,a,input,select,textarea'))return;
    event.preventDefault();
    const old=detailsRuns.get(details),open=!(old?old.open:details.open),start=details.getBoundingClientRect().height;
    old?.animation.cancel();detailsRuns.delete(details);
    details.open=true;
    const end=open?details.getBoundingClientRect().height:summary.getBoundingClientRect().height+parseFloat(getComputedStyle(details).paddingTop)+parseFloat(getComputedStyle(details).paddingBottom)+parseFloat(getComputedStyle(details).borderTopWidth)+parseFloat(getComputedStyle(details).borderBottomWidth);
    if(Math.abs(start-end)<1){details.open=open;return;}
    const animation=details.animate([{height:start+'px',overflow:'hidden'},{height:end+'px',overflow:'hidden'}],{duration:180,easing});
    const run={animation,open};detailsRuns.set(details,run);animation.onfinish=()=>settle(details,run);
    animation.oncancel=()=>{if(detailsRuns.get(details)===run)detailsRuns.delete(details);};
  });
  reduced.addEventListener('change',()=>{if(reduced.matches)for(const [details,run] of detailsRuns)settle(details,run);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)for(const [details,run] of detailsRuns)settle(details,run);});
  return {busy,clearBusy,reduced:()=>reduced.matches};
})();
