'use strict';
const E=AmfExercise,$=id=>document.getElementById(id);
const labels={new:'待采集',collected:'待判断分支',awaiting_confirmation:'待确认本次动作',confirmed:'已确认练习动作',observing:'恢复观察中',closed_training:'演练完成',alternative:'转下游分支复核',stopped:'已停止',rolled_back_escalated:'配置已回退 · 待复核',escalated_unrestored:'回退未确认 · 已升级'};
const reasons={target_or_schema_mismatch:'对象或模型版本不匹配',missing_evidence:'取证记录不完整',incomparable_evidence:'记录对象、版本或单位不可比',different_observation_windows:'记录不在同一训练窗',conflicting_evidence:'支持与相反记录同时存在',downstream_evidence:'记录指向下游鉴权，停止入口处置分支',stale_or_changed_plan:'计划已变化，需要重新核对',stale_or_missing_confirmation:'确认已过期或与当前计划不一致',apply_interrupted:'应用中断',recurrence:'观察期间复发',operator_stop:'主动停止',operator_stop_before_change:'变更前已停止',rollback_binding_unavailable:'备份缺失或不匹配，未继续写入'};
const eventLabels={created:'建立练习',evidence_collected:'采集同窗记录',plan_prepared:'形成具体计划',exercise_confirmed:'确认本页演练动作',local_model_changed:'修改内存模型',observation_started:'开始恢复检查',observed:'记录观察窗',training_completed:'模型条件满足',alternative_branch:'交接下游分支',stopped:'停止',rollback_verified:'核对回退配置',rollback_failed:'回退失败升级',rollback_blocked:'回退绑定不足，停止写入'};
let session;
function node(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
function pairs(target,items){const list=node('dl','');for(const [k,v] of items){list.append(node('dt',k),node('dd',String(v)));}target.replaceChildren(list);}
function render(){
 $('phase').textContent=labels[session.phase]||session.phase;$('reason').textContent=reasons[session.reason]||session.reason;
 $('target').textContent=session.model.target_id+' / '+session.model.schema_version;$('revision').textContent='配置修订 '+session.model.revision+' · 入口预算 '+session.model.ingress_budget;
 for(const [id,phase] of [['collect','new'],['review','collected'],['confirm','awaiting_confirmation'],['execute','confirmed'],['observe','observing']])$(id).disabled=session.phase!==phase;
 $('stop').disabled=['closed_training','rolled_back_escalated','escalated_unrestored','stopped','alternative'].includes(session.phase);
 const row=session.records?.registration_window;
 if(row)pairs($('evidence'),[['训练窗',row.window],['当前窗请求',row.incoming],['当前窗完成',row.current_completed],['历史排队完成',row.prior_completed],['队列余量',row.queue],['下游鉴权错误',session.records.downstream_auth?.auth_errors??'记录缺失']]);
 else $('evidence').textContent='采集后查看三个同窗记录。';
 if(session.plan)pairs($('plan'),[['对象',session.plan.target_id],['绑定修订',session.plan.from_revision],['入口预算',session.plan.before_budget+' → '+session.plan.to_budget],['回退配置',session.backup?session.backup.ingress_budget:'执行前自动保存']]);
 else $('plan').textContent='只有入口排队分支满足时，才会形成演练计划。';
 const table=node('table','');const head=node('tr','');for(const name of ['训练窗','当前完成/请求','历史完成','队列'])head.append(node('th',name));table.append(head);
 session.observations.forEach(r=>{const tr=node('tr','');for(const value of [r.window,r.current_completed+'/'+r.incoming,r.prior_completed,r.queue])tr.append(node('td',String(value)));table.append(tr);});$('observations').replaceChildren(table);
 $('audit-count').textContent='('+session.audit.length+')';$('audit').replaceChildren(...session.audit.map(r=>node('li',r.sequence+'. '+(eventLabels[r.type]||r.type)+' · 配置修订 '+r.config_revision)));
 const outcomes={closed_training:'本次内存模型满足恢复条件，演练结束。',rolled_back_escalated:'已恢复保存的配置；业务模型仍需复核，不把配置回退当作业务恢复。',escalated_unrestored:'未确认配置恢复，保持升级处理。',alternative:'下游鉴权分支成立，不执行入口预算动作。',stopped:'演练已停止，查看原因及账本。'};
 $('outcome').textContent=outcomes[session.phase]||'继续当前可用步骤，核对过程与结果。';$('json').textContent=JSON.stringify(E.snapshot(session),null,2);
}
function reset(){session=E.create($('scenario').value,AMF_SOURCE_BINDING);$('feedback').textContent='已开始新的练习。';render();}
for(const [value,title] of Object.entries(E.cases)){const option=node('option',title);option.value=value;$('scenario').append(option);}
for(const id of ['collect','review','confirm','execute','observe'])$(id).addEventListener('click',()=>{try{E[id](session);$('feedback').textContent='已完成本步记录。';}catch(e){$('feedback').textContent='当前阶段不能执行这一步，请核对状态。';}render();});
$('stop').addEventListener('click',()=>{E.cancel(session);$('feedback').textContent='已停止并核对当前状态。';render();});
$('reset').addEventListener('click',reset);$('scenario').addEventListener('change',reset);
$('export').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(E.snapshot(session),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='amf-exercise-'+session.case_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
reset();
