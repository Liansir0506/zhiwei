// Append to the sealed guide viewer; use the same authenticated readonly API.
let supportRequest=0;
const checkNames={approved_source_roster:'准确准入来源清单',guide_ledger:'指南入库账本',source_bytes:'原件字节与校验和',source_access_contract:'准入访问范围',guide_only_vector_counts:'指南片段与集合数量',exact_admitted_snapshot:'完整向量与权限快照',staged_without_active:'仅候选、未激活',live_source_identity_and_access:'实时来源身份与权限',alarm_recall:'告警工单召回',sop_integrity:'SOP结构完整性',meta_completeness:'元数据完整性',duplicates:'工单重复检查',desensitize:'敏感信息检查'};
const baseTable=table;
table=function(rows,ordinary=false){return baseTable(rows.map(r=>({...r,check:checkNames[r.check]||r.check})),ordinary);};
function filterSupport(){const query=$("support-filter").value.trim().toLocaleLowerCase();let count=0;document.querySelectorAll('.support-entry').forEach(card=>{card.hidden=!card.dataset.search.includes(query);if(!card.hidden)count++;});$("support-count").textContent=`匹配 ${count} / 20 个专题`;} 
async function loadSupport(){
 const requestKey=key,request=++supportRequest;$("support-count").textContent="正在核对原件与指南对应关系…";
 try{const data=await api('/preview/checks');if(key!==requestKey||request!==supportRequest)return;
  const matrix=data.support_alignment,summary=matrix.summary;
  $("support-summary").replaceChildren(node('p',`${summary.guide_families} 个文字家族 · ${summary.parent_files} 份原件绑定 · ${summary.step_alignment_rows} 项逐步对齐 · ${summary.record_contracts} 组取证项`),node('p','20个家族各重复10份编号；现场证据已验证0、动作批准0、独立质量分母0。'));
  $("support-entries").replaceChildren();
  for(const entry of matrix.entries){
   const card=node('details','','support-entry');card.dataset.search=[entry.title,entry.equipment,entry.alarm,entry.hypothesis,entry.network_tech].join(' ').toLocaleLowerCase();
   card.append(node('summary',`${entry.title} · 完整现场SOP未通过`),node('p',`适用声明：${entry.network_tech}；实际对象、部署和版本仍须另核对。原模板原因只作为待验证假设：${entry.hypothesis}`));
   const fields=node('ul','');entry.records.forEach(r=>fields.append(node('li',r.description+'；现场原始记录待提供。')));card.append(node('h3','具体取证项'),fields);
   for(const [label,proofs] of [['支持记录分支',entry.primary_conditions],['相反记录分支',entry.counter_conditions]]){
    const list=node('ul','');proofs.forEach(p=>list.append(node('li',p.quote.replace(/^- /,''))));card.append(node('h3',label+'（仅供复核）'),list);
   }
   const list=node('div','','support-steps');
   entry.steps.forEach(row=>{const item=node('article','','support-step');item.append(node('h4',`原第${row.old_step}步 · ${row.old_label}`),node('p',row.status_label));
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
