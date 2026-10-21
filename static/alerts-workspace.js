/* 告警中心：真实 /alerts 只读接口；不写告警、不伪造状态。 */
window.AlertsWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = String(text); return n; };
  let config, epoch = 0, controller = null, active = false, page = 1, pageSize = 12;
  let last = null, selected = null, detailEpoch = 0;
  const severityClass = s => ({ critical: 'sev-critical', major: 'sev-major', minor: 'sev-minor', warning: 'sev-warning' }[s] || 'sev-warning');

  function state() { return { query: $('alertsQuery').value.trim(), severity: $('alertsSeverity').value,
    status: $('alertsStatusFilter').value, device_type: $('alertsType').value,
    occurred_from: $('alertsFrom')?.value || '', occurred_to: $('alertsTo')?.value || '' }; }

  function syncFilters(params = {}) {
    if (params.query !== undefined) $('alertsQuery').value = params.query || '';
    if (params.severity !== undefined) $('alertsSeverity').value = params.severity || '';
    if (params.status !== undefined) $('alertsStatusFilter').value = params.status || '';
    if (params.device_type !== undefined) $('alertsType').value = params.device_type || '';
    // 同范围明细：把统计窗口写入日期框，用户可继续调整
    if (params.occurred_from !== undefined && $('alertsFrom')) $('alertsFrom').value = String(params.occurred_from).slice(0, 10);
    if (params.occurred_to !== undefined && $('alertsTo')) $('alertsTo').value = String(params.occurred_to).slice(0, 10);
    if (params.page) page = Number(params.page);
  }

  function buildQuery() {
    const f = state(); const q = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
    return q.toString();
  }

  function summary(data) {
    const box = $('alertsSummary'); box.replaceChildren();
    const s = data.summary || {};
    const dataTo = s.data_to ? String(s.data_to).slice(0, 10) : '—';
    const cards = [
      ['活动告警', s.open || 0, '未处理 + 已确认', 'warn'],
      ['紧急待办', s.critical_open || 0, '需要优先处置', 'bad'],
      ['窗口新增', s.window_recent || 0, '近 7 天（截至 ' + dataTo + '）', 'info'],
      ['已清除', s.cleared || 0, '累计 ' + (s.total || 0) + ' 条', 'ok'],
    ];
    for (const [label, value, foot, tone] of cards) {
      const card = el('article', 'alert-stat', null); card.dataset.tone = tone;
      card.append(el('span', 'alert-stat-label', label), el('b', 'alert-stat-value', value), el('span', 'alert-stat-foot', foot));
      box.append(card);
    }
  }

  function fillTypes(types) {
    const select = $('alertsType'); const current = select.value;
    select.replaceChildren(new Option('全部设备类型', ''));
    for (const type of types || []) select.append(new Option(type, type));
    select.value = current;
  }

  function table(items) {
    const box = $('alertsTable'); box.replaceChildren();
    if (!items.length) {
      const empty = el('div', 'alert-empty');
      empty.append(el('strong', null, '没有符合条件的告警'), el('p', null, '清空筛选或换一个关键词再试。'));
      const reset = el('button', 'ui-btn', '清空筛选'); reset.type = 'button'; reset.onclick = () => { $('alertsFilter').reset(); page = 1; load(); };
      empty.append(reset); box.append(empty); return;
    }
    const t = el('table', 'data-table alert-table card-table');
    const head = el('thead'), hr = el('tr');
    for (const name of ['级别', '告警', '设备', '类型', '状态', '处置', '发生时间', '操作']) hr.append(el('th', null, name));
    head.append(hr); t.append(head);
    const body = el('tbody');
    for (const item of items) {
      const tr = el('tr'); tr.dataset.id = item.alarm_id; tr.tabIndex = 0; tr.style.cursor = 'pointer';
      const sev = el('td'); sev.dataset.label = '级别'; sev.append(el('span', 'severity-dot ' + severityClass(item.severity)), el('span', null, item.severity_label)); tr.append(sev);
      const alarm = el('td'); alarm.dataset.label = '告警'; alarm.append(el('strong', null, item.alarm_code), el('small', null, item.alarm_id)); tr.append(alarm);
      const device = el('td'); device.dataset.label = '设备'; device.append(el('strong', null, item.device_name), el('small', null, item.device_id)); tr.append(device);
      const typeCell = el('td', null, item.device_type); typeCell.dataset.label = '类型'; tr.append(typeCell);
      const statusCell = el('td', null, item.status_label); statusCell.dataset.label = '状态'; tr.append(statusCell);
      const handlingCell = el('td');
      handlingCell.dataset.label = '处置';
      handlingCell.append(el('span', 'pill ' + (HANDLING_STATE_PILL[item.handling_state] || 'warn'),
        item.handling_state_label || '待确认'));
      tr.append(handlingCell);
      const timeCell = el('td', 'alert-time', (item.occurred_at || '').replace('T', ' ').slice(5, 16)); timeCell.dataset.label = '时间'; tr.append(timeCell);
      const op = el('td'); const view = el('button', 'link-button', '详情'); view.type = 'button'; view.onclick = event => { event.stopPropagation(); open(item.alarm_id); };
      const diag = el('button', 'link-button', '诊断'); diag.type = 'button';
      diag.onclick = () => config.go('agent', { query: item.alarm_code + ' 常见原因', alarm_id: item.alarm_id });
      op.append(view, diag); tr.append(op);
      tr.addEventListener('click', () => open(item.alarm_id));
      tr.addEventListener('keydown', event => { if (event.key === 'Enter') open(item.alarm_id); });
      body.append(tr);
    }
    t.append(body); box.append(t);
  }

  function pagination(data) {
    $('alertsCount').textContent = '共 ' + data.total + ' 条 · 第 ' + data.page + ' / ' + data.pages + ' 页';
    $('alertsPage').textContent = data.page + ' / ' + data.pages;
    $('alertsPrev').disabled = data.page <= 1;
    $('alertsNext').disabled = data.page >= data.pages;
  }

  async function load(params = {}) {
    // 统计卡“同范围明细”：先切到对应子页签（工单/设备），不请求告警列表
    const subtab = params.subtab || params.tab;
    if (subtab && subtab !== 'alerts') {
      leave(); ++epoch;
      if (window.switchAlertPane) window.switchAlertPane(subtab, params);
      return;
    }
    leave(); active = true; const t = ++epoch; controller = new AbortController();
    syncFilters(params);
    // 返回列表时保留上次页码与筛选；只有显式传 page（翻页/新搜索）才变更
    if (params.page !== undefined) page = params.page;
    else if (params.reset === true || params.reset === 'true') page = 1;
    $('alertsStatus').textContent = '正在读取告警…';
    $('alertsTable').replaceChildren(el('div', 'alert-empty', '正在读取告警…'));
    try {
      const response = await fetch('/alerts?' + buildQuery(), { headers: config.headers(), signal: controller.signal, cache: 'no-store' });
      if (!response.ok) {
        const message = { 401: '登录已失效，请重新设置凭据。', 403: '当前账号无权查看告警。',
          404: '当前服务尚未加载告警接口：请重启平台后刷新。', 503: '告警数据暂不可用，请稍后刷新。' }[response.status] || ('读取失败（HTTP ' + response.status + '）');
        throw new Error(message);
      }
      const data = await response.json();
      if (t !== epoch || !active) return;
      last = data; summary(data); fillTypes(data.device_types); table(data.items || []); pagination(data);
      const range = data.range || {};
      const rangeText = range.from_date
        ? `当前筛选：发生时间 ${range.from_date} ~ ${range.to_date}（含首尾，共 ${data.total} 条）· `
        : '';
      $('alertsStatus').textContent = rangeText + data.scope + ' · 最近更新 ' + (data.generated_at || '').replace('T', ' ').slice(5, 16);
    } catch (error) {
      if (error.name === 'AbortError' || t !== epoch) return;
      // 错误集中到状态行一处；表格只放恢复动作，不重复同一句
      $('alertsStatus').textContent = error instanceof TypeError?'网络连接中断，告警数据未能加载；恢复连接后可手动重试。':error.message;
      const panel = el('div', 'alert-empty alert-recover');
      panel.append(el('p', null, '告警数据未能加载。'));
      const retry = el('button', 'ui-btn', '重新加载'); retry.type = 'button';
      retry.onclick = () => load();
      const toOps = el('button', 'ui-btn', '查看运行状态'); toOps.type = 'button';
      toOps.onclick = () => { if (config.go) config.go('ops', {}); };
      panel.append(el('div', 'action-row', ''));
      panel.lastChild.append(retry, toOps);
      $('alertsTable').replaceChildren(panel);
    }
  }

  function detailShell(item) {
    const wrap = el('div', 'alert-dialog-inner');
    const head = el('div', 'alert-dialog-head');
    const title = el('div'); title.append(el('h2', null, item.title || item.alarm_code), el('p', null, item.alarm_id));
    const pills = el('div', 'alert-dialog-pills');
    pills.append(el('span', 'pill ' + (item.severity === 'critical' ? 'bad' : item.severity === 'major' ? 'warn' : 'muted'), item.severity_label), el('span', 'pill muted', item.status_label));
    const handlingBadge = el('span', 'pill ' + (HANDLING_STATE_PILL[item.handling_state] || 'warn'),
      '处置：' + (item.handling_state_label || '待确认'));
    handlingBadge.id = 'alertHandlingBadge';
    pills.prepend(el('span', 'meta-line', '上游事件：')); pills.append(handlingBadge);
    head.append(title, pills); wrap.append(head);
    const body = el('div', 'alert-dialog-body');
    const grid = el('div', 'kv-grid');
    const entries = [['设备', item.device?.name || '—'], ['设备 ID', item.device?.device_id || '—'], ['设备类型', item.device?.device_type || '—'],
      ['厂家 / 型号', (item.device?.vendor || '—') + ' / ' + (item.device?.model || '—')], ['软件版本', item.device?.software_version || '—'],
      ['发生时间', (item.occurred_at || '').replace('T', ' ')], ['清除时间', item.cleared_at ? item.cleared_at.replace('T', ' ') : '—'], ['来源系统', item.source_system || '—']];
    for (const [name, value] of entries) { const cell = el('div'); cell.append(el('span', null, name), el('b', null, value)); grid.append(cell); }
    body.append(el('h3', 'alert-dialog-title', '核对事件与适用范围'), grid);
    if (item.recent?.length) {
      const recent = el('details','tech-details'); recent.append(el('summary',null,'同设备近期告警 · '+item.recent.length+' 条'));
      const list = el('div', 'alert-recent');
      for (const row of item.recent) { const line = el('div', 'alert-recent-row'); line.append(el('span', null, row.alarm_code), el('span', null, row.severity), el('time', null, (row.occurred_at || '').replace('T', ' ').slice(5, 16))); list.append(line); }
      recent.append(list); body.append(recent);
    }
    const payload = item.payload && Object.keys(item.payload).length ? item.payload : null;
    if (payload) {
      const details = el('details', 'tech-details'); details.append(el('summary', null, '原始事件字段'));
      details.append(el('pre', 'tech-block', JSON.stringify(payload, null, 2))); body.append(details);
    }
    wrap.append(body);
    const actions = el('div', 'alert-dialog-actions');
    const copy = el('button', 'ui-btn', '复制告警 ID'); copy.type = 'button';
    copy.onclick = async () => { try { await navigator.clipboard.writeText(item.alarm_id); copy.textContent = '已复制'; } catch { copy.textContent = '复制失败'; } setTimeout(() => copy.textContent = '复制告警 ID', 1500); };
    const diag = el('button', 'ui-btn primary', '查看诊断建议'); diag.type = 'button';
    diag.onclick = () => { close(); config.go('agent', { query: item.alarm_code + ' 常见原因', alarm_id: item.alarm_id }); };
    const closeBtn = el('button', 'ui-btn ghost', '关闭'); closeBtn.type = 'button'; closeBtn.onclick = close;
    actions.append(copy, diag, closeBtn); wrap.append(actions);
    return wrap;
  }

  /* ---------- 处理记录（正式工作流：服务端账本） ---------- */
  const HANDLING_ACTIONS = [['confirm', '人工确认'], ['handle', '记录处理'], ['observe', '恢复观察']];

  const HANDLING_STATE_PILL = {pending_confirm: 'warn', processing: 'info', observing: 'info', resolved: 'ok'};
  const OBSERVE_ACTIONS = {confirm: '人工确认', handle: '记录处理', observe: '恢复观察', resolve: '标记解决'};

  async function handlingSection(alarmId) {
    const box = el('section', 'alert-handling');
    box.append(el('h3', 'alert-dialog-title', '处置进度'));
    box.append(el('p', 'ui-help', '先记录处理，再填写恢复依据。观察未通过时继续处理，不会关闭告警。'));
    const stateBar = el('p', 'alert-handling-state meta-line', '正在读取处置状态…');
    box.append(stateBar);
    const observeForm = el('div', 'alert-handling-observe');
    observeForm.hidden = true;
    const list = el('div', 'alert-handling-list');
    const note = el('input', 'ui-input');
    note.placeholder = '处理备注（可选，随本条记录写入）';
    note.maxLength = 500;
    const actions = el('div', 'action-row');
    const receipt = el('p', 'meta-line');
    const history = el('details','tech-details'); history.append(el('summary',null,'查看处理记录与验证依据'),list);
    box.append(stateBar, note, actions, observeForm, receipt, history);

    async function refresh() {
      note.value = '';
      actions.hidden = false;
      observeForm.hidden = true;
      observeForm.replaceChildren();
      try {
        const response = await fetch('/alerts/' + encodeURIComponent(alarmId) + '/handling',
          { headers: config.headers(), cache: 'no-store' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || '读取失败');
        // 状态条：当前状态 + 最近观察结论 + 下一步
        const pill = el('span', 'pill ' + (HANDLING_STATE_PILL[data.state] || 'warn'), data.state_label || data.state);
        const bits = [pill];
        if (data.last_observe_verdict === 'pass') bits.push(el('span', 'meta-line', '最近观察：通过'));
        if (data.last_observe_verdict === 'fail') bits.push(el('span', 'meta-line', '最近观察：未通过（可继续补证处理）'));
        if ((data.next_actions || []).length) bits.push(el('span', 'meta-line', '下一步：' + data.next_actions.map(a => OBSERVE_ACTIONS[a]).join(' / ')));
        if (data.state === 'resolved') bits.push(el('span', 'meta-line', '处置闭环完成'));
        stateBar.replaceChildren(...bits);
        // 时间线（含验证依据）
        const items = data.items || [];
        list.replaceChildren();
        if (!items.length) list.append(el('p', 'meta-line', '尚无处理记录。'));
        for (const item of items) {
          const row = el('div', 'alert-handling-row');
          const verdictPill = item.verdict === 'pass' ? '（通过）' : item.verdict === 'fail' ? '（未通过）' : '';
          row.append(el('span', 'pill ' + (item.action === 'confirm' ? 'info' : item.action === 'observe' ? (item.verdict === 'fail' ? 'bad' : 'ok') : 'warn'),
              item.action_label + verdictPill),
            el('span', null, item.actor), el('time', null, (item.created_at || '').replace('T', ' ').slice(0, 19)));
          if (item.metric_name) row.append(el('p', 'alert-handling-note',
            '验证依据：' + item.metric_name + ' = ' + (item.metric_value || '—') + ' · 观察窗口 ' + (item.observe_minutes || '—') + ' 分钟'));
          if (item.note) row.append(el('p', 'alert-handling-note', item.note));
          if (item.run_id) row.append(el('p', 'alert-handling-run', '诊断 run：' + item.run_id));
          list.append(row);
        }
        // 按当前状态动态渲染动作
        actions.replaceChildren();
        const next = data.next_actions || [];
        const suggested = next.includes('confirm') ? 'confirm' : next.includes('resolve') ? 'resolve' : next.includes('handle') ? 'handle' : next[0];
        for (const action of next) {
          const btn = el('button', 'ui-btn' + (action === suggested ? ' primary' : ''), action === 'observe' ? '填写恢复验证' : OBSERVE_ACTIONS[action]);
          btn.type = 'button';
          btn.onclick = () => {
            if(action === 'observe') { observeForm.hidden = false; actions.hidden = true; observeForm.querySelector('input')?.focus(); }
            else post(action);
          };
          actions.append(btn);
        }
        if (!next.length) actions.append(el('span', 'meta-line',
          data.state === 'resolved' ? '已解决；处置闭环完成。' : '当前状态没有可用动作。'));
        // observe：展开验证依据表单
        if (next.includes('observe')) {
          actions.hidden = false;
          observeForm.append(el('p', 'ui-help', '恢复观察验证依据（必填）：'));
          const metricName = el('input', 'ui-input'); metricName.placeholder = '观察指标名（如 AMF 注册成功率）'; metricName.maxLength = 80; metricName.id = 'observeMetricName';
          const metricValue = el('input', 'ui-input'); metricValue.placeholder = '处理后指标值（如 99.2%）'; metricValue.maxLength = 80; metricValue.id = 'observeMetricValue';
          const minutes = el('input', 'ui-input'); minutes.placeholder = '观察窗口（分钟）'; minutes.id = 'observeMinutes';
          const verdictSel = el('select', 'ui-input'); verdictSel.id = 'observeVerdict';
          for (const [value, label] of [['','请选择验证结论'], ['pass', '观察通过'], ['fail', '观察未通过（继续处理）']]) {
            const opt = el('option', null, label); opt.value = value; verdictSel.append(opt);
          }
          for (const input of [metricName, metricValue, minutes, verdictSel]) {
            input.style.marginBottom = '8px';
            observeForm.append(input);
          }
          minutes.type='number'; minutes.min='1'; minutes.step='1';
          metricName.setAttribute('aria-label','观察指标名'); metricValue.setAttribute('aria-label','处理后指标值'); minutes.setAttribute('aria-label','观察时长（分钟）'); verdictSel.setAttribute('aria-label','验证结论');
          const observationActions=el('div','action-row');
          const submit=el('button','ui-btn primary','提交验证结果'); submit.type='button'; submit.onclick=()=>post('observe');
          const cancel=el('button','ui-btn','返回处理'); cancel.type='button'; cancel.onclick=()=>{observeForm.hidden=true;actions.hidden=false;};
          observationActions.append(submit,cancel); observeForm.append(observationActions);
        }
      } catch (error) {
        stateBar.replaceChildren(el('span', 'meta-line', '处置状态读取失败：' + error.message));
        list.replaceChildren();
      }
    }

    async function post(action) {
      for (const other of box.querySelectorAll('button')) other.disabled = true;
      receipt.textContent = '正在写入…';
      const payload = {action, note: note.value.trim()};
      if (action === 'observe') {
        payload.metric_name = ($('observeMetricName') || {}).value?.trim() || '';
        payload.metric_value = ($('observeMetricValue') || {}).value?.trim() || '';
        payload.observe_minutes = Number(($('observeMinutes') || {}).value);
        payload.verdict = ($('observeVerdict') || {}).value || '';
        if (!payload.metric_name || !payload.metric_value || !Number.isInteger(payload.observe_minutes) || payload.observe_minutes < 1 || !['pass','fail'].includes(payload.verdict)) {
          receipt.textContent = '请填写指标、处理后值、正整数分钟，并选择验证结论。';
          for (const other of box.querySelectorAll('button')) other.disabled = false;
          return;
        }
      }
      if (action === 'resolve') payload.note = note.value.trim() || '恢复观察通过，确认解决';
      try {
        const response = await fetch('/alerts/' + encodeURIComponent(alarmId) + '/handling', {
          method: 'POST', headers: { 'Content-Type': 'application/json', ...config.headers() },
          body: JSON.stringify(payload), cache: 'no-store' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || ('写入失败（HTTP ' + response.status + '）'));
        receipt.textContent = '已记录：' + (data.action_label || action) + ' · 处置状态转为「' + (data.state_label || '—') + '」';
        await refresh();
        // 同步详情头部的处置徽标
        try {
          const fresh = await fetch('/alerts/' + encodeURIComponent(alarmId), {headers: config.headers(), cache: 'no-store'});
          if (fresh.ok) {
            const item = await fresh.json();
            const badge = document.getElementById('alertHandlingBadge');
            if (badge) {
              badge.textContent = '处置：' + (item.handling_state_label || item.handling_state);
              badge.className = 'pill ' + (HANDLING_STATE_PILL[item.handling_state] || 'warn');
            }
          }
        } catch { }
      } catch (error) {
        receipt.textContent = error.message;
      } finally {
        for (const other of box.querySelectorAll('button')) other.disabled = false;
      }
    }
    await refresh();
    return box;
  }

  async function runsSection(item) {
    const box = el('section', 'alert-runs');
    box.append(el('h3', 'alert-dialog-title', '诊断记录（可重开）'));
    box.append(el('p', 'ui-help', '本告警的历史诊断保存在服务端；切换页面或刷新后可从这里重新打开，或一键重新诊断。'));
    const list = el('div', 'alert-runs-list');
    list.append(el('p', 'meta-line', '正在读取诊断记录…'));
    box.append(list);
    try {
      const response = await fetch('/agent/runs?alarm_id=' + encodeURIComponent(item.alarm_id),
        { headers: config.headers(), cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || '读取失败');
      const items = data.items || [];
      list.replaceChildren();
      if (!items.length) { list.append(el('p', 'meta-line', '本告警还没有诊断记录；可在上方时间线确认后创建诊断。')); return box; }
      for (const run of items) {
        const row = el('div', 'alert-run-row');
        const head = el('div', 'alert-run-head');
        head.append(el('span', 'pill ok', 'ANSWERED'),
          el('span', 'pill ' + (run.mode === 'rule' ? 'muted' : 'info'), run.mode === 'rule' ? '规则诊断' : '检索诊断'),
          el('span', 'meta-line', (run.created_at || '').replace('T', ' ').slice(0, 16) + ' · ' + (run.created_by || '—')));
        const reopen = el('button', 'ui-btn', '重新诊断'); reopen.type = 'button';
        reopen.title = '跳转诊断工作台并带入本告警与问题';
        reopen.onclick = () => {
          try {
            const alarmInput = document.getElementById('agentAlarm');
            const queryInput = document.getElementById('agentQuery');
            if (alarmInput) alarmInput.value = item.alarm_id;
            if (queryInput) queryInput.value = run.query || '';
            if (window.switchTab) window.switchTab('agent');
          } catch { }
        };
        head.append(reopen);
        row.append(head);
        if (run.summary) row.append(el('p', 'alert-run-summary', run.summary));
        row.append(el('p', 'alert-handling-run', 'run：' + run.run_id + ' · 版本 ' + (run.kb_version || '—')));
        list.append(row);
      }
    } catch (error) {
      list.replaceChildren(el('p', 'meta-line', '诊断记录读取失败：' + error.message));
    }
    return box;
  }

  const TICKET_STATE_LABEL = {open: '待受理', processing: '处理中', resolved: '待验证', closed: '已关闭', cancelled: '已取消'};
  const TICKET_NEXT = {open: '受理', in_progress: '处理回执 / 关闭验证'};

  async function ticketSection(alarmId) {
    const box = el('section', 'alert-ticket');
    box.append(el('h3', 'alert-dialog-title', '关联工单'));
    box.append(el('p', 'ui-help',
      '从诊断结论一键生成工单草稿（自动带入事件、窗口、建议分类与优先级），人工核对专业、优先级和负责人后建单；同一告警已有进行中工单时不重复建单。'));
    let currentTicket = null;
    const stateBar = el('p', 'meta-line', '正在读取关联工单…');
    const form = el('div', 'alert-ticket-form');
    form.hidden = true;
    const actions = el('div', 'action-row');
    const receipt = el('p', 'meta-line');
    box.append(stateBar, form, actions, receipt);

    async function refresh() {
      form.hidden = true; form.replaceChildren(); actions.replaceChildren();
      actions.hidden = false;
      try {
        const response = await fetch('/alerts/' + encodeURIComponent(alarmId) + '/ticket',
          { headers: config.headers(), cache: 'no-store' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || '读取失败');
        const ticket = data.ticket;
        currentTicket = ticket ? ticket.ticket_id : null;
        if (!ticket) {
          stateBar.replaceChildren(el('span', 'meta-line', '该告警尚无关联工单。'));
          const draftBtn = el('button', 'ui-btn primary', '生成工单草稿'); draftBtn.type = 'button';
          draftBtn.onclick = () => loadDraft();
          actions.append(draftBtn);
          return;
        }
        stateBar.replaceChildren(
          el('span', 'pill ' + (ticket.status === 'closed' ? 'ok' : 'info'),
            ticket.ticket_id + ' · ' + (TICKET_STATE_LABEL[ticket.status] || ticket.status)),
          el('span', 'meta-line', `优先级 ${ticket.priority} · 负责人 ${ticket.owner || '—'}`));
        const open = el('button', 'ui-btn', '在工单列表查看'); open.type = 'button';
        open.onclick = () => {
          close();
          const tab = document.querySelector('#alertsSubtabs [data-alertstab="tickets"]');
          if (tab) tab.click();
        };
        actions.append(open);
        if (ticket.status === 'open') {
          const accept = el('button', 'ui-btn primary', '受理工单'); accept.type = 'button';
          accept.onclick = () => postState('accept', '');
          actions.append(accept);
        } else if (ticket.status === 'processing') {
          const note = el('input', 'ui-input');
          note.placeholder = '处理回执 / 关闭验证结论（关闭至少 8 字，说明验证依据与结果）';
          note.maxLength = 500; note.style.width = '100%'; note.style.marginBottom = '8px';
          stateBar.append(note);
          const progress = el('button', 'ui-btn', '记录处理回执'); progress.type = 'button';
          progress.onclick = () => postState('progress', note.value.trim());
          const close = el('button', 'ui-btn primary', '关闭验证'); close.type = 'button';
          close.onclick = () => postState('close', note.value.trim());
          actions.append(progress, close);
        } else {
          stateBar.append(el('span', 'meta-line', '工单已关闭；如需继续请重开或新建说明。'));
        }
      } catch (error) {
        stateBar.replaceChildren(el('span', 'meta-line', '工单读取失败：' + error.message));
      }
    }

    async function loadDraft() {
      receipt.textContent = '正在生成工单草稿…';
      try {
        const response = await fetch('/alerts/' + encodeURIComponent(alarmId) + '/ticket-draft',
          { method: 'POST', headers: config.headers(), cache: 'no-store' });
        const d = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(d.detail || '草稿生成失败');
        receipt.textContent = '';
        actions.hidden = true;
        form.hidden = false; form.replaceChildren();
        form.append(el('p', 'ui-help', (d.suggestions || []).join('；') + '。以下为草稿，请核对后建单：'));
        if (d.existing_ticket) {
          form.append(el('p', 'notice warn', '该告警已有进行中工单 ' + d.existing_ticket.ticket_id +
            '（' + (TICKET_STATE_LABEL[d.existing_ticket.status] || d.existing_ticket.status) + '）；确认建单会被拒绝。'));
        }
        const title = el('input', 'ui-input'); title.value = d.title || ''; title.maxLength = 120;
        title.id = 'ticketTitle'; title.style.width = '100%'; title.style.marginBottom = '8px';
        const category = el('input', 'ui-input'); category.value = d.category || ''; category.maxLength = 40;
        category.id = 'ticketCategory'; category.style.width = '100%'; category.style.marginBottom = '8px';
        const priority = el('select', 'ui-input'); priority.id = 'ticketPriority';
        priority.style.width = '100%'; priority.style.marginBottom = '8px';
        for (const [value, label] of [['P1', 'P1 · 紧急'], ['P2', 'P2 · 高'], ['P3', 'P3 · 中'], ['P4', 'P4 · 低']]) {
          const opt = el('option', null, label); opt.value = value; if (value === d.priority) opt.selected = true;
          priority.append(opt);
        }
        const owner = el('select', 'ui-input'); owner.id = 'ticketOwner';
        owner.style.width = '100%'; owner.style.marginBottom = '8px';
        for (const user of (d.owner_candidates || [d.owner_suggestion].filter(Boolean))) {
          const opt = el('option', null, user); opt.value = user;
          if (user === d.owner_suggestion) opt.selected = true;
          owner.append(opt);
        }
        const summary = el('textarea', 'ui-input'); summary.rows = 3; summary.maxLength = 500;
        summary.id = 'ticketSummary'; summary.style.width = '100%'; summary.style.marginBottom = '8px';
        summary.placeholder = '工单摘要：现象、已核对证据、候选判断、下一步安排（可选，建议引用诊断结论）';
        if (d.last_handling_note) summary.value = d.last_handling_note;
        for(const [label,input] of [['工单标题',title],['处理专业',category],['优先级',priority],['负责人',owner],['摘要与核查建议',summary]]){
          const field=el('label','ui-field');field.append(el('span','ui-label',label),input);form.append(field);
        }
        if ((d.missing_hints || []).length) form.append(el('p', 'notice warn', '缺项提示：' + d.missing_hints.join('；')));
        if (d.diagnosis_run_id) form.append(el('p', 'meta-line', '关联诊断 run：' + d.diagnosis_run_id));
        const create = el('button', 'ui-btn primary', '确认建单'); create.type = 'button';
        create.onclick = async () => {
          create.disabled = true;
          try {
            const res = await fetch('/alerts/' + encodeURIComponent(alarmId) + '/ticket', {
              method: 'POST', headers: { 'Content-Type': 'application/json', ...config.headers() },
              body: JSON.stringify({ title: title.value.trim(), category: category.value.trim(),
                priority: priority.value, owner: owner.value.trim(), summary: summary.value.trim() }),
              cache: 'no-store' });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(payload.detail || ('建单失败（HTTP ' + res.status + '）'));
            receipt.textContent = payload.message || '工单已创建';
            form.hidden = true;
            await refresh();
          } catch (error) {
            receipt.textContent = error.message;
          } finally { create.disabled = false; }
        };
        const cancel = el('button', 'ui-btn', '放弃草稿'); cancel.type = 'button';
        cancel.onclick = () => { form.hidden = true; form.replaceChildren(); actions.hidden = false; };
        const row = el('div', 'action-row'); row.append(create, cancel);
        form.append(row);
      } catch (error) {
        receipt.textContent = '草稿生成失败：' + error.message;
      }
    }

    async function postState(action, note) {
      for (const other of actions.querySelectorAll('button')) other.disabled = true;
      receipt.textContent = '正在更新工单…';
      try {
        if (!currentTicket) { receipt.textContent = '尚未定位到工单，无法更新状态。'; return; }
        const res = await fetch('/tickets/' + encodeURIComponent(currentTicket) + '/state', {
          method: 'POST', headers: { 'Content-Type': 'application/json', ...config.headers() },
          body: JSON.stringify({ action, note }), cache: 'no-store' });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(payload.detail || ('更新失败（HTTP ' + res.status + '）'));
        receipt.textContent = payload.message || '已更新';
        await refresh();
      } catch (error) {
        receipt.textContent = error.message;
      } finally {
        for (const other of actions.querySelectorAll('button')) other.disabled = false;
      }
    }
    await refresh();
    return box;
  }

  async function open(id) {
    const request = ++detailEpoch, key = config.key();
    selected = id;
    const host = $('alertsDetail');
    const view = document.getElementById('view-alerts');
    if (!host) {  // 兼容：没有内嵌容器时回退弹窗
      const dlg = $('alertDlg'), body = $('alertDlgBody');
      body.replaceChildren(el('div', 'alert-dialog-loading', '正在读取告警详情…'));
      try { dlg.showModal(); } catch { }
      try {
        const response = await fetch('/alerts/' + encodeURIComponent(id), { headers: config.headers(), cache: 'no-store' });
        if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || '读取失败'); }
        const pane = await detailPane(await response.json());
        if(request!==detailEpoch || key!==config.key())return;
        body.replaceChildren(pane);
      } catch (error) { body.replaceChildren(el('div', 'alert-empty', error.message)); }
      return;
    }
    // 独立详情区：取代一次性弹窗，返回后保留列表筛选/页码/滚动
    host.hidden = false;
    if (view) view.classList.add('detail-open');
    host.replaceChildren(el('div', 'alert-dialog-loading', '正在读取告警详情…'));
    host.scrollTop = 0;
    try {
      const response = await fetch('/alerts/' + encodeURIComponent(id), { headers: config.headers(), cache: 'no-store' });
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || '读取失败'); }
      const pane = await detailPane(await response.json());
      if(request!==detailEpoch || key!==config.key())return;
      host.replaceChildren(pane);
    } catch (error) {
      if(request!==detailEpoch || key!==config.key())return;
      const back=el('button','ui-btn','返回列表');back.type='button';back.onclick=close;
      host.replaceChildren(back,el('div', 'alert-empty', error.message));
    }
  }

  async function detailPane(item) {
    const shell = detailShell(item);
    shell.prepend(detailBackBar(item));
    const overview=el('section','alert-task-panel');overview.dataset.panel='event';
    overview.append(shell.querySelector('.alert-dialog-body'),shell.querySelector('.alert-dialog-actions'));
    const tabs=el('div','alert-task-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','处理任务');
    const panels=el('div','alert-task-panels');shell.append(tabs,panels);
    const loaders={diagnosis:()=>runsSection(item),ticket:()=>ticketSection(item.alarm_id),handling:()=>handlingSection(item.alarm_id)};
    let selection=0;const key=config.key();
    for(const [name,label] of [['event','核对事件'],['diagnosis','诊断记录'],['ticket','关联工单'],['handling','处理与验证']]){
      const panel=name==='event'?overview:el('section','alert-task-panel');panel.dataset.panel=name;panel.id='alert-task-'+name;
      panel.setAttribute('role','tabpanel');panel.hidden=name!=='event';panels.append(panel);
      const button=el('button','alert-task-tab'+(name==='event'?' is-active':''),label);button.type='button';button.dataset.task=name;button.id='alert-task-tab-'+name;button.setAttribute('role','tab');button.setAttribute('aria-controls',panel.id);button.setAttribute('aria-selected',String(name==='event'));panel.setAttribute('aria-labelledby',button.id);
      button.onclick=async()=>{
        const token=++selection;
        tabs.querySelectorAll('button').forEach(b=>{const on=b===button;b.classList.toggle('is-active',on);b.setAttribute('aria-selected',String(on));});
        panels.querySelectorAll(':scope > section').forEach(p=>p.hidden=p!==panel);
        if(name==='event')return;
        if(panel.dataset.loaded==='true')return;
        panel.replaceChildren(el('p','meta-line','正在读取…'));
        try{const section=await loaders[name]();if(token!==selection||key!==config.key()||selected!==item.alarm_id)return;
          const reload=el('button','ui-btn sm','刷新本区记录');reload.type='button';reload.onclick=()=>{delete panel.dataset.loaded;button.click();};
          panel.replaceChildren(reload,section);panel.dataset.loaded='true';
          if(name==='diagnosis'){const start=el('button','ui-btn primary','开始新的诊断');start.type='button';start.onclick=()=>{close();config.go('agent',{query:item.alarm_code+' 常见原因',alarm_id:item.alarm_id});};panel.append(start);}
        }catch(error){if(token===selection)panel.replaceChildren(el('p','notice warn',error.message));}
      };tabs.append(button);
    }
    return shell;
  }

  function detailBackBar(item) {
    const bar = el('div', 'detail-back-bar');
    const back = el('button', 'ui-btn', '← 返回告警列表'); back.type = 'button';
    back.onclick = () => close();
    bar.append(back, el('span', 'meta-line', '核对事件，再选择本次要继续的任务'));
    return bar;
  }

  function close() {
    detailEpoch++;
    const host = $('alertsDetail');
    if (host) { host.hidden = true; host.replaceChildren(); }
    const view = document.getElementById('view-alerts');
    if (view) view.classList.remove('detail-open');
    const dlg = $('alertDlg'); if (dlg?.open) dlg.close();
    selected = null;
  }

  function exportCsv() {
    const items = last?.items || [];
    if (!items.length) { $('alertsStatus').textContent = '当前没有可导出的告警'; return; }
    const header = ['alarm_id', 'alarm_code', 'severity', 'status', 'device_id', 'device_name', 'device_type', 'occurred_at'];
    const lines = [header.join(',')].concat(items.map(item => header.map(k => '"' + String(item[k] ?? '').replace(/"/g, '""') + '"').join(',')));
    const blob = new Blob(["\ufeff" + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'alerts-' + new Date().toISOString().slice(0, 10) + '.csv';
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function leave() { epoch++; controller?.abort(); controller = null; active = false; close(); }

  function init(options) {
    config = options;
    $('alertsFilter').addEventListener('submit', e => { e.preventDefault(); page = 1; load(); });
    $('alertsReset').addEventListener('click', () => { $('alertsFilter').reset(); page = 1; load(); });
    $('alertsRefresh').addEventListener('click', () => load());
    $('alertsExport').addEventListener('click', exportCsv);
    $('alertsPrev').addEventListener('click', () => { if ((last?.page || 1) > 1) { page = last.page - 1; load({ page }); } });
    $('alertsNext').addEventListener('click', () => { if (last && last.page < last.pages) { page = last.page + 1; load({ page }); } });
    $('alertDlg').addEventListener('click', e => { if (e.target === $('alertDlg')) close(); });
    $('alertDlg').addEventListener('close', () => { selected = null; });
    window.addEventListener('storage',event=>{if(event.key==='api_key'||event.key===null)close();});
  }

  return { init, load, leave, open, close };
})();
