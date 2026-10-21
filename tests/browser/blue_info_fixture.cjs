// Stateful UI contracts only: no actual databases, admission, publication or model calls.
const core=require('./blue_core_fixture.cjs');
const state=Object.assign(core.state,{infoWrites:[],parsed:0,executed:0,uploadFail:false,noCandidates:false,uploads:[],gatePassed:false,gateRuns:0,issue:{id:1,query_text:'AMF注册失败如何排查',cause_label:'资料缺失',answer_text:'需补充证据',assigned_to:null,verify_note:null}});
let role='ops';
function send(res,code,data){res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));}
const versions=[{version:'ui-fixture',status:'ACTIVE'},{version:'ui-next',status:'STAGED'}];
function route(req,res,u,body){
 const p=u.pathname,admin=role==='admin',post=req.method==='POST';
 if(post)state.infoWrites.push({path:p,body});
 if(p.startsWith('/stats/'))return send(res,200,{result:{active:2,recent:3,total:10,online:9,rate:90,unit:'%',backlog:1,overdue_24h:0,metric:'时延',trend:[],items:[],formula:'在线数 / 在册数 × 100%',denominator:10}});
 if(p==='/query/parse'){
  state.parsed++;const data=body.question.includes('汇总')?{supported:false,source:'gate',reason:'汇总请使用常用统计'}:{supported:true,source:'rule',spec:{table:'alarms',select:['alarm_id','status'],filters:[{field:'severity',op:'=',value:'critical'}],limit:20,time_window:'last_7d',explain:'最近紧急告警'},resolved:{order_by:{field:'occurred_at',dir:'desc'},time_window:'last_7d'}};
  if(body.question.includes('慢'))return setTimeout(()=>send(res,200,data),350);
  return send(res,200,data);
 }
 if(p==='/query/execute'){state.executed++;if(state.executeFail)return send(res,503,{detail:'查询依赖预热中，请重试'});const result={columns:['alarm_id','status'],rows:state.emptyQuery?[]:[{alarm_id:'UI-ALM-001',status:'open'}],row_count:state.emptyQuery?0:1,elapsed_ms:8,truncated:false};if(state.executeDelay)return setTimeout(()=>send(res,200,result),state.executeDelay);return send(res,200,result);}
 if(p==='/kb/capabilities')return send(res,200,{can_read_jobs:true});
 if(p==='/kb/version-scope')return send(res,200,{active:'ui-fixture',versions:versions.map(v=>({...v,readable:admin||v.status==='ACTIVE'}))});
 if(p==='/kb/catalog'){if(state.catalogFail)return send(res,503,{detail:'目录暂不可用'});return send(res,200,{kb_version:'ui-fixture',total:1,scope_note:'当前权限',page:1,pages:1,items:[{title:'AMF注册排查指南',source:'guide_amf.md',doc_type:u.searchParams.get('kind')==='faq'?'faq':'guide',synthetic:true,kb_version:'ui-fixture',summary:'先核对注册失败事件与接口状态。',visible_matching_chunks:1,question:'AMF注册失败如何排查',evidence_id:'ui_evidence_1'}]});}
 if(p==='/admin/ingest-workspace')return send(res,200,{can_write:admin,scope:'隔离测试目标',worker:{status:'running',compatible:true,message:'当前配置一致'},candidates:state.noCandidates?[]:[{version:'ui-next',label:'新候选',compatible:true,reason:'独立可写目录'}],uploads:state.uploads});
 if(p==='/admin/ingest-workspace/uploads'&&post){
  if(state.uploadFail)return send(res,503,{detail:'暂存服务不可用'});
  const row={id:'upload-1',filename:req.headers['x-filename'],source_id:u.searchParams.get('source_id'),kb_version:u.searchParams.get('kb_version'),admission_status:'pending',admission_note:'需具体审核',job:null};state.uploads=[row];return send(res,200,{...row,created:true});
 }
 if(p==='/admin/ingest-workspace/uploads/upload-1')return send(res,200,{sha256:'ui-hash-only',source_id:state.uploads[0]?.source_id,ledger:[]});
 if(p==='/admin/ingest-workspace/candidates'&&post){state.noCandidates=false;return send(res,200,{version:'ui-next'});}
 if(p==='/kb-fix/issues')return send(res,200,{items:state.issue.closed?[]:[state.issue]});
 if(p.startsWith('/kb-fix/issues/1/')){if(p.endsWith('/assign'))state.issue.assigned_to='UI reviewer';if(p.endsWith('/retest'))state.issue.verify_note='新答案与引用已生成，仍需人工核对';if(p.endsWith('/close')){if(!state.issue.verify_note||body.note?.length<8)return send(res,409,{detail:'需先复测并填写依据'});state.issue.closed=true;state.closeNote=body.note;}return send(res,200,{message:'已记录'});}
 if(p==='/case-drafts')return send(res,200,{items:[]});
 if(p==='/admin/eval/reports')return send(res,200,{active_version:'ui-fixture',versions,reports:[],warnings:[],jobs:[],can_run:false,workflow:'受控评测需已审核的独立题集'});
 if(p==='/admin/permissions')return send(res,200,{keys:[{id:1,user_id:'ops-ui',roles:['ops'],tenant_id:'default',key_hint:'ui-***',enabled:true}],matrix:[{role:'ops',desc:'运维只读与受控处理',apis:['/alerts']}]});
 if(p==='/admin/version-workspace/capabilities')return send(res,200,{can_read_versions:admin||role==='auditor',can_manage_versions:admin});
 if(p==='/admin/version-workspace/summary')return send(res,200,{active:'ui-fixture',counts:{ACTIVE:1,STAGED:1},note:'仅摘要'});
 if(p==='/admin/version-workspace')return send(res,200,{versions,gaps:[],scope:'隔离界面验收',runtime_note:'保留现有审核与鉴权',execution_mode:state.reviewOnly?'review_only':'test'});
 if(p==='/admin/version-workspace/runtime-handoffs')return send(res,200,{items:[],jobs:[]});
 if(p==='/admin/version-workspace/history')return send(res,200,{items:[]});
 if(/^\/admin\/version-workspace\/versions\/[^/]+$/.test(p)){const version=p.split('/').pop();return send(res,200,{version,status:version==='ui-next'?'STAGED':'ACTIVE',scope:'本地隔离范围',snapshot:{collections:{faq:{count:10}}},sources:[],candidate_kind:'registered',blockers:[],gaps:[],uploads:[],gate_jobs:[],packages:[],binding:{}});}
 if(p.endsWith('/gate')&&p.startsWith('/admin/version-workspace/'))return send(res,200,state.gateRuns?{recorded:true,report:{passed:state.gatePassed,checks:[{check:'来源准入',passed:state.gatePassed}]}}:{recorded:false,note:'尚未运行门禁'});
 if(p==='/admin/version-workspace/reviews')return send(res,200,{id:'review-1',actor:'UI reviewer',fingerprint:'ui-config',inspection:{blockers:[]}});
 if(p==='/admin/version-workspace/gate-jobs'){state.gateRuns++;state.gatePassed=state.gateRuns>1;return send(res,200,{id:'gate-'+state.gateRuns});}
 if(p.startsWith('/admin/version-workspace/gate-jobs/'))return send(res,200,{id:p.split('/').pop(),state:state.gatePassed?'passed':'blocked',completed:5,total:5,review_id:'review-1',error:state.gatePassed?'':'来源检查未通过'});
 if(p==='/admin/version-workspace/plans')return send(res,200,{id:'plan-1',from_version:'ui-fixture',version:'ui-next',scope:'本地隔离',confirmation:'publish ui-next',pointer_execution_enabled:false});
 return send(res,404,{detail:'unmatched info fixture '+p});
}
const supported=p=>p.startsWith('/stats/')||p.startsWith('/query/')||['/kb/capabilities','/kb/version-scope','/kb/catalog','/case-drafts','/admin/eval/reports','/admin/permissions'].includes(p)||p.startsWith('/admin/ingest-workspace')||p.startsWith('/kb-fix/issues')||p.startsWith('/admin/version-workspace');
module.exports={state,setRole(value){role=value;},handle(req,res,u){if(!supported(u.pathname))return core.handle(req,res,u);if(req.method==='GET')route(req,res,u,{});else{let raw='';req.on('data',c=>raw+=c);req.on('end',()=>{try{route(req,res,u,req.headers['content-type']?.includes('json')?JSON.parse(raw||'{}'):{bytes:Buffer.byteLength(raw)});}catch(e){send(res,500,{detail:e.message});}});}return true;}};
