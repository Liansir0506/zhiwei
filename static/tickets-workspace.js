/* 工单页签（B1）：我的待办 / 处理中 / 已关闭，集中入口 + 状态操作。 */
window.TicketsWorkspace = (() => {
  let config = {}, seq = 0, state = 'active', page = 1, last = null, detailId = null;

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };
  const $ = id => document.getElementById(id);
  const STATUS_PILL = {open: 'warn', processing: 'info', closed: 'ok', resolved: 'warn', cancelled: 'muted'};
  const STATUS_LABEL = {open:'待受理',processing:'处理中',closed:'已关闭',resolved:'待验证',cancelled:'已取消'};

  function init(options) { config = options; }

  function leave() { ++seq; }

  async function load(params = {}) {
    const host = $('ticketsPane');
    if (!host) return;
    const token = ++seq;
    if (params.state !== undefined) state = params.state || 'active';
    if (params.page) page = params.page;
    if (params.mine !== undefined) mineOnly = !!params.mine;
    host.replaceChildren(el('p', 'meta-line', '正在读取工单…'));
    const query = `/tickets?state=${encodeURIComponent(state)}&page=${page}&page_size=20${mineOnly ? '&mine=true' : ''}`;
    let data;
    try {
      const response = await fetch(query, {headers: config.headers(), cache: 'no-store'});
      data = await response.json().catch(() => ({}));
      if (token !== seq) return;
      if (!response.ok) {
        if (response.status === 404) {
          throw new Error('当前进程未加载工单接口（404）：请双击“启动运维平台.bat”重启后再试。');
        }
        throw new Error(data.detail || ('读取失败（HTTP ' + response.status + '）'));
      }
    } catch (error) {
      if (token !== seq) return;
      host.replaceChildren(el('p', 'notice warn', '工单读取失败：' + error.message));
      return;
    }
    last = data;
    host.replaceChildren();

    // 工具栏：筛选 + 我的
    const bar = el('div', 'filter-bar');
    const tabs = el('div', 'ticket-state-tabs');
    for (const [value, label, count] of [['active', '待办中', (data.counts?.open || 0) + (data.counts?.processing || 0)],
      ['open', '待受理', data.counts?.open || 0], ['processing', '处理中', data.counts?.processing || 0],
      ['closed', '已关闭', data.counts?.closed || 0]]) {
      const btn = el('button', 'ticket-state-tab' + (state === value ? ' is-active' : ''), `${label} ${count}`);
      btn.type = 'button';
      btn.onclick = () => load({state: value, page: 1});
      tabs.append(btn);
    }
    const mineLabel = el('label', 'ticket-mine');
    const mineBox = el('input'); mineBox.type = 'checkbox'; mineBox.checked = mineOnly;
    mineBox.onchange = () => { mineOnly = mineBox.checked; load({page: 1}); };
    mineLabel.append(mineBox, el('span', null, '只看我创建的'));
    bar.append(tabs, mineLabel);
    host.append(bar);

    if (!data.items.length) {
      const empty = el('div', 'alert-empty');
      empty.append(el('strong', null, '当前筛选下没有工单'),
        el('p', null, '在“告警与资源”打开一条告警，可在详情里由诊断结论生成工单草稿。'));
      host.append(empty);
      return;
    }

    const table = el('table', 'data-table ticket-table card-table');
    const head = el('thead'), hr = el('tr');
    for (const name of ['工单', '状态', '优先级', '负责人', '关联告警', '设备', '创建时间']) hr.append(el('th', null, name));
    head.append(hr); table.append(head);
    const body = el('tbody');
    for (const item of data.items) {
      const tr = el('tr');
      tr.tabIndex = 0;
      const idCell = el('td'); idCell.dataset.label = '工单';
      idCell.append(el('strong', null, item.ticket_id), el('small', null, item.title));
      tr.append(idCell);
      const statusCell = el('td'); statusCell.dataset.label = '状态';
      statusCell.append(el('span', 'pill ' + (STATUS_PILL[item.status] || 'muted'), item.status_label));
      tr.append(statusCell);
      const priCell = el('td', null, item.priority); priCell.dataset.label = '优先级'; tr.append(priCell);
      const ownerCell = el('td', null, item.owner || '—'); ownerCell.dataset.label = '负责人'; tr.append(ownerCell);
      const alarmCell = el('td'); alarmCell.dataset.label = '关联告警';
      if (item.alarm_id) {
        const link = el('button', 'link-btn', item.alarm_code || item.alarm_id);
        link.type = 'button';
        link.onclick = event => { event.stopPropagation(); openAlarm(item.alarm_id); };
        alarmCell.append(link);
      } else alarmCell.append(el('span', null, '—'));
      tr.append(alarmCell);
      const deviceCell = el('td', null, item.device_name || item.device_id || '—'); deviceCell.dataset.label = '设备'; tr.append(deviceCell);
      const timeCell = el('td', 'alert-time', (item.opened_at || '').replace('T', ' ').slice(5, 16)); timeCell.dataset.label = '创建时间'; tr.append(timeCell);
      tr.onclick = () => openDetail(item.ticket_id);
      tr.onkeydown = event => { if (event.key === 'Enter') openDetail(item.ticket_id); };
      body.append(tr);
    }
    table.append(body);
    host.append(table);

    const pager = el('div', 'alert-pager');
    const prev = el('button', 'ui-btn', '上一页'); prev.type = 'button'; prev.disabled = data.page <= 1;
    prev.onclick = () => load({page: data.page - 1});
    const next = el('button', 'ui-btn', '下一页'); next.type = 'button'; next.disabled = data.page >= data.pages;
    next.onclick = () => load({page: data.page + 1});
    pager.append(prev, el('span', 'meta-line', `第 ${data.page} / ${data.pages} 页 · 共 ${data.total} 张`), next);
    host.append(pager);
    host.append(el('p', 'meta-line', data.scope || ''));
  }

  function openAlarm(alarmId) {
    config.go?.('alerts', {});
    setTimeout(() => { try { window.AlertsWorkspace?.open(alarmId); } catch { } }, 400);
  }

  async function openDetail(ticketId) {
    const host = $('ticketsPane');
    if (!host) return;
    detailId = ticketId;
    const back = el('button', 'ui-btn', '← 返回工单列表'); back.type = 'button';
    back.onclick = () => { detailId = null; load({}); };
    const wrap = el('section', 'surface ticket-detail');
    wrap.append(back, el('p', 'meta-line', '正在读取…'));
    host.replaceChildren(wrap);
    try {
      const response = await fetch('/tickets/' + encodeURIComponent(ticketId), {headers: config.headers(), cache: 'no-store'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || '读取失败');
      const t = data.ticket || {};
      wrap.replaceChildren(back);
      wrap.append(el('h3', '', t.ticket_id + ' · ' + t.title));
      const grid = el('div', 'kv-grid');
      for (const [label, value] of [['状态', STATUS_LABEL[t.status] || t.status],
        ['优先级', t.priority], ['负责人', t.owner || '—'], ['部门', t.dept_id || '—'],
        ['创建时间', (t.opened_at || '—').replace('T', ' ')], ['关闭时间', (t.closed_at || '—').replace('T', ' ')]]) {
        const cell = el('div'); cell.append(el('span', null, label), el('b', null, String(value))); grid.append(cell);
      }
      wrap.append(grid);
      const actions = el('div', 'action-row');
      if (t.alarm?.alarm_id) {
        const link = el('button', 'ui-btn', '查看原告警与诊断'); link.type = 'button';
        link.onclick = () => openAlarm(t.alarm.alarm_id);
        actions.append(link);
      }
      const note = el('input', 'ui-input'); note.placeholder = '处理回执 / 关闭验证结论（关闭至少 8 字）'; note.maxLength = 500;
      note.setAttribute('aria-label','处理回执与验证结论'); note.hidden=t.status!=='processing';
      note.style.width = '100%'; note.style.margin = '10px 0 8px';
      wrap.append(note, actions);
      const receipt = el('p', 'meta-line');
      const post = async (action, button) => {
        if(action==='close'&&note.value.trim().length<8){receipt.textContent='请先填写至少 8 字的验证依据与结论，再关闭工单。';note.focus();return;}
        button.disabled = true;
        try {
          const res = await fetch('/tickets/' + encodeURIComponent(ticketId) + '/state', {
            method: 'POST', headers: {'Content-Type': 'application/json', ...config.headers()},
            body: JSON.stringify({action, note: note.value.trim()}), cache: 'no-store'});
          const payload = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(payload.detail || ('操作失败（HTTP ' + res.status + '）'));
          receipt.textContent = payload.message || '已更新';
          await openDetail(ticketId);
        } catch (error) { receipt.textContent = error.message; }
        finally { button.disabled = false; }
      };
      if (t.status === 'open') {
        const accept = el('button', 'ui-btn primary', '受理'); accept.type = 'button';
        accept.onclick = () => post('accept', accept); actions.append(accept);
      } else if (t.status === 'processing') {
        const progress = el('button', 'ui-btn', '记录处理回执'); progress.type = 'button';
        progress.onclick = () => post('progress', progress);
        const close = el('button', 'ui-btn primary', '验证并关闭'); close.type = 'button';
        close.onclick = () => post('close', close);
        actions.append(progress, close);
      } else if (t.status === 'closed') {
        // C4：关闭后沉淀案例草稿（提交审核，不自动入库）
        const draft = el('button', 'ui-btn primary', '生成案例草稿'); draft.type = 'button';
        draft.onclick = async () => {
          draft.disabled = true;
          try {
            const res = await fetch('/tickets/' + encodeURIComponent(ticketId) + '/case-draft', {
              method: 'POST', headers: config.headers(), cache: 'no-store'});
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(payload.detail || ('生成失败（HTTP ' + res.status + '）'));
            receipt.textContent = payload.message || '案例草稿已提交审核';
          } catch (error) { receipt.textContent = error.message; }
          finally { draft.disabled = false; }
        };
        actions.append(draft);
      }
      wrap.append(receipt);
    } catch (error) {
      wrap.replaceChildren(back, el('p', 'notice warn', '工单读取失败：' + error.message));
    }
  }

  let mineOnly = false;
  return {init, load, leave, openDetail};
})();
