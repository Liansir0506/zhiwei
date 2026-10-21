"use strict";
const version="test-sop-records-r2", storage="sop-record-preview-key";
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
// Append to the sealed guide viewer; use the same authenticated readonly API.
let supportRequest=0;
const checkNames={approved_source_roster:'准确准入来源清单',guide_ledger:'指南入库账本',source_bytes:'原件字节与校验和',source_access_contract:'准入访问范围',guide_only_vector_counts:'指南片段与集合数量',exact_admitted_snapshot:'完整向量与权限快照',staged_without_active:'仅候选、未激活',live_source_identity_and_access:'实时来源身份与权限',alarm_recall:'告警工单召回',sop_integrity:'SOP结构完整性',meta_completeness:'元数据完整性',duplicates:'工单重复检查',desensitize:'敏感信息检查',revision_record_alignment:'修订取证项与原文对应'};
const baseTable=table;
table=function(rows,ordinary=false){return baseTable(rows.map(r=>({...r,check:checkNames[r.check]||r.check})),ordinary);};
function filterSupport(){const query=$("support-filter").value.trim().toLocaleLowerCase();let count=0;document.querySelectorAll('.support-entry').forEach(card=>{card.hidden=!card.dataset.search.includes(query);if(!card.hidden)count++;});$("support-count").textContent=`匹配 ${count} / 20 个专题`;} 
async function loadSupport(){
 const requestKey=key,request=++supportRequest;$("support-count").textContent="正在核对原件与指南对应关系…";
 try{const data=await api('/preview/checks');if(key!==requestKey||request!==supportRequest)return;
  const matrix=data.support_alignment,summary=matrix.summary;
  $("support-summary").replaceChildren(node('p',`${summary.guide_families} 个文字家族 · ${summary.parent_files} 份原件绑定 · ${summary.step_alignment_rows} 项逐步对齐 · ${summary.record_contracts} 组取证项`),node('p',`原60组取证要求新增${summary.record_extensions}组：11项变更、9项依赖、10项配置、19项派生告警。文字缺口已补；实际记录待提供。`),node('p','现场证据已验证0、动作批准0、独立质量分母0。'));
  $("support-entries").replaceChildren();
  for(const entry of matrix.entries){
   const card=node('details','','support-entry');card.dataset.search=[entry.title,entry.equipment,entry.alarm,entry.hypothesis,entry.network_tech].join(' ').toLocaleLowerCase();
   card.append(node('summary',`${entry.title} · 完整现场SOP未通过`),node('p',`适用声明：${entry.network_tech}；实际对象、部署和版本仍须另核对。原模板原因只作为待验证假设：${entry.hypothesis}`));
   const fields=node('ul','');entry.records.forEach(r=>{const item=node('li','');item.dataset.recordId=r.id;item.append(node('p',(r.kind==='revision_extension'?'本次新增 · ':'')+r.description));const b=node('button','核对本项原文');b.addEventListener('click',()=>source(r.proof.evidence_id));item.append(b);fields.append(item);});card.append(node('h3','具体取证项'),fields);
   for(const [label,proofs] of [['支持记录分支',entry.primary_conditions],['相反记录分支',entry.counter_conditions]]){
    const list=node('ul','');proofs.forEach(p=>list.append(node('li',p.quote.replace(/^- /,''))));card.append(node('h3',label+'（仅供复核）'),list);
   }
   const list=node('div','','support-steps');
   entry.steps.forEach(row=>{const item=node('article','','support-step');item.append(node('h4',`原第${row.old_step}步 · ${row.old_label}`),node('p',row.status_label),node('p',row.text_changed?'本次已补齐文字要求；实际证据待提供。':'文字要求沿用；具体适用性仍需复核。'));
    row.guide_proofs.forEach(proof=>{const b=node('button',`核对指南原文 · 第${proof.line}行`);b.addEventListener('click',()=>source(proof.evidence_id));item.append(b);});list.append(item);});
   card.append(node('h3','原10步逐项对齐'),list);
   const gaps=node('ul','');entry.unresolved_requirements.forEach(g=>gaps.append(node('li',g.label+'：待提供。')));card.append(node('h3','现场执行仍缺'),gaps);
   const parents=node('details','');parents.append(node('summary','10份旧原件身份与位置（不迁移正文）'));
   entry.parent_references.forEach(p=>parents.append(node('p',`${p.filename} · ${p.asset_id} · SHA ${p.file_sha256} · 第1步行${p.step_lines['1']}`)));card.append(parents);$("support-entries").append(card);
  }
  filterSupport();
 }catch(e){if(key!==requestKey||request!==supportRequest)return;$("support-entries").replaceChildren();$("support-summary").replaceChildren();$("support-count").textContent=e.message;}
}
document.querySelector('[data-tab=support]').addEventListener('click',loadSupport);
$("support-filter").addEventListener('input',filterSupport);
$("logout").addEventListener('click',()=>{supportRequest++;$("support-summary").replaceChildren();$("support-entries").replaceChildren();$("support-count").textContent='';$("support-filter").value='';});
