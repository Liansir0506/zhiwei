/* Scoped, read-only catalog and explicit administrator upload controls. */
window.KnowledgeWorkspace = (() => {
  const $=id=>document.getElementById(id);
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=String(text);return n;};
  let config, epoch=0, active=null, timer=null, controller=null, capabilities={}, uploadBusy=false, canWrite=false;
  let scopeCache=null, capabilitiesCache=null;
  let uploadStage=0, docsPane='upload';
  function renderUpload() {
    for(const [id,on] of [['stepCandidate',docsPane==='upload'&&uploadStage===1],['stepUpload',docsPane==='upload'],['stepJobs',docsPane==='jobs'],['stepDocs',docsPane==='documents']])$(id).hidden=!on;
    $('docsSteps').hidden=docsPane!=='upload';
    document.querySelectorAll('[data-docs-pane]').forEach(button=>{const on=button.dataset.docsPane===docsPane;button.hidden=button.dataset.docsPane==='upload'?!canWrite:button.dataset.docsPane==='jobs'&&!capabilities.can_read_jobs;button.classList.toggle('is-active',on);button.setAttribute('aria-selected',String(on));});
    $('uploadForm').hidden=!canWrite||uploadStage===2;
    $('fileInput').closest('label').hidden=uploadStage!==0;
    $('uploadFileRules').hidden=uploadStage!==0;
    $('uploadReviewFields').hidden=uploadStage!==1;
    $('uploadReviewFields').querySelectorAll('input,select').forEach(input=>input.disabled=uploadStage!==1);
    $('uploadSubmit').disabled=uploadStage!==1||!canWrite||uploadBusy||!$('uploadVersion').value;
    $('uploadWizardNext').hidden=uploadStage!==0;$('uploadWizardBack').hidden=uploadStage!==1;$('uploadSubmit').hidden=uploadStage!==1;
    $('uploadStepResult').hidden=uploadStage!==2;
    $('uploadReviewFilename').textContent='已选文件：'+($('fileInput').files[0]?.name||'尚未选择');
    document.querySelectorAll('#docsSteps li').forEach((li,index)=>{li.dataset.state=index<uploadStage?'done':index===uploadStage?'active':'waiting';});
  }
  function showDocsPane(name) {docsPane=name;renderUpload();}
  function fileError(file) {
    if(!file)return '请先选择要提交的资料。';
    if(!/^(manual|faq|fault_case|sop|guide|standard)_/i.test(file.name)||! /\.(md|csv|txt|docx|pdf|xlsx)$/i.test(file.name)||file.size===0||file.size>20*1024*1024)return '请使用受支持格式、资料类型前缀和非空文件，大小不超过20MB。';
    if(!/^[\x20-\x7E]+$/.test(file.name))return '当前接口需要英文文件名，例如 manual_device.pdf；请重命名后选择。';
    return '';
  }
  const pages={kb:1,docs:1};
  function valid(turn) {return turn.epoch===epoch&&turn.key===config.key()&&active===turn.view&&$('view-'+turn.view).classList.contains('active');}
  function turn(view) {return {epoch:++epoch,key:config.key(),view};}
  function stop() {epoch++;controller?.abort();controller=null;clearTimeout(timer);timer=null;active=null;}
  function clear() {
    for (const id of ['kbResults','docsList','kbDetail','docDetail','ingestJobs','ingestLedger','candidateCompatibility']) $(id).replaceChildren();
    $('uploadSubmit').disabled=true;capabilities={};
    $('uploadMsg').textContent='';
    for(const prefix of ['kb','doc']){$(prefix+'Summary').textContent='';$(prefix+'Page').textContent='';$(prefix+'Previous').disabled=true;$(prefix+'Next').disabled=true;}
    $('uploadForm').hidden=true;
    uploadStage=0;
    $('candidateForm').hidden=true;canWrite=false;$('ingestWorker').textContent='';
    for(const id of ['fileInput','uploadSource','candidateLabel'])$(id).value='';
    const cs=$('candidateState'); if(cs){cs.textContent='尚未核对';cs.className='pill muted';}
    const us=$('uploadState'); if(us){us.textContent='等待候选';us.className='pill muted';}
    const pre=$('uploadPrereq'); if(pre){pre.hidden=true;pre.textContent='';}
    const fl=$('fileLabel'); if(fl)fl.textContent='点击选择文件';
    document.querySelectorAll('#docsSteps li').forEach(li=>li.removeAttribute('data-state'));
  }
  function credentialsChanged() {const view=active;stop();clear();scopeCache=null;capabilitiesCache=null;if(view)load(view);}
  async function json(url, options={}) {
    const response=await fetch(url,{cache:'no-store',...options,headers:{...config.headers(),...options.headers}});
    if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}
      const error=new Error((response.status===409&&typeof detail==='string'?detail:null)||({401:'登录已失效，请重新设置凭据。',403:'当前账号无此操作权限。',429:'请求过于频繁，请稍后刷新。',503:'服务暂不可用，请稍后重试。'})[response.status]|| (typeof detail==='string'?detail:'请求失败（HTTP '+response.status+'）'));error.status=response.status;throw error;}
    return response.json();
  }
  function filters(view) {
    const prefix=view==='kb'?'kb':'doc';
    return {query:$(prefix+'Search').value.trim(),kind:view==='docs'?'document':$('kbKind').value,
      origin:$(prefix+'Origin').value,kb_version:$(prefix+'Version').value,page:pages[view],page_size:12};
  }
  function updateVersionNotice(view,scope) {
    const box=$(view==='kb'?'kbVersionNotice':'docVersionNotice');if(!box)return;
    const chosen=$(view==='kb'?'kbVersion':'docVersion').value;
    if(!chosen||chosen===scope.active){box.hidden=true;box.textContent='';return;}
    const meta=(scope.versions||[]).find(item=>item.version===chosen)||{};
    box.hidden=false;box.textContent='管理员预览：本页显示 '+(meta.status==='STAGED'?'候选版本 ':'')+chosen
      +'，不改变激活版本 '+scope.active+'；预览仅供核对，问答与发布仍使用激活版本。';
  }

  async function catalog(view, current) {
    const prefix=view==='kb'?'kb':'doc', results=$(view==='kb'?'kbResults':'docsList');
    results.replaceChildren(el('p','empty-state','正在读取当前权限可见知识…'));
    $(prefix+'Detail').replaceChildren();
    const params=new URLSearchParams(Object.entries(filters(view)).filter(([,value])=>value!==''));
    const data=await json('/kb/catalog?'+params,{signal:controller.signal});if(!valid(current))return;
    results.replaceChildren();
    $(prefix+'Summary').textContent=`${data.kb_version} · ${data.total} 条可见条目 · ${data.scope_note}`;
    $(prefix+'Page').textContent=`第 ${data.page} 页 / ${Math.max(data.pages,1)} 页`;
    $(prefix+'Previous').disabled=data.page<=1;$(prefix+'Next').disabled=data.page>=data.pages;
    if(!data.items.length){results.append(el('p','empty-state','没有符合当前条件的可读资料。可清空搜索或调整筛选。'));return;}
    for(const item of data.items){
      const card=el('article','knowledge-card');
      card.append(el('h3',null,item.title||item.source));
      const tags=el('div','knowledge-tags');
      tags.append(el('span','pill info',item.doc_type==='faq'?'FAQ':'文档'),el('span','pill muted',item.kb_version));
      card.append(tags);
      const summary=String(item.summary||'').replace(/\s+/g,' ').trim();
      card.append(el('p','knowledge-snippet',summary.length>120?summary.slice(0,120)+'…':summary));
      card.append(el('p','knowledge-meta','来源 '+item.source+' · 匹配片段 '+item.visible_matching_chunks+' 个'));
      const actions=el('div','knowledge-actions');const detail=el('button','subtle-button','查看原文');detail.type='button';
      detail.onclick=()=>openDetail(item,view,current);actions.append(detail);
      if(view==='kb'){const ask=el('button','subtle-button',item.question?'填入FAQ问题':'补充问题');ask.type='button';
        ask.onclick=()=>{if(!valid(current))return;config.showQa();Workspace.fillQuestion(item.question||`请结合 ${item.title}，说明：`);};actions.append(ask);}
      card.append(actions);results.append(card);
    }
  }
  let detailRequest=0;
  async function openDetail(item, view, current, fragmentIndex=0) {
    if(!valid(current))return;const request=++detailRequest,target=$(view==='kb'?'kbDetail':'docDetail');
    target.replaceChildren(el('p','empty-state','正在重新校验权限并读取原文…'));
    try{
      const fragments=item.fragments||[item];
      const group=await EvidenceViewer.loadAll({...item,evidence_id:fragments[fragmentIndex].evidence_id},config.headers());
      if(!valid(current)||request!==detailRequest)return;
      const box=el('article','knowledge-card');box.append(el('h3',null,item.title));
      if(fragments.length>1){const label=el('label','knowledge-meta','选择匹配可见片段：'),selector=el('select');selector.setAttribute('aria-label','文档片段');
        fragments.forEach((fragment,index)=>selector.add(new Option(`片段 ${index+1} / ${fragments.length}`,String(index))));selector.value=String(fragmentIndex);
        selector.onchange=()=>openDetail(item,view,current,Number(selector.value));label.append(selector);box.append(label);}
      if(item.question)box.append(el('p',null,'原问题：'+item.question));
      for(const evidence of group){
        const tech=el('details','tech-details');
        tech.append(el('summary',null,'版本、来源修订与位置'),
          el('p','knowledge-meta',`版本：${evidence.kb_version} · 来源修订：${evidence.source_revision}`),
          el('p','knowledge-meta',EvidenceViewer.location(evidence.location)),
          el('p','knowledge-meta',EvidenceViewer.reviewNotice(evidence)));
        box.append(tech,el('pre','knowledge-original',evidence.text));
      }
      box.append(el('p','knowledge-meta','分块原文仅用于查阅；可读状态不代表生产操作审批。'));target.replaceChildren(box);target.scrollIntoView({block:'nearest'});
    }catch(error){if(valid(current)&&request===detailRequest)target.replaceChildren(el('p','context-error',error.message));}
  }
  async function jobs(current) {
    if(!capabilities.can_read_jobs){$('uploadNotice').textContent='当前账号可查阅文档；上传与任务需相应权限。';$('ingestWorker').textContent='当前账号无入库状态读取权限。';$('ingestJobs').replaceChildren(el('p','empty-state','当前账号无入库任务查看权限。'));showDocsPane('documents');return;}
    try{
      const data=await json('/admin/ingest-workspace',{signal:controller.signal});if(!valid(current))return;
      canWrite=data.can_write===true;$('candidateForm').hidden=!canWrite;
      if(!canWrite&&docsPane==='upload')docsPane='documents';
      $('uploadNotice').textContent=canWrite?data.scope:'当前账号只读；创建、上传和重试仅限未缩小范围的default管理员。';
      const worker=data.worker;
      const workerNames={running:'运行中',ready:'就绪',busy:'处理中',starting:'启动中',stale:'心跳过期',stopped:'已停止',unavailable:'不可用',idle:'空闲'};
      $('ingestWorker').textContent=`专用Worker：${workerNames[worker.status]||worker.status} · ${worker.message} · ${worker.compatible?'配置一致':'配置不一致'}${worker.heartbeat_at?' · 心跳 '+FmtTime.relative(worker.heartbeat_at):''}`;
      const candidate=$('uploadVersion'),previous=candidate.value;
      candidate.replaceChildren(new Option('选择兼容的可写候选',''));$('candidateCompatibility').replaceChildren();
      for(const row of data.candidates){if(row.compatible)candidate.add(new Option(row.label+' · '+row.version,row.version));
        $('candidateCompatibility').append(el('p','knowledge-meta',`${row.version} · ${row.compatible?'兼容可写':'不可写'} · ${row.reason}`));}
      if([...candidate.options].some(option=>option.value===previous))candidate.value=previous;
      const usable=data.candidates.filter(row=>row.compatible);
      if(!candidate.value&&usable.length===1)candidate.value=usable[0].version;
      if($('candidateCreateDetails')&&!usable.length&&canWrite)$('candidateCreateDetails').open=true;
      $('uploadSubmit').disabled=!canWrite||uploadBusy||!candidate.value;
      // 步骤与就近提示：不可写 / 无兼容候选时明确原因与下一步
      const compatibleRows=data.candidates.filter(row=>row.compatible);
      const cs=$('candidateState'),us=$('uploadState'),pre=$('uploadPrereq'),steps=document.querySelectorAll('#docsSteps li');
      if(cs){cs.textContent=canWrite?(compatibleRows.length?'可写 · '+compatibleRows.length+' 个兼容候选':'无可写候选'):'只读';
        cs.className='pill '+(canWrite?(compatibleRows.length?'ok':'warn'):'muted');}
      if(us){us.textContent=canWrite&&compatibleRows.length?'可以上传':(canWrite?'等待创建候选':'只读');us.className='pill '+(canWrite&&compatibleRows.length?'ok':'muted');}
      if(pre){
        if(!canWrite){pre.hidden=false;pre.textContent='当前账号只能查看；创建候选与上传需要未缩小范围的 default 管理员。';}
        else if(!compatibleRows.length){pre.hidden=false;pre.textContent='没有兼容的可写候选：选择文件后，在“核对资料”的入库目标中创建新候选，再提交。';}
        else {pre.hidden=true;pre.textContent='';}
      }
      if(steps.length){
        steps.forEach(li=>li.removeAttribute('data-state'));
        if(!canWrite||!compatibleRows.length){steps[0].dataset.state='active';}
        else {steps[0].dataset.state='done';steps[1].dataset.state='active';}
      }
      renderUpload();
      $('ingestJobs').replaceChildren();
      const statuses={pending:'排队中',retry_wait:'等待重试',running:'处理中',success:'候选处理完成',failed:'失败',cancelled:'已取消'};
      if(!data.uploads.length)$('ingestJobs').append(el('p','empty-state','暂无工作区上传。历史候选保持封存，创建兼容候选后可上传。'));
      for(const record of data.uploads){
        const job=record.job,card=el('article','knowledge-card');card.dataset.uploadId=record.id;
        const compatible=data.candidates.some(row=>row.version===record.kb_version&&row.compatible);card.dataset.compatible=String(compatible);
        card.append(el('h3',null,record.filename),el('p','knowledge-meta',`来源 ${record.source_id} · 候选 ${record.kb_version}`));
        if(!compatible)card.append(el('p','knowledge-meta','该候选已归档或与当前配置不一致，不能重新入库；记录仅供追溯。'));
        card.append(el('p',null,record.admission_status==='pending'?'待审核 · 仅暂存，未进入向量或问答':'沿用此前批准的原文，未改动'));
        card.append(el('p','knowledge-meta',record.admission_note));
        if(job){card.append(el('p',null,`${statuses[job.status]||job.status} · 阶段 ${job.stage} · ${job.progress_done}/${job.progress_total} · 尝试 ${job.attempt_count}/${job.max_attempts}`));
          card.append(el('p','knowledge-meta',`任务 ${job.job_id}${job.heartbeat_at?' · 心跳 '+FmtTime.relative(job.heartbeat_at):''}`));
          if(job.status==='success')card.append(el('p','knowledge-meta','入库完成不等于审核通过或版本激活；发布须通过现有门禁。'));
          if(job.error_message)card.append(el('p','context-error',job.error_message));
          if(canWrite&&['failed','cancelled','pending','retry_wait','running'].includes(job.status)&&(!['failed','cancelled'].includes(job.status)||compatible)){
            const action=['failed','cancelled'].includes(job.status)?'retry':'cancel',button=el('button','subtle-button',action==='retry'?'重试此任务':'取消此任务');button.type='button';button.dataset.action=action;
            button.onclick=async()=>{if(!valid(current))return;button.disabled=true;
              try{await json('/admin/ingest-workspace/uploads/'+record.id+'/'+action,{method:'POST'});if(valid(current))await jobs(current);}
              catch(error){if(valid(current)){card.append(el('p','context-error',error.message));button.disabled=false;}}};card.append(button);}
        }
        if(job && canWrite && record.kb_version){
          const toVersion=el('button','subtle-button','查看版本与审核');toVersion.type='button';toVersion.dataset.action='version';
          toVersion.onclick=()=>{if(typeof window.switchTab==='function')window.switchTab('versions',{version:record.kb_version});};
          card.append(toVersion);
        }
        const detail=el('button','subtle-button','查看账本与文件校验');detail.type='button';detail.dataset.action='ledger';
        detail.onclick=async()=>{try{const result=await json('/admin/ingest-workspace/uploads/'+record.id);if(!valid(current))return;
          const box=el('article','knowledge-card');box.append(el('h3',null,'候选入库账本'),el('p','knowledge-meta',`SHA256 ${result.sha256} · 来源 ${result.source_id}`));
          if(!result.ledger.length)box.append(el('p',null,'尚无入库账本记录；待审核或尚未执行不会被视为已入库。'));
          for(const row of result.ledger)box.append(el('p',null,`${row.filename} · ${row.status} · ${row.chunk_count}片段 · ${row.asset_id}${row.error_kind?' · '+row.error_kind:''}`));
          $('ingestLedger').replaceChildren(box);$('ingestLedger').scrollIntoView({block:'nearest'});
        }catch(error){if(valid(current))$('ingestLedger').replaceChildren(el('p','context-error',error.message));}};
        card.append(detail);
        $('ingestJobs').append(card);
      }
      clearTimeout(timer);timer=setTimeout(()=>{if(valid(current))jobs(current);},5000);
    }catch(error){if(valid(current)&&error.name!=='AbortError'){
      $('ingestJobs').replaceChildren(el('p','context-error',error.message));$('ingestWorker').textContent='无法确认当前Worker状态：'+error.message;
      $('candidateCompatibility').replaceChildren();$('ingestLedger').replaceChildren();
      $('uploadForm').hidden=true;$('candidateForm').hidden=true;canWrite=false;$('uploadSubmit').disabled=true;
      const pre=$('uploadPrereq');if(pre){pre.hidden=false;pre.textContent='入库状态读取失败：'+error.message+'。请手动刷新。';}
      clearTimeout(timer);timer=setTimeout(()=>{if(valid(current))jobs(current);},5000);
    }}
  }
  async function load(view) {
    stop();clear();active=view;controller=new AbortController();const current=turn(view);detailRequest++;
    if(view==='docs')renderUpload();
    if(view==='docs'){$('uploadSubmit').disabled=true;$('uploadNotice').textContent='正在核对上传权限…';}
    try{
      capabilities=capabilitiesCache||(capabilitiesCache=await json('/kb/capabilities',{signal:controller.signal}));if(!valid(current))return;
      let scope=scopeCache;
      if(!scope){
        try{scope=await json('/kb/version-scope',{signal:controller.signal});}
        catch(error){
          if(error.name==='AbortError')throw error;
          // 兼容尚未重启的服务：退回旧版语义（当前激活 + 同绑定候选可预览）
          const versions=(await json('/kb/versions',{signal:controller.signal})).versions||[];
          scope={active:(versions.find(v=>v.status==='ACTIVE')||{}).version||'',
            versions:versions.map(v=>({version:v.version,status:v.status,readable:v.status==='STAGED',reason:''}))};
        }
        scopeCache=scope;
      }
      if(!valid(current))return;
      const selector=$(view==='kb'?'kbVersion':'docVersion'),selected=selector.value;
      selector.replaceChildren(new Option(scope.active?('当前激活：'+scope.active):'当前激活版本',''));
      for(const version of (scope.versions||[])){
        if(version.version===scope.active)continue;
        const suffix=version.readable?' · 候选预览':' · '+({ARCHIVED:'归档',STAGED:'候选',ACTIVE:'激活'}[version.status]||version.status)+'不可读';
        const option=new Option(version.version+suffix,version.version);
        if(!version.readable){option.disabled=true;option.title=version.reason||'当前服务不可读该版本';}
        selector.add(option);
      }
      if([...selector.options].some(option=>option.value===selected))selector.value=selected;
      updateVersionNotice(view,scope);
      if(view==='docs'){
        $('uploadVersion').replaceChildren(new Option('正在核对兼容候选',''));
      }
      if(view==='docs')await Promise.all([
        catalog(view,current).catch(error=>{if(valid(current)&&error.name!=='AbortError')$('docsList').replaceChildren(el('p','context-error',error.message));}),
        jobs(current)
      ]);
      else await catalog(view,current);
    }catch(error){if(valid(current)&&error.name!=='AbortError'){
      $(view==='kb'?'kbResults':'docsList').replaceChildren(el('p','context-error',error.message));
      if(view==='docs'){$('uploadForm').hidden=true;$('ingestJobs').replaceChildren();}
    }}
  }
  async function upload(event) {
    event.preventDefault();if(uploadBusy||!canWrite||active!=='docs')return;
    if(uploadStage!==1){$('uploadMsg').textContent='请先核对资料信息，再提交。';return;}
    const file=$('fileInput').files[0],candidate=$('uploadVersion').value,id=$('uploadSource').value.trim(),msg=$('uploadMsg');
    if(!file||!candidate||!id){msg.textContent='请选择文件、待发布候选并填写稳定来源ID。';return;}
    if(!/^(manual|faq|fault_case|sop|guide|standard)_/i.test(file.name)||! /\.(md|csv|txt|docx|pdf|xlsx)$/i.test(file.name)||file.size===0||file.size>20*1024*1024){msg.textContent='请检查类型前缀、受支持格式及文件大小（非空且不超过20MB）。';return;}
    if(!/^[\x20-\x7E]+$/.test(file.name)){msg.textContent='当前上传协议需使用英文文件名；请保留类型前缀后重命名。';return;}
    if(id.length>128||/[\x00-\x1F]/.test(id)){msg.textContent='来源ID需为1～128个字符且不能包含控制字符。';return;}
    const current={epoch,key:config.key(),view:active};uploadBusy=true;$('uploadSubmit').disabled=true;msg.textContent='正在提交 '+file.name+'…';
    const done=window.UIMotion?.busy($('uploadSubmit'));
    try{
      const result=await json('/admin/ingest-workspace/uploads?'+new URLSearchParams({kb_version:candidate,source_id:id}),{method:'POST',headers:{'X-Filename':file.name,'Content-Type':'application/octet-stream'},body:file});
      if(!valid(current))return;
      msg.textContent=`${result.created===false?'已存在同一上传':'已上传'}：${result.filename} · ${result.job_id?'已准入并排队，任务 '+result.job_id:'待具体审核，仅暂存，未进入向量或问答'}。尚未激活。`;
      uploadStage=2;renderUpload();
      await jobs(current);
    }catch(error){if(valid(current))msg.textContent=error.message;}
    finally{done?.();uploadBusy=false;if(active==='docs')renderUpload();}
  }
  function init(options) {
    config=options;
    for(const view of ['kb','docs']){const prefix=view==='kb'?'kb':'doc';
      $(prefix+'Filter').onsubmit=event=>{event.preventDefault();pages[view]=1;scopeCache=null;capabilitiesCache=null;load(view);};
      for(const suffix of ['Origin','Version',...(view==='kb'?['Kind']:[])])$(prefix+suffix).onchange=()=>{pages[view]=1;load(view);};
      $(prefix+'Previous').onclick=()=>{pages[view]=Math.max(1,pages[view]-1);load(view);};
      $(prefix+'Next').onclick=()=>{pages[view]++;load(view);};
    }
    $('uploadForm').onsubmit=upload;
    document.querySelectorAll('[data-docs-pane]').forEach(button=>button.onclick=()=>showDocsPane(button.dataset.docsPane));
    $('uploadWizardNext').onclick=()=>{const error=fileError($('fileInput').files[0]);if(error){$('uploadMsg').textContent=error;return;}uploadStage=1;$('uploadMsg').textContent='';renderUpload();};
    $('uploadWizardBack').onclick=()=>{uploadStage=0;renderUpload();};
    $('uploadShowJobs').onclick=()=>showDocsPane('jobs');
    $('uploadAnother').onclick=()=>{uploadStage=0;$('fileInput').value='';$('uploadSource').value='';$('fileLabel').textContent='点击选择文件';$('uploadMsg').textContent='';renderUpload();};
    $('jobRefresh').onclick=()=>{if(active==='docs'&&controller)jobs({epoch,key:config.key(),view:active});};
    $('fileInput').onchange=()=>{const file=$('fileInput').files[0];const label=$('fileLabel');if(label)label.textContent=file?file.name:'点击选择文件';$('uploadMsg').textContent=file?fileError(file):'';};
    $('uploadVersion').onchange=()=>{$('uploadSubmit').disabled=!canWrite||uploadBusy||!$('uploadVersion').value;};
    $('candidateForm').onsubmit=async event=>{event.preventDefault();if(!canWrite)return;const current={epoch,key:config.key(),view:active},button=$('candidateCreate');button.disabled=true;
      try{const result=await json('/admin/ingest-workspace/candidates',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label:$('candidateLabel').value.trim()})});
        if(!valid(current))return;await jobs(current);if(valid(current)){$('uploadVersion').value=result.version;$('uploadSubmit').disabled=false;$('uploadMsg').textContent='已创建兼容可写候选 '+result.version+'；尚未激活。';}}
      catch(error){if(valid(current))$('uploadMsg').textContent=error.message;}finally{button.disabled=false;}};
    window.addEventListener('storage',event=>{if(event.key==='api_key')credentialsChanged();});
  }
  return {init,load,leave(){stop();clear();},credentialsChanged,showPane:showDocsPane};
})();
