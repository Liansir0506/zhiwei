/* In-memory training model. No network, filesystem, platform action or identity API. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.AmfExercise=api;})(globalThis,function(){
 'use strict';
 const clone=x=>JSON.parse(JSON.stringify(x));
 const cases={normal:'入口排队分支',counter:'下游鉴权分支',conflict:'同窗证据矛盾',missing:'缺少取证记录',wrong_target:'对象不匹配',apply_failure:'应用中断后回退',rollback_failure:'回退失败升级',recurrence:'观察期间复发'};
 const flags={real_action_authorized:false,site_truth_verified:false,human_review_verified:false,independent_quality:false};
 function event(s,type,detail={}){s.audit.push({sequence:s.audit.length+1,type,phase:s.phase,config_revision:s.model.revision,detail:clone(detail),...flags});}
 function validTarget(s){return s.model.environment==='in_memory_training'&&s.model.target_id==='train-amf-001'&&s.model.schema_version==='amf-model-v1';}
 function stop(s,code){s.phase='stopped';s.reason=code;event(s,'stopped',{reason:code});return s;}
 function create(caseId,binding){
  if(!Object.hasOwn(cases,caseId))throw Error('unknown_case');
  if(!binding||binding.source_version!=='draft-r2'||!/^([a-f0-9]{64})$/.test(binding.body_sha256||''))throw Error('source_reference_required');
  const s={protocol:'amf-exercise-v1',case_id:caseId,phase:'new',reason:'',source_reference:clone(binding),
   model:{environment:'in_memory_training',target_id:'train-amf-001',schema_version:'amf-model-v1',revision:1,ingress_budget:20,queue:0,tick:0},
   baseline:{incoming:40,ingress_budget:80},records:null,plan:null,grant:null,backup:null,observations:[],consecutive_good:0,audit:[],...flags};
  if(caseId==='counter')s.model.ingress_budget=80;
  if(caseId==='wrong_target')s.model.target_id='other-amf';
  event(s,'created',{case_name:cases[caseId],data_origin:'generated_training_model'});return s;
 }
 function probe(s){
  const m=s.model,prior=m.queue,incoming=60,priorCompleted=Math.min(prior,m.ingress_budget);
  const currentCompleted=Math.min(incoming,Math.max(0,m.ingress_budget-priorCompleted));
  const authErrors=['counter','conflict'].includes(s.case_id)?20:0;
  m.queue=prior+incoming-priorCompleted-currentCompleted;m.tick++;
  return {environment:m.environment,target_id:m.target_id,schema_version:m.schema_version,revision:m.revision,
   window:'training-tick-'+m.tick,incoming,current_completed:currentCompleted,prior_completed:priorCompleted,
   queue:m.queue,auth_errors:authErrors,unit:'requests_per_training_tick',data_origin:'generated_training_model'};
 }
 function collect(s){
  if(s.phase!=='new')throw Error('collect_phase');
  if(!validTarget(s))return stop(s,'target_or_schema_mismatch');
  const row=probe(s);
  s.records={registration_window:row,ingress_queue:{...clone(row)},downstream_auth:{...clone(row)}};
  if(s.case_id==='missing')delete s.records.downstream_auth;
  s.phase='collected';event(s,'evidence_collected',{record_ids:Object.keys(s.records)});return s;
 }
 function review(s){
  if(s.phase!=='collected')throw Error('review_phase');
  if(!validTarget(s))return stop(s,'target_or_schema_mismatch');
  const rows=['registration_window','ingress_queue','downstream_auth'].map(id=>s.records?.[id]);
  if(rows.some(r=>!r))return stop(s,'missing_evidence');
  if(rows.some(r=>r.environment!==s.model.environment||r.target_id!==s.model.target_id||r.schema_version!==s.model.schema_version||r.revision!==s.model.revision||r.unit!=='requests_per_training_tick'))return stop(s,'incomparable_evidence');
  if(new Set(rows.map(r=>r.window)).size!==1)return stop(s,'different_observation_windows');
  const [registration,queue,auth]=rows;
  if(!Number.isInteger(registration.incoming)||registration.incoming<=0||!Number.isInteger(registration.current_completed)||registration.current_completed<0||registration.current_completed>registration.incoming||!Number.isInteger(queue.queue)||queue.queue<0||!Number.isInteger(auth.auth_errors)||auth.auth_errors<0||auth.auth_errors>registration.incoming)return stop(s,'invalid_denominator_or_measurement');
  const primary=registration.incoming>s.baseline.incoming&&queue.queue>0;
  const counter=auth.auth_errors>0;
  if(primary&&counter)return stop(s,'conflicting_evidence');
  if(counter){s.phase='alternative';s.reason='downstream_evidence';event(s,'alternative_branch');return s;}
  if(!primary)return stop(s,'no_supported_branch');
  s.plan={action:'restore_ingress_budget',environment:s.model.environment,target_id:s.model.target_id,schema_version:s.model.schema_version,
   from_revision:s.model.revision,before_budget:s.model.ingress_budget,to_budget:s.baseline.ingress_budget,
   observation_required_consecutive:2,observation_max_ticks:4,source_reference:clone(s.source_reference)};
  s.phase='awaiting_confirmation';event(s,'plan_prepared',{action:s.plan.action,limits:'memory_model_only'});return s;
 }
 function planMatches(s){return validTarget(s)&&s.plan?.action==='restore_ingress_budget'&&s.plan.environment===s.model.environment&&s.plan.target_id===s.model.target_id&&s.plan.schema_version===s.model.schema_version&&s.plan.from_revision===s.model.revision&&s.plan.before_budget===s.model.ingress_budget&&s.plan.to_budget===80&&s.plan.observation_required_consecutive===2&&s.plan.observation_max_ticks===4&&JSON.stringify(s.plan.source_reference)===JSON.stringify(s.source_reference);}
 function confirm(s){
  if(s.phase!=='awaiting_confirmation')throw Error('confirmation_phase');
  if(!planMatches(s))return stop(s,'stale_or_changed_plan');
  s.grant={kind:'exercise_confirmation_only',bound_plan:JSON.stringify(s.plan),consumed:false,...flags};
  s.phase='confirmed';event(s,'exercise_confirmed',{kind:s.grant.kind,authenticated_human_approval:false});return s;
 }
 function rollback(s,reason){
  s.grant=null;
  if(!s.backup||!validTarget(s)||s.backup.target_id!==s.model.target_id||s.backup.schema_version!==s.model.schema_version||!Number.isInteger(s.backup.ingress_budget)||s.backup.ingress_budget<1||s.backup.ingress_budget>1000){
   s.phase='escalated_unrestored';s.reason='rollback_binding_unavailable';event(s,'rollback_blocked',{config_restored:false,service_recovered:false});return s;
  }
  if(s.case_id==='rollback_failure'){
   s.phase='escalated_unrestored';s.reason=reason;event(s,'rollback_failed',{config_restored:false,service_recovered:false});return s;
  }
  s.model.ingress_budget=s.backup.ingress_budget;s.model.revision++;
  s.phase='rolled_back_escalated';s.reason=reason;
  event(s,'rollback_verified',{config_restored:s.model.ingress_budget===s.backup.ingress_budget,
   original_revision:s.backup.revision,service_recovered:false});return s;
 }
 function execute(s){
  if(s.phase!=='confirmed')throw Error('execution_phase');
  if(!planMatches(s)||!s.grant||s.grant.kind!=='exercise_confirmation_only'||s.grant.consumed||s.grant.bound_plan!==JSON.stringify(s.plan))return stop(s,'stale_or_missing_confirmation');
  s.backup={target_id:s.model.target_id,schema_version:s.model.schema_version,ingress_budget:s.model.ingress_budget,revision:s.model.revision};
  s.grant.consumed=true;
  s.model.ingress_budget=['apply_failure','rollback_failure'].includes(s.case_id)?70:s.plan.to_budget;s.model.revision++;
  event(s,'local_model_changed',{before_budget:s.backup.ingress_budget,after_budget:s.model.ingress_budget});
  if(['apply_failure','rollback_failure'].includes(s.case_id))return rollback(s,'apply_interrupted');
  s.phase='observing';event(s,'observation_started',{required_consecutive:2,max_ticks:4,universal_site_threshold:false});return s;
 }
 function observe(s){
  if(s.phase!=='observing')throw Error('observation_phase');
  if(!validTarget(s))return stop(s,'target_or_schema_mismatch');
  if(s.case_id==='recurrence'&&s.observations.length===2){s.model.ingress_budget=20;s.model.revision++;}
  const row=probe(s);s.observations.push(row);
  const good=row.queue===0&&row.current_completed===row.incoming&&row.auth_errors===0;
  s.consecutive_good=good?s.consecutive_good+1:0;
  event(s,'observed',{...row,pass:good,consecutive_good:s.consecutive_good});
  if(s.case_id==='recurrence'&&s.observations.length===3)return rollback(s,'recurrence');
  if(s.consecutive_good>=2){s.phase='closed_training';event(s,'training_completed',{model_criteria_met:true,site_recovery_verified:false});}
  else if(s.observations.length>=4)return rollback(s,'observation_failed');
  return s;
 }
 function cancel(s){
  if(['closed_training','rolled_back_escalated','escalated_unrestored','stopped','alternative'].includes(s.phase))throw Error('terminal_phase');
  if(s.phase==='observing')return rollback(s,'operator_stop');
  return stop(s,'operator_stop_before_change');
 }
 function snapshot(s){return {...clone(s),...flags,data_origin:'generated_training_model',source_authenticity_verified:false};}
 return {cases,create,collect,review,confirm,execute,observe,cancel,snapshot};
});
