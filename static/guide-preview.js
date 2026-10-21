"use strict";
const version="test-sop-evidence-r1", storage="sop-guide-preview-key";
const $=id=>document.getElementById(id);
let key=sessionStorage.getItem(storage)||"", busy=false;
const params=new URLSearchParams(location.hash.slice(1));
if(params.has("preview_key")){key=params.get("preview_key")||"";history.replaceState(null,"",location.pathname);sessionStorage.setItem(storage,key);}
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,options={}){
 const response=await fetch(path,{...options,cache:"no-store",headers:{"X-API-Key":key,...options.headers}});
 const data=await response.json();
 if(!response.ok){if(response.status===401||response.status===403){$("workspace").hidden=true;$("login").hidden=false;}throw new Error(data.detail||"请求未完成，请重试");}return data;
}
async function source(id){
 const requestKey=key;
 try{const data=await api("/kb/evidence/"+encodeURIComponent(id));if(key!==requestKey)return;$("source-meta").textContent=`${data.source} · ${data.source_revision} · ${data.kb_version}`;$("source-text").textContent=data.text;$("source-panel").hidden=false;$("source-panel").scrollIntoView({behavior:"smooth"});$("status").textContent="已重读原文与引用位置。";}catch(e){if(key!==requestKey)return;$("source-panel").hidden=true;$("source-text").textContent="";$("status").textContent=e.message;}
}
function table(rows,ordinary=false){
 const t=node("table","");const head=node("tr","");for(const label of ["检查项","结果",ordinary?"实际分母 / 说明":"检查分母"])head.append(node("th",label));t.append(head);
 for(const row of rows){const tr=node("tr","");tr.append(node("td",row.check),node("td",row.passed?"通过":"未通过"),node("td",ordinary?(row.reason||String(row.sop_count??row.scanned??row.chunks??row.sampled??"无适用样本")):String(row.denominator)));t.append(tr);}return t;
}
async function enter(){
 const requestKey=key;
 $("status").textContent="正在核对候选与依赖…";
 try{
  const checks=await api("/preview/checks");const catalog=await api(`/kb/catalog?kb_version=${version}&kind=document&doc_type=guide&page_size=30`);
  if(key!==requestKey)return;
  $("check-results").replaceChildren(node("p",`指南读取完整性：${checks.read_preview_ready?"通过":"未通过"}；核验时间 ${checks.checked_at_utc}`),table(checks.checks));
  $("gate-results").replaceChildren(node("p","普通发布门禁未通过。指南读取检查不授予发布资格。"),table(checks.ordinary_publication_gate.checks,true));
  $("gaps").replaceChildren(...checks.gaps.map(g=>node("li",g)));
  $("catalog-items").replaceChildren();$("count").textContent=`(${catalog.total})`;
  for(const item of catalog.items){
   const card=node("article","","card");card.append(node("h3",checks.source_titles[item.source]||item.title),node("p",`${item.source} · ${item.source_revision} · ${item.visible_matching_chunks} 个片段`));
   const fragments=node("div","","fragments");item.fragments.forEach((fragment,i)=>{const b=node("button",`片段 ${i+1}`);b.addEventListener("click",()=>source(fragment.evidence_id));fragments.append(b);});card.append(fragments);$("catalog-items").append(card);
  }
  sessionStorage.setItem(storage,key);$("key").value="";$("login").hidden=true;$("workspace").hidden=false;$("logout").hidden=false;$("status").textContent="20份指南已通过当前快照校验，可查看目录和检索证据。";
 }catch(e){if(key!==requestKey)return;$("workspace").hidden=true;$("status").textContent=e.message;}
}
$("login-form").addEventListener("submit",e=>{e.preventDefault();key=$("key").value.trim();enter();});
$("logout").addEventListener("click",()=>{key="";sessionStorage.removeItem(storage);$("workspace").hidden=true;$("login").hidden=false;$("logout").hidden=true;$("catalog-items").replaceChildren();$("search-results").replaceChildren();$("source-text").textContent="";$("source-panel").hidden=true;$("status").textContent="已退出预览。";});
document.querySelectorAll("[data-tab]").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll("[data-tab]").forEach(tab=>{const selected=tab===b;tab.setAttribute("aria-selected",String(selected));$(tab.dataset.tab).hidden=!selected;});}));
$("search-form").addEventListener("submit",async e=>{
 e.preventDefault();if(busy)return;const query=$("query").value.trim();if(!query)return;
 const requestKey=key;
 busy=true;$("search-button").disabled=true;$("search-results").replaceChildren();$("source-panel").hidden=true;$("source-text").textContent="";$("status").textContent="正在检索并校验原文，首次模型加载需要稍等…";
 try{const data=await api("/kb/search-evidence",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query,kb_version:version,top_n:5})});
  if(key!==requestKey)return;
  if(!data.evidence.length){$("search-results").append(node("p","没有找到可支持该问题的证据。当前指南不能提供完整处置流程。"));}
  for(const evidence of data.evidence){const card=node("article","","card");card.append(node("h3",evidence.source),node("pre",evidence.text));const b=node("button","重读原文引用");b.addEventListener("click",()=>source(evidence.evidence_id));card.append(b);$("search-results").append(card);}
  $("status").textContent=`检索完成：${data.evidence.length} 个可核对原文片段。`;
 }catch(error){if(key===requestKey)$("status").textContent=error.message;}finally{busy=false;$("search-button").disabled=false;}
});
if(key)enter();
