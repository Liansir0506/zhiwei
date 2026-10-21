const fs=require('fs');
const input=JSON.parse(fs.readFileSync(0,'utf8')),E=require(input.engine_path);
const expected={normal:'closed_training',counter:'alternative',conflict:'stopped',missing:'stopped',wrong_target:'stopped',apply_failure:'rolled_back_escalated',rollback_failure:'escalated_unrestored',recurrence:'rolled_back_escalated'};
const results=[];
for(const [id,phase] of Object.entries(expected)){
 const s=E.create(id,input.binding);E.collect(s);if(s.phase==='collected')E.review(s);
 if(s.phase==='awaiting_confirmation'){E.confirm(s);E.execute(s);}
 for(let i=0;i<8&&s.phase==='observing';i++)E.observe(s);
 if(s.phase!==phase)throw Error('Unexpected training branch: '+id);
 results.push({case_id:id,expected_phase:phase,actual_phase:s.phase,passed:true,snapshot:E.snapshot(s)});
}
process.stdout.write(JSON.stringify({cases:results,training_model_only:true,physical_truth_verified:false,independent_denominator:0,paid_calls:0}));
