/* Read-only workspace panels. All original text is fetched again through EvidenceViewer. */
window.Workspace = (() => {
  const $ = id => document.getElementById(id);
  let config, sources = [], evidenceRequest = 0, contextRequest = 0;
  let selectedAnswer = null, selectionEpoch = 0;
  function clearInline() {
    document.querySelectorAll('.chunkview').forEach(n => { n._request = (n._request || 0) + 1; n.replaceChildren(); });
    document.querySelectorAll('.msg.assistant details.src').forEach(n => { n.open = false; });
    document.querySelectorAll('.msg.assistant').forEach(n => {
      n.classList.remove('answer-selected'); n.querySelector('.answer-select')?.setAttribute('aria-pressed','false');
      n.querySelectorAll('.ref').forEach(ref => ref.removeAttribute('aria-current'));
    });
  }
  function evidenceContext(owner) { return {owner, epoch: selectionEpoch}; }
  function evidenceContextCurrent(context) {
    return context.owner === selectedAnswer && context.epoch === selectionEpoch && context.owner?.isConnected;
  }
  function bindAnswer(owner, items, version, status) {
    owner._selectAnswer = (force = false) => {
      if (owner === selectedAnswer && !force) return;
      setSources(items, version, status, owner);
    };
    owner._selectAnswer();
  }
  function invalidateOriginals(message) {
    evidenceRequest++; selectionEpoch++; clearInline();
    if (selectedAnswer) { selectedAnswer.classList.add('answer-selected'); selectedAnswer.querySelector('.answer-select')?.setAttribute('aria-pressed','true'); }
    $('evidenceDetail').replaceChildren(node('div','context-error',message));
  }
  const node = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  };
  function empty(target, title, description) {
    const box = node('div', 'empty-state');
    box.append(node('strong', null, title), node('span', null, description));
    target.replaceChildren(box);
  }
  function list(target, pairs) {
    const dl = node('dl', 'detail-list');
    for (const [label, value] of pairs) {
      if (value == null || value === '') continue;
      const row = node('div'); row.append(node('dt', null, label), node('dd', null, value)); dl.append(row);
    }
    target.append(dl);
  }
  function showPanel(id) {
    openDrawer('context-open', document.querySelector('#view-qa aside.right'));
    $(id).scrollIntoView({ block: 'nearest' });
  }
  let drawerTrigger = null, drawerPanel = null;
  function openDrawer(className, panel) {
    closeDrawer(false); drawerTrigger = document.activeElement; drawerPanel = panel;
    document.body.classList.add(className);
    if ($('workspaceMask')) $('workspaceMask').hidden = false;
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true');
    panel.querySelector('button, input, select, summary')?.focus();
  }
  function closeDrawer(restore = true) {
    document.body.classList.remove('context-open', 'mobile-menu');
    if ($('workspaceMask')) $('workspaceMask').hidden = true;
    if (drawerPanel) { drawerPanel.removeAttribute('role'); drawerPanel.removeAttribute('aria-modal'); }
    drawerPanel = null; if (restore) drawerTrigger?.focus();
  }
  function reset() {
    evidenceRequest++; contextRequest++; sources = []; selectedAnswer = null; selectionEpoch++;
    clearInline();
    document.querySelectorAll('.chunkview').forEach(n => { n._request = (n._request || 0) + 1; n.replaceChildren(); });
    $('evidenceCount').textContent = $('contextEvidenceCount').textContent = '0';
    $('evidenceDetail').replaceChildren();
    empty($('evidenceList'), '还没有引用来源', '回答完成后，引用来源会显示在这里。');
    empty($('businessContent'), '尚未关联业务对象', '输入告警或设备ID，查看授权范围内的上下文。');
    $('contextLookup').disabled = false;
    $('dKb').textContent = '—';
    $('dSess').textContent = config.session().slice(0, 8) + '…';
    $('answerDiag').textContent = '等待首次提问';
    $('pipeDiag').textContent = '等待提问…';
    $('perfDiag').textContent = '尚未测量';
  }
  function setSources(items, version, status, owner = null) {
    selectionEpoch++; clearInline(); selectedAnswer = owner;
    if (owner) { owner.classList.add('answer-selected'); owner.querySelector('.answer-select')?.setAttribute('aria-pressed','true'); }
    $('dKb').textContent = version || '—';
    evidenceRequest++; $('evidenceDetail').replaceChildren();
    // Refusal and incomplete answers must not keep a prior answer's evidence panel.
    sources = ['REFUSED', 'OUT_OF_SCOPE', 'NO_EVIDENCE', 'NEEDS_CLARIFICATION', 'FAILED'].includes(status)
      ? [] : Array.isArray(items) ? items : [];
    $('evidenceCount').textContent = $('contextEvidenceCount').textContent = sources.length;
    $('evidenceList').replaceChildren();
    if (!sources.length) {
      const text = status === 'NEEDS_CLARIFICATION' ? '补充问题中的设备、版本或故障条件后再提问。'
        : status === 'ANSWERED' ? '本次回答没有知识引用。' : '本次请求没有可查看的引用。';
      empty($('evidenceList'), '暂无引用证据', text); return;
    }
    sources.forEach((source, i) => {
      const card = node('div', 'evidence-card'), heading = node('div', 'evidence-card-heading');
      heading.append(node('span', 'evidence-number', String(i + 1).padStart(2, '0')),
        node('strong', null, source.source || source.doc_id || '知识片段'));
      const button = node('button', null, '查看原文 ↗'); button.type = 'button';
      button.onclick = () => openSource(source);
      card.append(heading, node('p', null, '知识版本：' + (source.kb_version || version || '未提供')), button);
      $('evidenceList').append(card);
    });
  }
  async function openSource(source) {
    if (!sources.includes(source)) return;
    const index = sources.indexOf(source);
    document.querySelectorAll('#evidenceList .evidence-card').forEach((card,i)=>card.classList.toggle('evidence-selected',i===index));
    selectedAnswer?.querySelectorAll('.ref').forEach(ref=>{
      if (Number(ref.textContent.replace(/\D/g,''))===index+1) ref.setAttribute('aria-current','true');
      else ref.removeAttribute('aria-current');
    });
    showPanel('evidencePanel');
    const request = ++evidenceRequest, key = config.key(), session = config.session();
    const target = $('evidenceDetail'); target.replaceChildren(node('div', 'empty-state', '正在重新校验权限并读取原文…'));
    try {
      const group = await EvidenceViewer.loadAll(source, config.headers());
      if (request !== evidenceRequest || key !== config.key() || session !== config.session()) return;
      target.replaceChildren(node('div', 'verified-label', '✓ 本次读取已通过权限校验'));
      for (const evidence of group) {
        const detail = node('div', 'evidence-card');
        detail.append(node('strong', null, evidence.source || '原文片段'));
        list(detail, [['知识版本', evidence.kb_version], ['来源修订', evidence.source_revision],
          ['原文位置', EvidenceViewer.location(evidence.location)],
          ['资料属性', evidence.synthetic ? '模拟 / 自编资料' : evidence.origin_type || '未提供']]);
        detail.append(node('div', 'evidence-review', EvidenceViewer.reviewNotice(evidence)));
        detail.append(node('div', 'evidence-original', evidence.text)); target.append(detail);
      }
    } catch (error) {
      if (request === evidenceRequest && key === config.key() && session === config.session())
        invalidateOriginals(error.message || '原文读取失败，请稍后手动重试。');
    }
  }
  async function lookupContext() {
    const kind = $('contextKind').value, id = $('contextId').value.trim();
    if (!id) return;
    hideSuggest();
    const request = ++contextRequest, key = config.key(), session = config.session();
    $('contextLookup').disabled = true;
    const target = $('businessContent'); target.replaceChildren(node('div', 'empty-state', '正在读取授权范围内的上下文…'));
    try {
      const r = await fetch(`/domain/${kind === 'alarm' ? 'alarms' : 'devices'}/${encodeURIComponent(id)}/context`,
        { headers: config.headers(), cache: 'no-store' });
      if (!r.ok) throw new Error(({401:'请先设置有效的 API Key。',403:'当前账号无权读取此业务上下文。',
        404:'未找到该对象，或不在当前授权范围内。',429:'查询过于频繁，请稍后重试。',503:'业务上下文暂不可用，请稍后重试。'})[r.status] || '上下文查询失败。');
      const data = await r.json();
      if (request !== contextRequest || key !== config.key() || session !== config.session()) return;
      target.replaceChildren();
      if (data.alarm) {
        list(target, [['告警ID',data.alarm.alarm_id],['告警码',data.alarm.alarm_code],['级别',data.alarm.severity],
          ['状态',data.alarm.status],['来源系统',data.alarm.source_system],['发生时间',data.alarm.occurred_at]]);
        if (/mock|simulat|synthetic/i.test(data.alarm.source_system || ''))
          target.append(node('p','context-note','模拟系统来源，用于本地场景验证。'));
      }
      if (data.device) {
        target.append(node('div','context-section-label','设备信息'));
        list(target, [['设备ID',data.device.device_id],['设备名称',data.device.name],['类型',data.device.device_type],
          ['厂家 / 型号',[data.device.vendor,data.device.model].filter(Boolean).join(' / ')],
          ['软件版本',data.device.software_version],['设备状态',data.device.status]]);
      }
      if (!data.alarm && !data.device) empty(target,'没有关联对象','当前接口没有返回告警或设备信息。');
      const counts = [['告警',data.alarms],['监测记录',data.monitoring],['关联工单',data.work_orders],['变更记录',data.changes],['可见知识',data.knowledge]];
      list(target,counts.filter(([,a])=>Array.isArray(a)).map(([label,a])=>[label,`${a.length} 条`]));
    } catch (error) {
      if (request === contextRequest && key === config.key() && session === config.session())
        target.replaceChildren(node('div','context-error',error.message || '上下文查询失败。'));
    } finally {
      if (request === contextRequest) $('contextLookup').disabled = false;
    }
  }
  let recommendations = [], recommendationVersion = '', recommendationNote = '正在加载当前可读问题…';
  function fillQuestion(question) {
    if ($('send').disabled) return;
    $('q').value = question; $('q').dispatchEvent(new Event('input')); $('q').focus();
  }
  function populateRecommendations(grid) {
    grid.replaceChildren();
    if (!recommendations.length) { grid.append(node('p', 'welcome-note', recommendationNote)); return; }
    for (const item of recommendations.slice(0, 3)) {
      const b = node('button', 'prompt-card'); b.type = 'button';
      b.append(node('strong', null, item.query), node('small', null, '选择后填入问题 →'));
      b.onclick = () => fillQuestion(item.query); grid.append(b);
    }
  }
  function setRecommendations(items, version, note) {
    recommendations = items; recommendationVersion = version || '';
    recommendationNote = note || '当前知识库暂无可推荐问题。';
    document.querySelectorAll('.welcome .prompt-grid').forEach(populateRecommendations);
    document.querySelectorAll('.welcome-version').forEach(n => n.textContent = recommendationVersion ? '当前可读版本：' + recommendationVersion : recommendationNote);
  }
  function welcome() {
    const wrapper = node('div', 'welcome');
    wrapper.append(node('div','welcome-emblem','◇'),
      node('h2',null,'从问题出发，找到可靠依据'), node('p',null,'描述设备、故障现象或知识问题。'));
    const grid = node('div','prompt-grid'); populateRecommendations(grid);
    wrapper.append(grid, node('div','welcome-note','以下问题来自当前可读知识。资料不足时会明确提示，不替代现场判断。'));
    return wrapper;
  }
  let suggestRequest = 0, suggestIndex = -1, suggestItems = [], suggestTimer = 0;
  function hideSuggest() {
    const box = $('contextSuggest');
    if (!box) return;
    box.hidden = true; box.replaceChildren();
    $('contextId').setAttribute('aria-expanded', 'false');
    $('contextId').removeAttribute('aria-activedescendant');
    suggestItems = []; suggestIndex = -1;
  }
  function renderSuggest(items) {
    const box = $('contextSuggest'), input = $('contextId');
    box.replaceChildren();
    suggestItems = items; suggestIndex = -1;
    if (!items.length) { hideSuggest(); return; }
    items.forEach((item, i) => {
      const option = node('div', 'suggest-item');
      option.id = 'suggestOption' + i;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      const alarm = item.kind === 'alarm';
      option.append(node('strong', null, alarm ? `${item.alarm_id} · ${item.alarm_code || '—'}` : `${item.device_id} · ${item.name || '—'}`));
      option.append(node('span', 'suggest-meta', alarm
        ? [item.severity, item.status].filter(Boolean).join(' · ')
        : [item.device_type].filter(Boolean).join(' ')));
      option.addEventListener('mousedown', e => { e.preventDefault(); applySuggest(i); });
      box.append(option);
    });
    box.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }
  function applySuggest(i) {
    const item = suggestItems[i];
    if (!item) return;
    $('contextId').value = item.kind === 'alarm' ? item.alarm_id : item.device_id;
    hideSuggest();
    lookupContext();
  }
  function moveSuggest(delta) {
    if (!suggestItems.length) return;
    suggestIndex = (suggestIndex + delta + suggestItems.length) % suggestItems.length;
    const options = [...$('contextSuggest').children];
    options.forEach((o, i) => o.setAttribute('aria-selected', String(i === suggestIndex)));
    $('contextId').setAttribute('aria-activedescendant', 'suggestOption' + suggestIndex);
    options[suggestIndex]?.scrollIntoView({ block: 'nearest' });
  }
  async function fetchSuggest() {
    const kind = $('contextKind').value, q = $('contextId').value.trim();
    if (q.length < 2) { hideSuggest(); return; }
    const request = ++suggestRequest;
    try {
      const r = await fetch(`/domain/suggest?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(q)}&limit=8`,
        { headers: config.headers(), cache: 'no-store' });
      if (!r.ok) { if (request === suggestRequest) hideSuggest(); return; }
      const data = await r.json();
      if (request !== suggestRequest || kind !== $('contextKind').value) return;
      renderSuggest((data.items || []).map(item => ({ ...item, kind })));
    } catch { if (request === suggestRequest) hideSuggest(); }
  }
  function bindContextSuggest() {
    const input = $('contextId');
    input.addEventListener('input', () => {
      clearTimeout(suggestTimer);
      suggestTimer = setTimeout(fetchSuggest, 200);
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (!suggestItems.length) fetchSuggest(); else moveSuggest(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); moveSuggest(-1); }
      else if (e.key === 'Enter' && suggestIndex >= 0) { e.preventDefault(); applySuggest(suggestIndex); }
      else if (e.key === 'Escape') hideSuggest();
    });
    input.addEventListener('blur', () => setTimeout(hideSuggest, 120));
  }
  function init(options) {
    config=options;
    const chat=$('chat');
    const syncChatGutter=()=>{const width=chat.offsetWidth-chat.clientWidth;if(chat.offsetWidth)$('view-qa').style.setProperty('--qa-scrollbar',width+'px');};
    new ResizeObserver(syncChatGutter).observe(chat);
    syncChatGutter();
    const send=$('send');
    const updateSend=()=>{send.textContent=send.disabled?'回答中…':'发送';send.setAttribute('aria-busy',String(send.disabled));};
    new MutationObserver(updateSend).observe(send,{attributes:true,attributeFilter:['disabled']});
    updateSend();
    $('contextForm').onsubmit=e=>{e.preventDefault();lookupContext();};
    $('contextKind').onchange=()=>{$('contextId').placeholder=$('contextKind').value==='alarm'?'输入告警ID':'输入设备ID';contextRequest++;hideSuggest();$('contextLookup').disabled=false;empty($('businessContent'),'尚未关联业务对象','输入ID后查询。');};
    bindContextSuggest();
    $('evidenceTab').onclick=()=>showPanel('evidencePanel');
    $('requestTab').onclick=()=>{$('requestDetails').open=true;showPanel('requestDetails');};
    $('conversationTab').onclick=()=>{document.body.classList.remove('context-open');$('q').focus();};
    $('contextClose').onclick=()=>closeDrawer();
    if ($('workspaceMask')) $('workspaceMask').onclick=()=>closeDrawer();
    $('newConversation').onclick=()=>$('newBtn').click();
    $('qaVersion').addEventListener('change',()=>{
      reset();const preview=Boolean($('qaVersion').value);$('versionNote').dataset.preview=String(preview);
      $('versionNote').textContent=preview?'管理员预览 · 不改变激活版本':'使用当前激活版本';
    });
    document.addEventListener('keydown',e=>{
      if (e.key==='Escape') closeDrawer();
      if (e.key==='Tab' && drawerPanel && !$('keyDlg').open) {
        const choices=[...drawerPanel.querySelectorAll('button,input,textarea,select,summary,a[href]')].filter(n=>!n.disabled && n.getClientRects().length);
        const first=choices[0], last=choices.at(-1);
        if (first && e.shiftKey && document.activeElement===first) {e.preventDefault();last.focus();}
        else if (last && !e.shiftKey && document.activeElement===last) {e.preventDefault();first.focus();}
      }
    });
    window.addEventListener('resize',()=>{if(drawerPanel) closeDrawer();});
    window.addEventListener('storage',e=>{if(e.key==='api_key'){reset();document.querySelectorAll('.chunkview').forEach(n=>{n._request=(n._request||0)+1;n.replaceChildren();});}});
  }
  return {init,reset,setSources,openSource,welcome,showPanel,fillQuestion,setRecommendations,openDrawer,closeDrawer,bindAnswer,evidenceContext,evidenceContextCurrent,invalidateOriginals};
})();
