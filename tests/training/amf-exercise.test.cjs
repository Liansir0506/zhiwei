const test=require('node:test'),assert=require('node:assert/strict');
const E=require('../../static/amf-exercise-engine.js');
const binding={guide_source:'unit-guide.md',source_version:'draft-r2',body_sha256:'a'.repeat(64),origin:'unit_fixture'};
function ready(mode='normal'){const s=E.create(mode,binding);E.collect(s);E.review(s);return s;}
function observing(mode='normal'){const s=ready(mode);E.confirm(s);E.execute(s);return s;}
test('normal path uses cohort counts, bound confirmation and verified observations',()=>{
 const s=ready();assert.equal(s.phase,'awaiting_confirmation');assert.throws(()=>E.execute(s));
 E.confirm(s);E.execute(s);E.observe(s);assert.equal(s.phase,'observing');
 assert.equal(s.observations[0].prior_completed,40);assert.equal(s.observations[0].current_completed,40);
 E.observe(s);assert.equal(s.phase,'observing');E.observe(s);assert.equal(s.phase,'closed_training');
 assert.equal(s.consecutive_good,2);assert.equal(s.model.ingress_budget,80);
 assert.ok(s.audit.every(r=>!r.real_action_authorized&&!r.site_truth_verified&&!r.human_review_verified));
});
for(const [mode,phase,reason] of [['counter','alternative','downstream_evidence'],['conflict','stopped','conflicting_evidence'],['missing','stopped','missing_evidence'],['wrong_target','stopped','target_or_schema_mismatch']])test(mode+' branch cannot execute',()=>{
 const s=E.create(mode,binding);E.collect(s);if(s.phase==='collected')E.review(s);
 assert.equal(s.phase,phase);assert.equal(s.reason,reason);assert.throws(()=>E.confirm(s));assert.throws(()=>E.execute(s));
});
for(const field of ['target_id','schema_version','environment'])test(field+' drift rejects without changing model',()=>{
 const s=ready();E.confirm(s);s.model[field]='external-or-other';const before=JSON.stringify(s.model);
 E.execute(s);assert.equal(s.phase,'stopped');assert.equal(JSON.stringify(s.model),before);
});
test('stale revision rejects without changing model',()=>{const s=ready();E.confirm(s);s.model.revision++;const before=JSON.stringify(s.model);E.execute(s);assert.equal(s.phase,'stopped');assert.equal(JSON.stringify(s.model),before);});
for(const field of ['action','to_budget','observation_required_consecutive','observation_max_ticks'])test('edited '+field+' invalidates confirmation',()=>{
 const s=ready();E.confirm(s);s.plan[field]=field==='action'?'restart_live_amf':999;E.execute(s);assert.equal(s.phase,'stopped');assert.equal(s.model.ingress_budget,20);
});
test('source revision drift invalidates plan',()=>{const s=ready();s.source_reference.source_version='other';E.confirm(s);assert.equal(s.phase,'stopped');});
test('confirmation cannot be replayed',()=>{const s=observing();const before=s.model.revision;assert.throws(()=>E.execute(s));assert.equal(s.model.revision,before);});
for(const mutation of ['target','revision','window','unit','denominator'])test('incomparable '+mutation+' stops review',()=>{
 const s=E.create('normal',binding);E.collect(s);
 if(mutation==='target')s.records.ingress_queue.target_id='other';
 if(mutation==='revision')s.records.ingress_queue.revision=99;
 if(mutation==='window')s.records.ingress_queue.window='different';
 if(mutation==='unit')s.records.ingress_queue.unit='milliseconds';
 if(mutation==='denominator')s.records.registration_window.incoming=0;
 E.review(s);assert.equal(s.phase,'stopped');
});
test('partial apply restores exact saved config but does not claim service recovery',()=>{
 const s=observing('apply_failure');assert.equal(s.phase,'rolled_back_escalated');assert.equal(s.model.ingress_budget,20);
 const last=s.audit.at(-1);assert.equal(last.detail.config_restored,true);assert.equal(last.detail.service_recovered,false);
});
test('rollback failure stays unresolved',()=>{const s=observing('rollback_failure');assert.equal(s.phase,'escalated_unrestored');assert.equal(s.model.ingress_budget,70);assert.equal(s.audit.at(-1).detail.config_restored,false);});
test('recurrence rolls back rather than completing',()=>{const s=observing('recurrence');E.observe(s);E.observe(s);E.observe(s);assert.equal(s.phase,'rolled_back_escalated');assert.equal(s.reason,'recurrence');});
test('cancel before change does not mutate configuration',()=>{const s=ready();E.cancel(s);assert.equal(s.phase,'stopped');assert.equal(s.model.ingress_budget,20);});
test('cancel after change restores backup and escalates',()=>{const s=observing();E.cancel(s);assert.equal(s.phase,'rolled_back_escalated');assert.equal(s.model.ingress_budget,20);});
test('snapshots are detached and do not modify session',()=>{const s=ready();const x=E.snapshot(s);x.model.ingress_budget=999;assert.equal(s.model.ingress_budget,20);});
test('unknown scenarios and unbound sources rejected',()=>{assert.throws(()=>E.create('unknown',binding));assert.throws(()=>E.create('normal',{}));});
for(const change of ['missing','target','budget'])test('rollback binding '+change+' cannot restore another or unknown target',()=>{
 const s=observing();
 if(change==='missing')s.backup=null;
 if(change==='target')s.model.target_id='other';
 if(change==='budget')s.backup.ingress_budget=999999;
 const before=JSON.stringify(s.model);E.cancel(s);assert.equal(s.phase,'escalated_unrestored');assert.equal(JSON.stringify(s.model),before);
});
test('export never claims physical facts or authenticated approval',()=>{const s=ready();s.real_action_authorized=true;s.human_review_verified=true;const x=E.snapshot(s);assert.equal(x.real_action_authorized,false);assert.equal(x.human_review_verified,false);assert.equal(x.source_authenticity_verified,false);});
