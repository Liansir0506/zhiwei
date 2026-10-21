/* 诊断工作台：单次受控只读诊断。5 阶段进度 + 证据账本 + 工具轨迹 + 人工复核 + 报告导出。
   8010（主站）与 18021（隔离只读工作区）共用本模块；元素 ID 两端一致。 */
window.AgentWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const element = (tag, cls, text) => {
    const item = document.createElement(tag); if (cls) item.className = cls;
    if (text !== undefined) item.textContent = String(text); return item;
  };
  const statuses = {ANSWERED: '诊断判断已生成', NO_EVIDENCE: '当前没有足够证据',
    NEEDS_CLARIFICATION: '需要补充条件', OUT_OF_SCOPE: '请求超出只读范围', FAILED: '诊断已停止', DEGRADED: '诊断降级'};
  const reasons = {evidence_ready: '引用已在结束前重新读取核对。', rule_context_ready: '基于授权业务快照生成判断；候选原因需人工确认。', missing_entity: '请提供告警事件 ID 或设备 ID；告警码不能代替事件 ID。',
    entity_unavailable: '实体不存在或不在授权范围内，无法读取现场快照。', entity_mismatch: '请核对告警与设备的对应关系。',
    missing_safety_controls: '流程缺少审批或回滚条件，当前不能提供该流程。', insufficient_evidence: '已读取的资料不足以支持回答。',
    unsafe_request: '诊断入口仅支持只读查询。', prompt_injection: '请求触发了安全规则。',
    timeout: '已达到服务时间预算。工具可能仍在退出，请稍后手动重试。', cancelled: '本次诊断已取消。',
    audit_unavailable: '审计服务不可用，结果已停止发布。', tool_error: '读取工具发生错误，未发布结果。',
    binding_changed: '知识版本或读取绑定变化，请重新提交。', access_changed: '访问权限变化，请检查凭据后重新提交。'};
  const httpErrors = {401: '请先设置有效的 API Key。', 403: '当前凭据无只读诊断权限。',
    422: '输入不符合要求，请核对问题与实体 ID。', 429: '请求过于频繁或诊断工作槽位已满，请稍后手动重试。',
    503: '只读诊断未启用或服务暂不可用。'};
  const toolNames = {get_alarm_context: '读取告警快照', get_device_context: '读取设备快照', search_evidence: '搜索知识证据', read_evidence: '读取原文',
    build_diagnosis: '生成规则判断'};
  const phases = {context: '实体读取', search: '证据搜索', read: '原文初读', verify: '结束核对'};
  // 五阶段：前四阶段对应服务端 trace 的 phase，第五阶段为人工复核（本地）。
  const stageOrder = ['scope', 'candidate', 'collect', 'verify', 'review'];
  const stageLabels = {scope: '① 范围', candidate: '② 候选', collect: '③ 取证', verify: '④ 校验', review: '⑤ 复核'};
  const phaseToStage = {context: 'scope', search: 'candidate', read: 'collect', verify: 'verify'};
  const reviewKey = () => {
    // 按身份分区：同一浏览器切换账号时不会看到上一身份的笔记
    // 用当前凭据的短指纹分区（不存明文、不新增凭据落盘点）
    let fingerprint = 'anonymous';
    try {
      const key = config && config.key ? config.key() : '';
      if (key) {
        let hash = 0;
        for (let index = 0; index < key.length; index++) hash = (hash * 31 + key.charCodeAt(index)) >>> 0;
        fingerprint = hash.toString(16);
      }
    } catch { }
    return 'diagnosis_reviews_v1:' + fingerprint;
  };
  let reviewStore = 'diagnosis_reviews_v1:anonymous';
  let config, enabled = false, sequence = 0, active = null, lastResult = null;

  let pendingFill = null;
  function applyFill() {
    if (!pendingFill) return;
    const values = pendingFill; pendingFill = null;
    if (typeof values.query === 'string' && values.query) $('agentQuery').value = values.query;
    if (typeof values.alarm_id === 'string' && values.alarm_id) $('agentAlarm').value = values.alarm_id;
    if (typeof values.device_id === 'string' && values.device_id) $('agentDevice').value = values.device_id;
    if ((values.alarm_id || values.device_id) && $('agentContext')) $('agentContext').open = true;
    if (values.query || values.alarm_id || values.device_id) $('agentQuery').focus();
    if ((values.query || values.alarm_id || values.device_id) && $('agentEntry')) $('agentEntry').open = true;
  }
  function availability() {
    const restricted = $('agentRestricted'), workspace = $('agentWorkspace');
    // 主入口始终可用：未启用检索型诊断时自动使用规则诊断（不调用模型）。
    if (restricted) restricted.hidden = true;
    if (workspace) workspace.hidden = false;
    const ruleNotice = $('agentRuleNotice');
    if (ruleNotice) ruleNotice.hidden = enabled;
    if ($('agentContext') && (!enabled || $('agentAlarm').value || $('agentDevice').value)) $('agentContext').open = true;
    const contextNote=document.querySelector('#agentForm .agent-form-note');
    if(contextNote)contextNote.textContent=enabled?'关联对象后核对业务记录；不填对象时仅检索知识。每次诊断独立。':'规则诊断需要具体告警事件 ID 或设备 ID，告警码不能代替事件 ID。';
    const status = $('agentAvailability');
    if (status) status.textContent = enabled
      ? (!config.key() ? '检索型诊断已启用 · 请先设置 API Key 后提交' : '检索型诊断已启用 · 仅使用当前激活版本')
      : (config.key() ? '规则诊断可用（不调用模型）· 需告警或设备 ID' : '规则诊断可用 · 请先设置 API Key');
    const blocked = !config.key() || Boolean(active);
    const submit = $('agentSubmit');
    if (submit) {
      submit.disabled = blocked;
      submit.setAttribute('aria-busy',String(Boolean(active)));
      submit.textContent = enabled ? '开始诊断' : '开始规则诊断';
      submit.title = !blocked ? '' : !config.key() ? '请先设置有效的 API Key' : '已有诊断在进行中，请等待或取消';
    }
    const cancel = $('agentCancel');
    if (cancel) cancel.hidden = !active;
    applyFill();
  }
  function standby() {
    // 切到其他板块：取消进行中的等待，但保留已出的结果与输入草稿（返回后可继续）
    if (active) { active.controller.abort(); clearInterval(active.timer); clearTimeout(active.deadline); active = null; }
    ++sequence;
  }

  function reset(clearDraft = true) {
    ++sequence;
    if (active) { active.controller.abort(); clearInterval(active.timer); clearTimeout(active.deadline); active = null; }
    if (!config) return;
    if(clearDraft && $('agentEntry')) { $('agentEntry').open=true; delete $('agentEntry').dataset.hasResult; }
    $('agentResult').replaceChildren(); $('agentResult').hidden = true; delete $('agentResult').dataset.status;
    $('agentProgress').textContent = '';
    const bar = $('agentStages'); if (bar) { bar.replaceChildren(); bar.hidden = true; }
    const review = $('agentReview'); if (review) { review.replaceChildren(); review.hidden = true; }
    if (clearDraft) for (const id of ['agentQuery', 'agentAlarm', 'agentDevice']) $(id).value = '';
    lastResult = null;
    availability();
  }
  function capability(value) { if (!value && enabled) reset(); enabled = value; availability(); }
  function current(run) { return run.sequence === sequence && run.key === config.key(); }
  function card(title) { const out = element('section', 'agent-card'); out.append(element('h3', '', title)); return out; }

  /* ---------- 五阶段进度 ---------- */
  function renderStages(data, run) {
    const bar = $('agentStages'); if (!bar) return;
    bar.replaceChildren(); bar.hidden = false;
    const seen = new Set();
    for (const event of data.trace || []) { const stage = phaseToStage[event.phase]; if (stage) seen.add(stage); }
    for (const key of stageOrder) {
      const step = element('div', 'agent-stage' + (seen.has(key) ? ' done' : key==='review'?' is-current':''));
      step.append(element('span', 'agent-stage-dot', seen.has(key) ? '✓' : '·'), element('span', 'agent-stage-label', stageLabels[key]));
      if (key === 'scope') step.title = '确认实体与读取范围';
      if (key === 'candidate') step.title = '检索候选证据';
      if (key === 'collect') step.title = '读取原文与实体观测';
      if (key === 'verify') step.title = '结束前重新核对引用';
      if (key === 'review') step.title = '人工复核结论（本地保存，可导出）';
      bar.append(step);
    }
    bar.append(element('span', 'agent-stage-note',
      (data.trace || []).length
        ? `已结束 · 按 ${data.trace.length} 条工具记录回看，人工复核待完成`
        : '本次没有工具事件'));
  }

  /* ---------- 人工复核 ---------- */
  function loadReviews() { try { return JSON.parse(localStorage.getItem(reviewStore) || '{}'); } catch { return {}; } }
  function syncReviewStore() { reviewStore = reviewKey(); }
  function saveReview(runId, value) {
    const all = loadReviews(); all[runId] = {...value, saved_at: new Date().toISOString()};
    localStorage.setItem(reviewStore, JSON.stringify(all));
  }
  function renderReview(data, run) {
    const host = $('agentReview'); if (!host) return;
    host.replaceChildren(); host.hidden = false;
    const existing = loadReviews()[data.run_id || ''] || {};
    host.append(element('h3', '', '核对结果，选择下一步'));
    host.append(element('p', 'ui-help',
      '确认可用后才可写入告警。需要补证或驳回时，请先完善条件；本机笔记与正式确认分别保留。'));
    const form = element('div', 'agent-review-form');
    const choices = [['confirmed', '确认可用'], ['need_more', '需补充证据'], ['rejected', '驳回']];
    const radios = element('div', 'agent-review-choices');
    let picked = existing.conclusion || '';
    for (const [value, label] of choices) {
      const item = element('label', 'agent-review-choice');
      const input = element('input'); input.type = 'radio'; input.name = 'agentReview'; input.value = value; input.checked = picked === value;
      input.onchange = () => { picked = value; };
      item.append(input, element('span', '', label)); radios.append(item);
    }
    form.append(radios);
    const note = element('textarea'); note.id = 'agentReviewNote'; note.rows = 2; note.maxLength = 500;
    note.placeholder = '复核备注：缺什么证据、下一步做什么（可选）'; note.value = existing.note || '';
    form.append(note);
    const actions = element('div', 'action-row');
    const alarmId = (data.request_context && data.request_context.alarm_id) || '';
    if (alarmId && data.run_id) {
      const confirm = element('button', 'ui-btn primary', '正式确认到告警'); confirm.type = 'button';
      let recorded=false;
      confirm.title = '仅当复核结论为「确认可用」时写入服务端处理账本（绑定告警 ' + alarmId + ' 与本次 run）';
      confirm.onclick = async () => {
        // 门禁：只有明确"确认可用"才能写人工确认；驳回/需补证据不进告警报本。
        if (picked !== 'confirmed') {
          $('agentProgress').textContent = picked === 'rejected'
            ? '当前复核结论是「驳回」：请修订条件后重新诊断；驳回结论不会写入告警处理记录。'
            : '当前复核结论是「需补充证据」：请补充条件重新诊断，复核确认可用后再正式确认到告警。';
          return;
        }
        confirm.disabled = true;
        try {
          const response = await fetch('/alerts/' + encodeURIComponent(alarmId) + '/handling', {
            method: 'POST', headers: { 'Content-Type': 'application/json', ...config.headers() },
            body: JSON.stringify({ action: 'confirm', note: note.value.trim() || '诊断证据核对后确认', run_id: data.run_id }),
            cache: 'no-store' });
          const payload = await response.json().catch(() => ({}));
          $('agentProgress').textContent = response.ok
            ? '已写入告警处理记录：' + (payload.action_label || '人工确认') + '，处理状态转为「' + (payload.state_label || '处理中') + '」（可在告警详情查看时间线与下一步）'
            : (payload.detail || '写入失败（HTTP ' + response.status + '）');
          if(response.ok){
            recorded=true;confirm.classList.remove('primary');confirm.textContent='已写入告警';
            const next=element('button','ui-btn primary','去告警继续处理');next.type='button';
            next.onclick=()=>{window.switchTab?.('alerts');window.AlertsWorkspace?.open(alarmId);};actions.prepend(next);
          }
        } catch (error) {
          $('agentProgress').textContent = '写入失败：' + error.message;
        } finally {
          confirm.disabled = recorded;
        }
      };
      actions.append(confirm);
    }
    const save = element('button', 'ui-btn', '保存本机笔记'); save.type = 'button';
    save.onclick = () => {
      if (!picked) { $('agentProgress').textContent = '请先选择复核结论。'; return; }
      saveReview(data.run_id || '', {conclusion: picked, note: note.value.trim()});
      $('agentProgress').textContent = `复核结论已保存（${choices.find(c => c[0] === picked)[1]}）。`;
    };
    const report = element('button', 'ui-btn', '导出报告'); report.type = 'button';
    report.onclick = () => exportReport(data, picked, note.value.trim());
    actions.append(save, report); form.append(actions); host.append(form);
  }
  function exportReport(data, conclusion, note) {
    const lines = [];
    lines.push('# 只读诊断报告', '');
    lines.push(`- run_id：${data.run_id || '—'}`);
    lines.push(`- 状态：${statuses[data.status] || data.status}（${data.stop_reason || '—'}）`);
    lines.push(`- 模式：${data.mode === 'rule' ? '规则诊断（业务快照）' : data.mode === 'domain' ? '实体快照' : '知识'} · 知识版本：${data.kb_version || '未使用'}`);
    const budget = data.budget || {};
    lines.push(`- 工具调用：${budget.calls_used ?? 0} 次 · 服务耗时 ${((budget.elapsed_ms || 0) / 1000).toFixed(1)} 秒`, '');
    const ctx = data.request_context || {};
    lines.push('## 问题与实体', '', `- 问题：${ctx.query || '—'}`,
      `- 告警 ID：${ctx.alarm_id || '—'}`, `- 设备 ID：${ctx.device_id || '—'}`, '');
    lines.push('## 摘录', '', data.status === 'ANSWERED' ? (data.answer || '') : '（本次没有发布知识摘录）', '');
    if ((data.sources || []).length) {
      lines.push('## 引用来源', '');
      data.sources.forEach((s, i) => lines.push(`${i + 1}. ${s.source_name || s.source || s.document_id || '知识原文'}（版本 ${s.kb_version || data.kb_version || '—'}）`));
      lines.push('');
    }
    if ((data.trace || []).length) {
      lines.push('## 工具轨迹', '');
      data.trace.forEach((e, i) => lines.push(`${i + 1}. ${toolNames[e.tool] || e.tool} · ${phases[e.phase] || e.phase} · ${e.status === 'ok' ? '读取完成' : '停止 / ' + e.status} · 剩余 ${e.calls_remaining ?? '—'} 次`));
      lines.push('');
    }
    for (const text of data.limitations || []) lines.push(`> 限制：${text}`, '');
    lines.push('## 人工复核', '', `- 结论：${conclusion ? {confirmed: '确认可用', need_more: '需补充证据', rejected: '驳回'}[conclusion] : '（未选择）'}`,
      `- 备注：${note || '（无）'}`, `- 导出时间：${new Date().toISOString()}`, '');
    const blob = new Blob([lines.join('\n')], {type: 'text/markdown;charset=utf-8'});
    const link = element('a'); link.href = URL.createObjectURL(blob);
    link.download = `diagnosis-${data.run_id || 'report'}.md`; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 4000);
  }

  function renderJudgment(host, d) {
    const scope = d.scope || {};
    host.append(element('p', 'agent-scope-line',
      `事件范围：${scope.alarm_code || '—'} @ ${scope.device_id || '—'}（${scope.device_type || '—'}）· 发生于 ${scope.occurred_at || '—'}` +
      (scope.evidence_window ? ` · 证据窗口 ${String(scope.evidence_window.from).slice(0, 16)} ~ ${String(scope.evidence_window.to).slice(0, 16)}` : '')));
    host.append(element('p', 'agent-scenario-note', '判断规则：' + (scope.scenario_note || '—')));
    // 事实
    const factBox = element('div', 'agent-facts');
    factBox.append(element('h4', '', '事实与异常'));
    for (const fact of d.facts || []) {
      factBox.append(element('p', 'agent-fact' + (/异常|缺失/.test(fact.text) ? ' is-flag' : ''),
        `${fact.id} ${fact.text}`));
    }
    host.append(factBox);
    // 候选
    const verdictMeta = {likely: ['ok', '倾向支持'], possible: ['warn', '可能相关'], unlikely: ['muted', '倾向排除']};
    for (const c of d.candidates || []) {
      const box = element('div', 'agent-candidate');
      const [cls, label] = verdictMeta[c.verdict] || ['muted', c.verdict];
      const head = element('div', 'agent-candidate-head');
      head.append(element('strong', '', c.title), element('span', 'pill ' + cls, label));
      box.append(head);
      for (const s of c.support || []) box.append(element('p', 'agent-cta is-support', '支持 · ' + s));
      for (const a of c.against || []) box.append(element('p', 'agent-cta is-against', '反对 · ' + a));
      for (const m of c.missing || []) box.append(element('p', 'agent-cta is-missing', '缺失 · ' + m));
      box.append(element('p', 'agent-next-check', '下一步：' + (c.next_checks || []).join('；')));
      host.append(box);
    }
    if ((d.missing_info || []).length) {
      host.append(element('p', 'agent-missing-line', '缺失信息：' + d.missing_info.join('；')));
    }
    host.append(element('p', 'agent-recommendation', d.recommendation || ''));
  }

  function render(data, run) {
    if (!statuses[data.status] || !Array.isArray(data.sources) || !Array.isArray(data.trace) || !Array.isArray(data.observations))
      throw new Error('诊断响应格式异常，请稍后手动重试。');
    lastResult = data;
    const root = $('agentResult'); root.replaceChildren(); root.hidden = false; root.dataset.status = data.status;
    renderStages(data, run);
    const summary = element('section', 'agent-result-summary'); summary.dataset.status = data.status;
    summary.append(element('h2', '', statuses[data.status]), element('p', '', reasons[data.stop_reason] || '诊断在当前条件下结束，详情见停止代码。'));
    const budget = data.budget || {};
    if (data.status === 'NEEDS_CLARIFICATION' && data.answer) summary.append(element('p', '', data.answer));
    const details = element('details','tech-details'); details.append(element('summary', '', '模式、版本与运行详情'), element('p', '', `run_id：${data.run_id || '—'} · ${data.stop_reason || '—'}`), element('p', '', `${data.mode === 'rule' ? '规则诊断（不调用模型）' : data.mode === 'domain' ? '实体快照模式' : '知识模式'} · 知识版本：${data.kb_version || '未使用'} · 工具调用 ${budget.calls_used ?? 0} 次 / 剩余 ${budget.calls_remaining ?? 0} 次 · 服务耗时 ${((budget.elapsed_ms || 0) / 1000).toFixed(1)} 秒`)); summary.append(details);
    root.append(summary);
    if (data.status === 'ANSWERED' && data.retrieval_scope) {
      const scope = card('检索范围'); scope.classList.add('agent-retrieval-scope');
      scope.append(element('p', '', data.retrieval_scope.description));
      if (data.retrieval_scope.ticket_id) scope.append(element('p', '', `关联工单：${data.retrieval_scope.ticket_id}`));
      root.append(scope);
    }
    const grid = element('div', 'agent-result-grid'), answer = card('诊断判断'), side = element('div','agent-support-list'); answer.classList.add('agent-answer-card');
    if (data.status === 'ANSWERED' && data.diagnosis && data.diagnosis.candidates) {
      // 结构化判断：事实→候选→缺失→下一步（候选只有倾向，根因由人工确认）
      renderJudgment(answer, data.diagnosis);
      const full = element('details', 'tech-details');
      full.append(element('summary', '', '完整文本与知识摘录'), element('div', 'agent-answer', data.answer));
      answer.append(full);
    } else {
      answer.append(element('div', 'agent-answer', data.status === 'ANSWERED' ? data.answer : '本次没有发布知识摘录。'));
    }
    const citations = card('证据账本');
    // Only a completed answer may publish evidence. Every open performs fresh auth.
    const sources = data.status === 'ANSWERED' ? data.sources : [];
    if (!sources.length) citations.append(element('p', '', '本次没有可展示的引用。'));
    sources.forEach((source, index) => {
      const item = element('div', 'evidence-card');
      item.append(element('strong', '', `[${index + 1}] ${source.source_name || source.source || source.document_id || '知识原文'}`),
        element('p', '', `版本：${source.kb_version || data.kb_version} · ${source.synthetic ? '模拟资料，仅限本地演示' : '来源属性以原文为准'}`));
      const open = element('button', '', '重新核对原文'), body = element('div'); let reading = 0;
      open.type = 'button';
      open.onclick = async () => {
        const read = ++reading; body.replaceChildren(element('p', '', '正在重新核对版本与访问权限…')); open.disabled = true;
        try {
          const records = await EvidenceViewer.loadAll(source, config.headers());
          if (lastResult !== data || run.key !== config.key() || read !== reading) return;
          body.replaceChildren();
          for (const record of records) {
            body.append(element('p', '', `来源修订：${record.source_revision || '—'} · ${record.synthetic ? '模拟资料' : '知识原文'}`),
              element('p', 'context-note', EvidenceViewer.location(record.location)));
            const notice = EvidenceViewer.reviewNotice(record); if (notice) body.append(element('p', 'context-note', notice));
            body.append(element('div', 'evidence-original', record.text));
          }
        } catch (error) { if (lastResult === data && run.key === config.key() && read === reading) body.replaceChildren(element('p', 'context-error', error.message)); }
        finally { if (lastResult === data && run.key === config.key() && read === reading) open.disabled = false; }
      };
      item.append(open, body); citations.append(item);
    });
    const trace = card('工具轨迹');
    trace.append(element('p', 'context-note', '以下为服务端返回的工具事件，不表示设备或业务动作已执行。'));
    const list = element('ol', 'agent-trace');
    if (!data.trace.length) trace.append(element('p', '', '本次没有调用工具。'));
    data.trace.forEach((event, index) => {
      const li = element('li', '', `${index + 1}. ${toolNames[event.tool] || event.tool} · ${phases[event.phase] || event.phase}`);
      li.append(element('small', '', `${event.status === 'ok' ? '读取完成' : '停止 / ' + event.status} · ${Number(event.elapsed_ms || 0).toFixed(0)} ms · 剩余 ${event.calls_remaining ?? event.remaining ?? '—'} 次`)); list.append(li);
    }); trace.append(list);
    for(const [label,content] of [['查看知识依据 · '+sources.length+' 条',citations],['查看工具读取记录',trace]]){const detail=element('details','agent-support');detail.append(element('summary','',label),content);side.append(detail);}
    grid.append(answer, side); root.append(grid);
    const observations = card('实体观测');
    const items = data.status === 'ANSWERED' ? data.observations : [];
    if (!items.length) observations.append(element('p', '', '本次没有发布实体观测；知识引用不代表现场事实。'));
    items.forEach(item => {
      const detail = element('details'); const ref = item.observation_ref || {};
      detail.append(element('summary', '', `${ref.entity_type || '实体'} · ${ref.entity_id || '—'}${item.synthetic ? ' · 模拟快照' : ''}`),
        element('p', '', `来源系统：${item.source_system || '未知'} · 记录时间：${item.recorded_at || '未知'} · 读取时间：${item.retrieved_at || '未知'} · 时效：${item.freshness === 'snapshot' ? '快照' : '未知'}`),
        element('p', '', `观测出处：${ref.call_id || '—'}`));
      const fields = element('dl', 'detail-list');
      Object.entries(item.fields || {}).forEach(([key, value]) => { const row = element('div'); row.append(element('dt', '', key), element('dd', '', value == null ? '未知' : typeof value === 'object' ? JSON.stringify(value) : value)); fields.append(row); });
      detail.append(fields); observations.append(detail);
    });
    const observationDetails=element('details','agent-support');observationDetails.append(element('summary','','查看关联对象与读取时间 · '+items.length+' 条'),observations);root.append(observationDetails);
    if (items.length) observations.append(element('p', 'context-note', '本批业务时间按带时区的原始值换算为 UTC 保存；设备和变更未提供观测记录时间，仍显示未知。'));
    for (const text of data.limitations || []) root.append(element('p', 'agent-limitations', text));
    renderReview(data, run);
    if ($('agentEntry')) { $('agentEntry').dataset.hasResult='true'; $('agentEntry').open = false; const title=$('agentEntry').querySelector('summary'); if(title)title.textContent='修改诊断条件 · '+(($('agentAlarm').value||$('agentDevice').value)||'知识问题'); }
    if (document.body.dataset.tab==='agent') $('agentStages').scrollIntoView({block:'start',behavior:'instant'});
  }
  async function submit(event) {
    event.preventDefault();
    if (active) { $('agentProgress').textContent = '已有诊断在进行中；请等待本次结束或点击“取消等待”。'; return; }
    if (!config.key()) { $('agentProgress').textContent = '请先设置有效的 API Key 再提交诊断。'; return; }
    if (!$('agentForm').reportValidity()) return;
    const query = $('agentQuery').value.trim(); if (!query) { $('agentProgress').textContent = '请填写诊断问题。'; return; }
    const body = {query}; for (const [field, id] of [['alarm_id', 'agentAlarm'], ['device_id', 'agentDevice']]) if ($(id).value.trim()) body[field] = $(id).value.trim();
    if (!enabled && !body.alarm_id && !body.device_id) {
      $('agentProgress').textContent = '规则诊断需要告警事件 ID 或设备 ID；只查知识请使用“运维问答”。';
      return;
    }
    reset(false);
    const run = {sequence, key: config.key(), controller: new AbortController(), started: Date.now(), timedOut: false}; active = run;
    const progress = () => { if (current(run)) $('agentProgress').textContent = `正在等待诊断结果 · 已等待 ${Math.floor((Date.now() - run.started) / 1000)} 秒。服务总预算 90 秒；阶段进度与工具记录将在结束后显示。`; };
    progress(); run.timer = setInterval(progress, 1000); availability();
    // Leave transport headroom above the server's 90-second budget; never resend.
    run.deadline = setTimeout(() => { run.timedOut = true; run.controller.abort(); }, 105000);
    try {
      // 未启用检索型诊断时走规则诊断（只读业务快照，不调用模型）
      const endpoint = enabled ? '/agent/diagnose' : '/agent/diagnose-rule';
      const response = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json', ...config.headers()},
        body: JSON.stringify(body), cache: 'no-store', signal: run.controller.signal});
      if (!current(run)) return;
      if (!response.ok) throw new Error(httpErrors[response.status] || '诊断服务请求失败，请稍后手动重试。');
      const data = await response.json(); if (!current(run)) return;
      // 固定本次 run 的请求上下文：正式确认/工单联动以提交时为准，不再现读输入框。
      data.request_context = {query, alarm_id: body.alarm_id || '', device_id: body.device_id || ''};
      render(data, run); $('agentProgress').textContent = '本次诊断已结束。补充条件或稍后重试需手动提交。';
    } catch (error) {
      if (!current(run)) return;
      $('agentResult').replaceChildren(); $('agentResult').hidden = true; delete $('agentResult').dataset.status;
      const bar = $('agentStages'); if (bar) { bar.replaceChildren(); bar.hidden = true; }
      const review = $('agentReview'); if (review) { review.replaceChildren(); review.hidden = true; }
      $('agentProgress').textContent = error.name === 'AbortError' ? '等待已超时，工具可能仍在退出；请稍后手动重试。'
        : error instanceof TypeError ? '网络连接中断，未取得诊断结果；请检查连接后手动重试。'
        : error instanceof SyntaxError ? '诊断响应无法读取，请稍后手动重试。' : error.message;
    } finally {
      clearInterval(run.timer); clearTimeout(run.deadline);
      if (active === run) { active = null; availability(); }
    }
  }
  function fill(values = {}) {
    pendingFill = { ...(pendingFill || {}), ...values || {} };
    applyFill();
  }
  function init(options) {
    config = options; syncReviewStore();
    window.addEventListener('storage', syncReviewStore);
    $('agentForm').addEventListener('submit', submit);
    $('agentCancel').onclick = () => { reset(false); $('agentProgress').textContent = '已取消等待并丢弃本次结果。服务端工具可能仍在退出，请稍后手动重试。'; };
    window.addEventListener('storage', event => { if (event.key === 'api_key' || event.key === null) reset(); });
    availability();
  }
  return {init, reset, standby, capability, fill};
})();
