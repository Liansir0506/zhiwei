/* 产品壳交互：全局搜索、待办通知、演示导览、账号菜单与快捷键。 */
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = String(text); return n; };
  let notifyData = null, notifyTimer = null;

  function go(tab, params) { if (typeof window.switchTab === 'function') window.switchTab(tab, params || {}); }
  function dialog(id, cls) {
    let d = $(id);
    if (d) return d;
    d = document.createElement('dialog'); d.id = id; d.className = 'ui-dialog ' + (cls || '');
    d.addEventListener('click', e => { if (e.target === d) d.close(); });
    document.body.append(d); return d;
  }
  function close(d) { if (d?.open) d.close(); }

  async function fetchJson(url) {
    const response = await fetch(url, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }

  /* 全局搜索：分类结果（告警 / 设备 / 知识），回车进入对应板块 */
  async function searchAll(query) {
    const [alarms, devices, kb] = await Promise.all([
      fetchJson('/alerts?page_size=8&query=' + encodeURIComponent(query)).catch(() => null),
      fetchJson('/devices?page_size=8&query=' + encodeURIComponent(query)).catch(() => null),
      fetchJson('/kb/catalog?page=1&page_size=8&query=' + encodeURIComponent(query)).catch(() => null),
    ]);
    return { alarms: alarms && alarms.items || [], devices: devices && devices.items || [],
             kb: kb && kb.items || [], totalAlarms: (alarms && alarms.total) || 0,
             totalDevices: (devices && devices.total) || 0, totalKb: (kb && kb.total) || 0,
             version: kb && kb.kb_version };
  }

  function renderSearchResults(query, data) {
    const d = dialog('searchDlg', 'search-dialog');
    d.replaceChildren();
    const box = node('div', 'search-results');
    const head = node('div', 'section-head');
    head.append(node('h2', null, '搜索“' + query + '”'), node('span', 'meta-line', '告警 / 设备 / 知识'));
    box.append(head);
    const groups = [];
    if (data.alarms.length) groups.push(['告警', data.totalAlarms, data.alarms,
      item => [item.alarm_code, (item.device_name || '') + ' · ' + (item.severity_label || item.severity)],
      () => go('alerts', { query })]);
    if (data.devices.length) groups.push(['设备', data.totalDevices, data.devices,
      item => [item.name || item.device_id, (item.device_type || '') + ' · ' + (item.status || '')],
      () => go('alerts', { tab: 'devices' })]);
    if (data.kb.length) groups.push(['知识', data.totalKb, data.kb,
      item => [item.title || item.question || '（无题）', (item.source || '') + ' · ' + (item.kb_version || data.version || '')],
      () => go('kb', { query })]);
    if (!groups.length) {
      box.append(node('p', 'notify-empty', '没有匹配的结果。可以换个关键词，或到知识中心按条件浏览。'));
    }
    for (const [label, total, items, describe, jump] of groups) {
      const group = node('section', 'search-group');
      const title = node('div', 'search-group-head');
      title.append(node('strong', null, label + '（' + total + '）'), node('button', 'link-button', '查看全部'));
      title.lastChild.onclick = jump;
      group.append(title);
      for (const item of items.slice(0, 5)) {
        const [primary, secondary] = describe(item);
        const row = node('div', 'search-row');
        row.append(node('strong', null, primary), node('span', 'search-row-sub', secondary));
        row.onclick = jump;
        group.append(row);
      }
      box.append(group);
    }
    const actions = node('div', 'alert-dialog-actions');
    const closeBtn = node('button', 'ui-btn', '关闭'); closeBtn.type = 'button'; closeBtn.onclick = () => close(d);
    actions.append(closeBtn);
    box.append(actions);
    d.append(box);
    if (!d.open) d.showModal();
  }

  function bindSearch() {
    const form = $('globalSearchForm'), input = $('globalSearch');
    if (!form || !input) return;
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const query = input.value.trim();
      if (!query) { go('alerts', {}); input.blur(); return; }
      const d = dialog('searchDlg', 'search-dialog');
      d.replaceChildren(node('div', 'notify-empty', '正在搜索告警、设备与知识…'));
      if (!d.open) d.showModal();
      try {
        const data = await searchAll(query);
        renderSearchResults(query, data);
      } catch (error) {
        d.replaceChildren(node('p', 'notify-empty', '搜索失败：' + error.message));
      }
      input.blur();
    });
    document.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); input.focus(); input.select();
      }
    });
    if (matchMedia('(max-width: 760px)').matches) input.placeholder = '搜索告警 / 设备 / 知识…';
  }

  /* 通知：活动告警前 5 条 */
  function renderNotify() {
    const d = dialog('notifyDlg', 'notify-dialog');
    d.replaceChildren();
    const box = node('div', 'notify-list');
    const head = node('div', 'section-head');
    head.append(node('h2', null, '待办通知'), node('span', 'meta-line', notifyData ? notifyData.summary.open + ' 项活动告警' : '—'));
    box.append(head);
    if (!notifyData) { box.append(node('p', 'notify-empty', '正在读取…')); }
    else if (!notifyData.items.length) { box.append(node('p', 'notify-empty', '当前没有活动告警。')); }
    else {
      for (const item of notifyData.items) {
        const row = node('div', 'notify-row');
        const info = node('div');
        info.append(node('strong', null, item.alarm_code + ' · ' + item.device_name), node('span', null, item.severity_label + ' · ' + item.status_label + ' · ' + (item.occurred_at || '').replace('T', ' ').slice(5, 16)));
        const btn = node('button', 'ui-btn sm', '查看'); btn.type = 'button';
        btn.onclick = () => { close(d); if (window.AlertsWorkspace?.open) window.AlertsWorkspace.open(item.alarm_id); else go('alerts', { query: item.alarm_id }); };
        row.append(info, btn); box.append(row);
      }
      const all = node('button', 'ui-btn primary', '进入告警中心'); all.type = 'button';
      all.onclick = () => { close(d); go('alerts'); }; all.style.marginTop = '12px';
      box.append(all);
    }
    d.append(box);
  }

  async function loadNotify() {
    try {
      notifyData = await fetchJson('/alerts?status=open&page_size=5');
      const dot = $('notifyDot'), btn = $('notifyBtn');
      const count = notifyData.summary?.open || 0;
      if (dot) dot.hidden = count <= 0;
      if (btn) btn.title = count > 0 ? ('待办通知：' + count + ' 项活动告警') : '待办通知';
    } catch { if ($('notifyDot')) $('notifyDot').hidden = true; }
  }

  function openNotify() { renderNotify(); const d = $('notifyDlg'); if (d && !d.open) d.showModal(); loadNotify().then(() => { if ($('notifyDlg')?.open) renderNotify(); }); }

  /* 演示导览 */
  const GUIDE = [
    ['dashboard', '总览', '在线率、活动告警、工单与知识资产；点击任意指标进入对应工作区。'],
    ['alerts', '告警中心', '按级别、状态与设备筛选；详情里可直接创建诊断。'],
    ['agent', '只读诊断', '一次一题，展示取证与边界；隔离服务未启动时会明确提示。'],
    ['kb', '知识中心', '搜索、版本、原文与引用；发布与回滚在版本与发布。'],
    ['docs', '文档与入库', '候选 → 上传 → 任务 → 台账 → 版本，一步一屏。'],
    ['versions', '版本与发布', '当前版本、候选、历史与门禁，原始配置全部折叠。'],
  ];
  function openGuide() {
    const d = dialog('guideDlg', 'guide-dialog'); d.replaceChildren();
    const box = node('div', 'guide');
    box.append(node('h2', null, '演示导览'), node('p', null, '按顺序看一遍，六步覆盖从告警发现到版本发布的完整闭环。'));
    const steps = node('div', 'guide-steps');
    GUIDE.forEach(([tab, title, desc], i) => {
      const row = node('div', 'guide-step');
      row.append(node('b', null, String(i + 1)));
      const copy = node('div'); copy.append(node('strong', null, title), node('span', null, desc));
      const btn = node('button', 'ui-btn sm', '进入'); btn.type = 'button';
      btn.onclick = () => { close(d); go(tab); };
      row.append(copy, btn); steps.append(row);
    });
    box.append(steps);
    const actions = node('div', 'guide-actions');
    const closeBtn = node('button', 'ui-btn ghost', '关闭'); closeBtn.type = 'button'; closeBtn.onclick = () => close(d);
    actions.append(closeBtn); box.append(actions); d.append(box);
    d.showModal();
  }

  /* 账号菜单 */
  async function openAccount() {
    const d = dialog('accountDlg', 'account-dialog'); d.replaceChildren();
    const box = node('div', 'account');
    box.append(node('h2', null, '当前账号'), node('p', null, '本机演示环境 · 凭据仅保存在当前浏览器'));
    let caps = null;
    try { caps = await fetchJson('/admin/capabilities'); } catch { }
    const roles = node('div', 'account-role');
    if (caps) {
      for (const role of caps.roles || []) roles.append(node('span', 'pill info', role));
      roles.append(node('span', 'pill muted', caps.tenant_id || 'default'));
    } else roles.append(node('span', 'pill muted', '未读取到角色'));
    box.append(roles);
    const actions = node('div', 'account-actions');
    const key = node('button', 'ui-btn', '设置 API Key'); key.type = 'button'; key.onclick = () => { close(d); $('keyBtn')?.click(); };
    const refresh = node('button', 'ui-btn', '刷新可用功能'); refresh.type = 'button'; refresh.onclick = () => { close(d); $('capabilitiesRetry')?.click(); };
    const perms = node('button', 'ui-btn', '访问管理'); perms.type = 'button'; perms.onclick = () => { close(d); go('perm'); };
    actions.append(key, refresh);
    if (caps?.can_read_permissions) actions.append(perms);
    box.append(actions); d.append(box); d.showModal();
  }

  function bind() {
    bindSearch();
    $('notifyBtn')?.addEventListener('click', openNotify);
    $('guideBtn')?.addEventListener('click', openGuide);
    $('avatarBtn')?.addEventListener('click', openAccount);
    // Keep the global shell accessible when the sidebar is explicitly collapsed.
    const sidebar = document.querySelector('aside.workspace-sidebar');
    const toggle = $('burger');
    const syncSidebar = () => {
      const narrow = matchMedia('(max-width: 760px)').matches;
      const expanded = narrow ? document.body.classList.contains('mobile-menu') : !document.body.classList.contains('side-collapsed');
      toggle?.setAttribute('aria-expanded', String(expanded));
      if (sidebar) sidebar.inert = !expanded;
    };
    new MutationObserver(syncSidebar).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    window.addEventListener('resize', syncSidebar);
    syncSidebar();
    window.openGuide = openGuide;
    loadNotify();
    notifyTimer = setInterval(() => { if (!document.hidden) loadNotify(); }, 60000);
    window.addEventListener('beforeunload', () => clearInterval(notifyTimer));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();

/* 侧栏拖拽调宽：鼠标/触控拖动边界，键盘 ← → 微调，双击或 Home 恢复默认。 */
(() => {
  const KEY = 'ui.sidebar_width_v1';
  const MIN = 200, MAX = 320;
  const root = document.documentElement;
  const aside = document.querySelector('aside.workspace-sidebar');
  const handle = document.getElementById('sidebarResizer');
  if (!aside || !handle) return;

  const clamp = value => Math.max(MIN, Math.min(MAX, Math.round(value)));
  const current = () => Math.round(aside.getBoundingClientRect().width);
  const syncAria = () => handle.setAttribute('aria-valuenow', String(current()));
  const setWidth = (value, persist) => {
    const width = clamp(value);
    root.style.setProperty('--sidebar-w', width + 'px');
    handle.setAttribute('aria-valuenow', String(width));
    if (persist) {
      try { localStorage.setItem(KEY, String(width)); } catch (e) {}
    }
    return width;
  };
  const reset = () => {
    root.style.removeProperty('--sidebar-w');
    try { localStorage.removeItem(KEY); } catch (e) {}
    requestAnimationFrame(syncAria);
  };

  // 恢复用户上次的宽度（head 里已提前设过，这里保证 JS 状态同步）
  try {
    const saved = parseInt(localStorage.getItem(KEY) || '', 10);
    if (Number.isFinite(saved)) setWidth(saved, false);
  } catch (e) {}
  syncAria();

  let drag = null;
  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0 || window.matchMedia('(max-width: 760px)').matches) return;
    drag = { pointerId: event.pointerId, startX: event.clientX, startWidth: current() };
    handle.classList.add('is-dragging');
    document.body.classList.add('sidebar-resizing');
    try { handle.setPointerCapture(event.pointerId); } catch (e) {}
    event.preventDefault();
  });
  handle.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    setWidth(drag.startWidth + (event.clientX - drag.startX), false);
    event.preventDefault();
  });
  const finish = event => {
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    const width = current();
    try { localStorage.setItem(KEY, String(width)); } catch (e) {}
    try {
      if (handle.hasPointerCapture && handle.hasPointerCapture(drag.pointerId)) handle.releasePointerCapture(drag.pointerId);
    } catch (e) {}
    drag = null;
    handle.classList.remove('is-dragging');
    document.body.classList.remove('sidebar-resizing');
    syncAria();
  };
  handle.addEventListener('pointerup', finish);
  handle.addEventListener('pointercancel', finish);
  handle.addEventListener('dblclick', event => { event.preventDefault(); reset(); });
  handle.addEventListener('keydown', event => {
    const step = event.shiftKey ? 24 : 8;
    if (event.key === 'ArrowLeft') { setWidth(current() - step, true); event.preventDefault(); }
    else if (event.key === 'ArrowRight') { setWidth(current() + step, true); event.preventDefault(); }
    else if (event.key === 'Home') { reset(); event.preventDefault(); }
  });
  // 视口跨越 1500 断点时，未自定义过宽度的用户回到该断点默认值（CSS 负责，ARIA 同步）
  window.addEventListener('resize', () => {
    try { if (!localStorage.getItem(KEY)) requestAnimationFrame(syncAria); } catch (e) {}
  });
})();
