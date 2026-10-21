/* 设备清单（告警与资源 · 设备页签）：只读 /devices，真实统计与状态分布。 */
window.DevicesWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text); return n;
  };
  const STATUS_LABELS = { online: '在线', offline: '离线', maintenance: '维护', retired: '退网' };
  const fmtNum = v => (v == null ? '—' : Number(v).toLocaleString('zh-CN'));
  let config, seq = 0, last = null, page = 1;

  async function load() {
    const host = $('devicesPane'); if (!host) return;
    const token = ++seq;
    host.replaceChildren(el('p', 'meta-line', '正在读取设备清单…'));
    try {
      const response = await fetch('/devices?page=' + page + '&page_size=20', { headers: config.headers(), cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (token !== seq) return;
      if (!response.ok) throw new Error(data.detail || ('读取失败（HTTP ' + response.status + '）'));
      last = data;
      render(host, data);
    } catch (error) {
      if (token !== seq) return;
      host.replaceChildren(el('p', 'notice warn', '设备清单读取失败：' + error.message));
    }
  }

  function render(host, data) {
    host.replaceChildren();
    const items = data.items || [];
    // 汇总：状态分布 + 设备类型
    const stats = el('div', 'alert-stats');
    const total = el('div', 'alert-stat');
    total.append(el('span', null, '设备总数'), el('b', null, fmtNum(data.total)));
    stats.append(total);
    for (const row of (data.summary && data.summary.by_status) || []) {
      const cell = el('div', 'alert-stat');
      cell.append(el('span', null, row.label || STATUS_LABELS[row.name] || row.name), el('b', null, fmtNum(row.count)));
      stats.append(cell);
    }
    host.append(stats);
    if (data.scope) host.append(el('p', 'meta-line', data.scope));
    if (!items.length) { host.append(el('p', 'alert-empty', '没有符合筛选条件的设备。')); return; }
    const table = el('table', 'data-table card-table');
    table.append(el('thead', '')); table.firstChild.append(el('tr', ''));
    for (const label of ['设备', '设备 ID', '类型', '厂家 / 型号', '软件版本', '状态', '部门']) table.firstChild.firstChild.append(el('th', null, label));
    const body = el('tbody', ''); table.append(body);
    for (const item of items) {
      const tr = el('tr', '');
      const cells = [
        [item.name || '—', '设备'],
        [item.device_id || '—', '设备 ID'],
        [item.device_type || '—', '类型'],
        [(item.vendor || '—') + ' / ' + (item.model || '—'), '厂家 / 型号'],
        [item.software_version || '—', '软件版本'],
        [STATUS_LABELS[item.status] || item.status || '—', '状态'],
        [item.dept_id || '—', '部门'],
      ];
      for (const [value, label] of cells) {
        const cell = el('td', null, value);
        cell.dataset.label = label;
        tr.append(cell);
      }
      body.append(tr);
    }
    host.append(table);
    const pager = el('div', 'alert-pager');
    const prev = el('button', 'ui-btn', '上一页'); prev.type = 'button'; prev.disabled = page <= 1;
    prev.onclick = () => { page -= 1; load(); };
    const info = el('span', 'meta-line', '第 ' + page + ' / ' + (data.pages || 1) + ' 页');
    const next = el('button', 'ui-btn', '下一页'); next.type = 'button'; next.disabled = page >= (data.pages || 1);
    next.onclick = () => { page += 1; load(); };
    pager.append(prev, info, next);
    host.append(pager);
  }

  function reset() { page = 1; }
  function init(options) { config = options; }
  return { init, load, reset };
})();
