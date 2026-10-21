/* In-memory UI contract fixture. No real credentials, services or model calls. */
const alarmId='UI-ALM-001';
const state={handling:'pending_confirm',verdict:null,history:[],ticket:null,qaCalls:0,evidenceReads:0,writes:[],diagnoses:0,failTicket:false};
const send=(res,code,data)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
const source={evidence_id:'ui_evidence_1',source:'AMF 注册异常核对说明',doc_id:'ui-guide',kb_version:'ui-fixture',source_revision:'r1',score:.95};
const alarm=()=>({alarm_id:alarmId,alarm_code:'ALM-AMF-REG',title:'AMF 注册成功率下降',severity:'critical',severity_label:'紧急',status:'open',status_label:'未处理',handling_state:state.handling,handling_state_label:{pending_confirm:'待确认',processing:'处理中',observing:'恢复观察',resolved:'已解决'}[state.handling],occurred_at:'2026-10-21T10:20:00',source_system:'UI contract fixture',device:{device_id:'AMF-02',name:'AMF-02',device_type:'AMF',software_version:'R2.4'},recent:[],payload:{}});
function route(req,res,u,body){
 const p=u.pathname,method=req.method;
 if(p==='/qa/recommendations')return send(res,200,{items:[1,2,3,4].map((n)=>({query:['AMF 注册失败先查什么？','N2 和 N4 有什么区别？','SCTP 关联异常怎样取证？','第四题不显示'][n-1]})),kb_version:'ui-fixture'});
 if(p==='/kb/versions')return send(res,200,{versions:[{version:'ui-fixture',status:'ACTIVE'}],can_preview_staged:false});
 if(p==='/kb/evidence/ui_evidence_1'){state.evidenceReads++;return send(res,200,{...source,location:{kind:'section',section:'2'},text:'先核对设备与事件窗口，再检查 N2 接口和 SCTP 关联；证据不足时继续取证。'});}
 if(p==='/qa/sse'){
  state.qaCalls++;
  if(body.query==='测试失败')return send(res,503,{detail:'fixture failure'});
  res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store'});
  const event=(name,data)=>res.write('event: '+name+'\ndata: '+JSON.stringify(data)+'\n\n');
  event('meta',{kb_version:'ui-fixture'});event('token',{text:'先核对设备、事件窗口与 N2 / SCTP 关联，再补充验证依据。[1]'});
  event('done',{status:'ANSWERED',answer:'先核对设备、事件窗口与 N2 / SCTP 关联，再补充验证依据。[1]',sources:[source],message_id:'ui-message',kb_version:'ui-fixture',route:'retrieval',answer_mode:'citation'});res.end();return;
 }
 if(p==='/feedback')return send(res,200,{status:'ok'});
 if(p==='/agent/diagnose-rule'){
  state.diagnoses++;
  return send(res,200,{run_id:'ui-run-1',status:'ANSWERED',stop_reason:'rule_context_ready',mode:'rule',answer:'核对 N2 与 SCTP 关联，补充同窗设备记录。',sources:[],trace:[{tool:'get_alarm_context',phase:'context',status:'ok',elapsed_ms:10},{tool:'build_diagnosis',phase:'verify',status:'ok',elapsed_ms:4}],observations:[],budget:{calls_used:2,calls_remaining:2,elapsed_ms:14},diagnosis:{scope:{alarm_code:'ALM-AMF-REG',device_id:'AMF-02',device_type:'AMF'},facts:[{id:'F1',text:'同窗关联异常'}],candidates:[{title:'接入关联异常',verdict:'possible',support:['F1'],missing:['设备日志'],next_checks:['读取同窗 SCTP 记录']}],missing_info:['设备日志'],recommendation:'保留假设，补充证据后再确认。'}});
 }
 if(p==='/alerts/'+alarmId)return send(res,200,alarm());
 if(p==='/alerts/'+alarmId+'/diagnoses')return send(res,200,{items:[]});
 if(p==='/alerts/'+alarmId+'/handling'){
  if(method==='POST'){
   const a=body.action;state.writes.push({path:p,...body});
   if(a==='confirm'&&state.handling==='pending_confirm')state.handling='processing';
   else if(a==='handle'&&state.handling==='processing'){}
   else if(a==='observe'&&state.handling==='processing'&&body.metric_name&&body.metric_value&&Number.isInteger(body.observe_minutes)&&body.observe_minutes>0){state.verdict=body.verdict;state.handling=body.verdict==='pass'?'observing':'processing';}
   else if(a==='resolve'&&state.handling==='observing'&&state.verdict==='pass')state.handling='resolved';
   else return send(res,409,{detail:'当前状态不允许此动作'});
   state.history.push({...body,action_label:a,actor:'UI reviewer',created_at:'2026-10-21T10:40:00'});
   return send(res,200,{action_label:a,state:state.handling,state_label:alarm().handling_state_label});
  }
  return send(res,200,{state:state.handling,state_label:alarm().handling_state_label,last_observe_verdict:state.verdict,next_actions:({pending_confirm:['confirm'],processing:['handle','observe'],observing:['resolve'],resolved:[]})[state.handling],items:state.history});
 }
 if(p==='/alerts/'+alarmId+'/ticket-draft')return send(res,200,{title:'AMF 注册异常排查',category:'核心网',priority:'P2',owner_suggestion:'ops-a',owner_candidates:['ops-a'],suggestions:['核对后创建'],diagnosis_run_id:'ui-run-1'});
 if(p==='/alerts/'+alarmId+'/ticket'){
  if(method==='POST'){
   state.writes.push({path:p,...body});
   if(state.failTicket){state.failTicket=false;return send(res,503,{detail:'建单暂时失败，保留草稿后重试'});}
   if(state.ticket)return send(res,409,{detail:'已有工单'});
   state.ticket={...body,ticket_id:'UI-TICKET-1',status:'open',alarm_id:alarmId,alarm:{alarm_id:alarmId}};
  }return send(res,200,{ticket:state.ticket});
 }
 if(p==='/tickets')return send(res,200,{items:state.ticket?[state.ticket]:[],counts:{open:state.ticket?.status==='open'?1:0,processing:state.ticket?.status==='processing'?1:0,closed:state.ticket?.status==='closed'?1:0},page:1,pages:1,total:state.ticket?1:0});
 if(p==='/tickets/UI-TICKET-1')return send(res,200,{ticket:state.ticket});
 if(p==='/tickets/UI-TICKET-1/state'){
  state.writes.push({path:p,...body});
  if(body.action==='accept')state.ticket.status='processing';
  else if(body.action==='progress'){}
  else if(body.action==='close'&&state.handling==='resolved'&&body.note?.length>=8)state.ticket.status='closed';
  else return send(res,409,{detail:'需先完成恢复验证，当前不能关闭工单'});
  return send(res,200,{message:'已记录'});
 }
 if(p==='/tickets/UI-TICKET-1/case-draft')return send(res,200,{message:'案例草稿已提交审核'});
 send(res,404,{detail:'unmatched fixture'});
}
const supported=p=>['/qa/recommendations','/kb/versions','/kb/evidence/ui_evidence_1','/qa/sse','/feedback','/agent/diagnose-rule','/tickets'].includes(p)||p.startsWith('/alerts/'+alarmId)||p.startsWith('/tickets/UI-TICKET-1');
module.exports={state,handle(req,res,u){if(!supported(u.pathname))return false;if(req.method==='GET')route(req,res,u,{});else{let raw='';req.on('data',c=>raw+=c);req.on('end',()=>{try{route(req,res,u,raw?JSON.parse(raw):{});}catch(e){send(res,500,{detail:e.message});}});}return true;}};
