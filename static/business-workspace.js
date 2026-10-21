/* Permission-bound read-only lists; stale requests never restore cleared data.
   业务页两种形态：隔离服务可用时是记录工作区；不可用时只有一个受限面板与恢复路径。 */
window.BusinessWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const UI_CLASSES = new Set(['detail-list','mt-3','mt-2','mt-4','action-row','section-head','code-line','surface','table-wrap','data-table','kv-grid','tech-details','tech-block','empty','notice','pill','ui-btn','ui-help','ui-input','ui-select','blocker-list','timeline','filter-bar','governance-row','knowledge-card','stack','two-col','version-item','version-item-top','surface version-summary','knowledge-meta','meta-line']);
  const node = (tag, a, b) => {
    const n = document.createElement(tag);
    if (b !== undefined) { if (a != null) n.textContent = String(a); if (b) n.className = b; }
    else if (a != null) { const text = String(a); if (UI_CLASSES.has(text)) n.className = text; else n.textContent = text; }
    return n;
  };
  const names = { alarms: '告警', devices: '设备', tickets: '工单', changes: '变更', integrations: '集成状态' };
  const keys = { alarms: 'alarm_id', devices: 'device_id', tickets: 'ticket_id', changes: 'change_id' };
  let config, epoch = 0, active = false, controller = null, kind = 'alarms', cursor = '', enabled = false;
  let pendingParams = null;
  const current = t => active && t.epoch === epoch && t.key === config.key();

  function clear() {
    for (const id of ['businessList', 'businessDetail', 'businessAccess']) $(id).replaceChildren();
    $('businessStatus').textContent = ''; $('businessScope').textContent = '';
    $('businessControls').hidden = true; $('businessNext').hidden = true;
    enabled = false; cursor = '';
  }
  function leave() { epoch++; controller?.abort(); controller = null; active = false; clear(); }
  function token() { epoch++; controller?.abort(); controller = new AbortController(); return { epoch, key: config.key(), signal: controller.signal }; }

  async function json(url, t) {
    const request = new AbortController(); let timedOut = false;
    const abort = () => request.abort(); t.signal.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(() => { timedOut = true; request.abort(); }, 15000);
    try {
      const r = await fetch(url, { cache: 'no-store', headers: config.headers(), signal: request.signal });
      if (!r.ok) {
        const error = new Error(({ 401: '登录已失效，请重新设置凭据。', 403: '当前凭据无此范围的读取权限。', 404: '实体不存在或不可访问。',
          422: '输入未通过校验。', 429: '请求过于频繁，请稍后手动刷新。', 503: '已准入快照、读取绑定或依赖不可用，请稍后手动刷新。' })[r.status] || '读取失败（HTTP ' + r.status + '）');
        error.status = r.status; throw error;
      }
      return await r.json();
    } catch (error) {
      if (timedOut) throw new Error('读取超时，未取得可验证结果；请稍后手动刷新。');
      if (error instanceof TypeError) throw new Error('网络连接中断，请检查连接后手动刷新。');
      if (error instanceof SyntaxError) throw new Error('服务响应无法读取，请手动刷新。');
      throw error;
    } finally { clearTimeout(timer); t.signal.removeEventListener('abort', abort); }
  }
  function failure(error, t) {
    if (!current(t) || error.name === 'AbortError') return;
    clear();
    $('businessStatus').textContent = error.message;
    const box = $('businessAccess');
    box.append(node('p', '读取结果已清空；请检查凭据和服务后手动刷新。', 'notice bad'));
    const retry = node('button', '重新核对', 'ui-btn'); retry.type = 'button'; retry.onclick = () => load();
    box.append(retry);
  }

  function fields(parent, data, skip = []) {
    const dl = node('dl', 'detail-list');
    for (const [key, value] of Object.entries(data || {})) {
      if (skip.includes(key) || value === null || value === undefined || value === '') continue;
      const row = node('div'); row.append(node('dt', key), node('dd', typeof value === 'object' ? JSON.stringify(value) : String(value)));
      dl.append(row);
    }
    parent.append(dl);
  }
  function button(label, click, cls = 'ui-btn') { const b = node('button', label, cls); b.type = 'button'; b.onclick = click; return b; }

  function links(target, group, rows, t) {
    if (!rows?.length) return;
    const box = node('section', 'mt-3');
    box.append(node('h3', names[group], 'section-head h2'));
    const row = node('div', 'action-row');
    for (const item of rows) {
      const id = item[keys[group]];
      if (!id) continue;
      const b = button(id, () => { if (current(t)) detail(group, id); }, 'ui-btn sm');
      b.dataset.kind = group; b.dataset.identity = id; row.append(b);
    }
    box.append(row); target.append(box);
  }

  async function detail(group, id) {
    const t = token();
    const target = $('businessDetail');
    target.classList.add('surface');
    target.replaceChildren(node('p', '正在重新核对关联与权限…', 'meta-line'));
    try {
      const d = await json('/business/' + group + '/' + encodeURIComponent(id), t);
      if (!current(t)) return;
      target.replaceChildren();
      const head = node('div', 'section-head');
      head.append(node('h2', names[group] + '详情'), node('span', '已准入模拟快照', 'pill info'));
      target.append(head);
      const idLine = node('div', 'code-line');
      idLine.append(node('code', id));
      const copy = node('button', '复制ID', 'copy-btn'); copy.type = 'button';
      copy.onclick = async () => { try { await navigator.clipboard.writeText(id); copy.textContent = '已复制'; } catch { copy.textContent = '复制失败'; } setTimeout(() => copy.textContent = '复制ID', 1500); };
      idLine.append(copy);
      target.append(idLine);
      target.append(node('p', '读取时间 ' + d.retrieved_at + ' · ' + d.record_time_basis, 'meta-line'));
      fields(target, d.entity, ['attributes_json']);
      if (d.entity?.attributes_json) {
        const details = node('details', null, 'tech-details');
        details.append(node('summary', '完整字段（JSON）'), node('pre', JSON.stringify(d.entity.attributes_json, null, 2), 'tech-block'));
        target.append(details);
      }
      const c = d.context || {};
      for (const [g, rows] of [['devices', c.device ? [c.device] : []], ['alarms', c.alarm ? [c.alarm] : (c.alarms || [])], ['tickets', c.work_orders], ['changes', c.changes]]) links(target, g, rows, t);
      const metrics = node('section', 'mt-3');
      metrics.append(node('h3', '关联监控快照', 'section-head h2'));
      for (const row of c.monitoring || []) fields(metrics, row);
      if (!c.monitoring?.length) metrics.append(node('p', '当前没有授权范围内的监控记录。', 'meta-line'));
      target.append(metrics);
      const assets = node('section', 'mt-3');
      assets.append(node('h3', '关联知识身份', 'section-head h2'));
      for (const row of c.knowledge || []) {
        const item = node('article', 'surface');
        item.append(node('p', (row.filename || row.asset_id) + ' · ' + (row.source_version || '修订未知'), 'meta-line'));
        item.append(node('p', '该关联身份不等于已验证正文；请通过只读诊断核对引用。', 'meta-line'));
        assets.append(item);
      }
      if (!c.knowledge?.length) assets.append(node('p', '当前没有可读的关联知识。', 'meta-line'));
      target.append(assets);
      if (c.truncated) target.append(node('p', '关联结果达到读取上限，不能视为完整关系集合。', 'notice warn'));
      const code = c.alarm?.alarm_code || c.alarms?.[0]?.alarm_code;
      const query = group === 'tickets' ? '请核对关联工单 ' + id + ' 的原文' : code ? code + ' 常见原因' : '请核对该设备的已关联知识证据';
      const actions = node('div', 'action-row');
      actions.append(button('带入只读诊断（手动提交）', () => {
        if (!current(t)) return;
        if (typeof window.switchTab === 'function') window.switchTab('agent', { query, alarm_id: c.alarm?.alarm_id || '' });
        else config.diagnose?.(d.diagnostic_target, query);
      }, 'ui-btn primary'));
      actions.append(button('返回记录列表', () => { if (current(t)) $('businessList').scrollIntoView({ block: 'start', behavior: 'smooth' }); }, 'ui-btn ghost'));
      target.append(actions);
      target.scrollIntoView({ block: 'start' });
    } catch (error) {
      if (current(t) && error.name !== 'AbortError') {
        target.replaceChildren(node('p', error.message, 'notice bad'));
        if ([401, 403, 503].includes(error.status)) failure(error, t);
      }
    }
  }

  async function list(after = '') {
    if (!enabled) return;
    const t = token();
    $('businessList').replaceChildren(); $('businessDetail').replaceChildren(); $('businessNext').hidden = true;
    $('businessStatus').textContent = '正在读取' + names[kind] + '…';
    try {
      const q = $('businessQuery').value.trim();
      const url = kind === 'integrations' ? '/business/integrations' : '/business/' + kind + '?' + new URLSearchParams({ query: q, after, limit: '20' });
      const d = await json(url, t); if (!current(t)) return;
      const target = $('businessList');
      $('businessScope').textContent = d.scope + ' · 读取时间 ' + d.retrieved_at;
      if (kind === 'integrations') {
        for (const row of d.adapters) {
          const card = node('article', 'surface');
          const head = node('div', 'section-head');
          head.append(node('h3', row.adapter_name), node('span', row.observed ? row.sync_status : '未同步 / 无账本',
            'pill ' + (row.observed ? 'ok' : 'muted')));
          card.append(head);
          fields(card, { 累计同步条数: row.items_synced, 上次同步时间: row.last_synced_at, 错误代码: row.last_error_code,
            模拟来源: row.simulation == null ? '未记录' : row.simulation ? '是' : '否' });
          card.append(node('p', '未进行实时连接探测；不代表外部系统可用。', 'meta-line'));
          target.append(card);
        }
        $('businessStatus').textContent = '只读同步账本；本页面不触发同步或重试。';
      } else {
        const wrap = node('div', 'table-wrap');
        const table = node('table', 'data-table');
        const thead = node('thead'), hr = node('tr');
        ['名称 / 编号', 'ID', '状态', '操作'].forEach(name => hr.append(node('th', name)));
        thead.append(hr); table.append(thead);
        const body = node('tbody');
        for (const row of d.items) {
          const id = row[keys[kind]], tr = node('tr');
          tr.append(node('td', row.name || row.title || row.alarm_code || id),
            node('td', id), node('td', row.status || '未知'),
            node('td', ''));
          const action = node('td');
          action.append(button('查看详情与关联', () => { if (active && enabled && config.key() === t.key) detail(kind, id); }, 'ui-btn sm'));
          tr.append(action); body.append(tr);
        }
        table.append(body); wrap.append(table);
        if (!d.items.length) target.append(node('p', '当前条件下没有可访问的记录；未显示其他范围的数量。', 'empty'));
        else target.append(wrap);
        cursor = d.next_cursor || '';
        $('businessNext').hidden = !cursor;
        $('businessStatus').textContent = '本页 ' + d.items.length + ' 条 · ' + names[kind] + ' · 已准入模拟快照';
      }
    } catch (error) { failure(error, t); }
  }

  function adminHelp(d, status) {
    const running = status?.status === 'running';
    const command = running ? '.\\scripts\\stop_readonly_workspace.ps1' : '.\\scripts\\start_readonly_workspace.ps1';
    const details = node('details', null, 'tech-details');
    details.append(node('summary', running ? '管理员帮助：停止隔离只读工作区' : '管理员帮助：启用隔离只读工作区'));
    details.append(node('p', running
      ? '隔离服务当前运行在 18021；停止会关闭服务并删除本次隔离凭据（不删除业务数据）：'
      : '在项目根目录的普通终端执行（会启动隔离测试存储与只读服务，18021 端口）：', 'ui-help'));
    const line = node('div', 'code-line');
    line.append(node('code', command));
    const copy = node('button', '复制命令', 'copy-btn'); copy.type = 'button';
    copy.onclick = async () => { try { await navigator.clipboard.writeText(command); copy.textContent = '已复制'; } catch { copy.textContent = '复制失败'; } setTimeout(() => copy.textContent = '复制命令', 1500); };
    line.append(copy);
    details.append(line);
    details.append(node('p', '本页不会、也无法从浏览器启动本机服务；启动完成后回到本页刷新。若提示“实现绑定已变化”，先执行 scripts/stop_readonly_workspace.ps1 再启动。', 'ui-help'));
    if (status?.reason) details.append(node('p', '当前状态原因：' + status.reason, 'ui-help'));
    return details;
  }

  function restricted(d, params) {
    const box = $('businessAccess');
    box.replaceChildren();
    const running = d.isolated?.status === 'running';
    const panel = node('section', 'surface');
    const head = node('div', 'section-head');
    head.append(node('h2', running ? '隔离只读服务已启用' : '隔离只读服务未启用'),
      node('span', running ? '可访问' : '受限', running ? 'pill ok' : 'pill warn'));
    panel.append(head);
    panel.append(node('p', running
      ? '业务记录与只读诊断在隔离工作区（18021）中；8010 只展示范围与入口，不代理业务数据、不伪造在线状态。'
      : '8010 只提供知识问答；告警、设备、工单、变更与集成账本放在隔离只读库中，本页不会伪造在线状态或列表。',
      running ? 'notice info' : 'notice warn'));
    const kv = node('div', 'kv-grid');
    const statusText = { running: '已启用', ready: '已启用', unavailable: '未启用', stopped: '未启用',
      starting: '启动中', degraded: '受限运行', unknown: '未核对' }[d.isolated?.status] || d.isolated?.status || '未核对';
    const items = [['服务状态', statusText],
      ['数据范围', d.notice || '仅已批准的 16 条领域资料'],
      ['当前筛选', params?.query ? names[params.kind || 'alarms'] + ' · ' + params.query : '未指定']];
    for (const [name, value] of items) { const cell = node('div'); cell.append(node('span', name), node('b', value)); kv.append(cell); }
    panel.append(kv);
    const actions = node('div', 'action-row');
    if (running) {
      const entry = node('a', '打开隔离业务工作区', 'ui-btn primary');
      entry.href = 'http://127.0.0.1:18021/'; entry.target = '_blank'; entry.rel = 'noopener';
      actions.append(entry);
    }
    actions.append(button('重新核对', () => load(params), running ? 'ui-btn' : 'ui-btn primary'));
    actions.append(button('查看总览统计', () => window.switchTab?.('dashboard'), 'ui-btn'));
    actions.append(button('去做运维问答', () => window.switchTab?.('qa'), 'ui-btn ghost'));
    panel.append(actions);
    panel.append(adminHelp(d, d.isolated));
    box.append(panel);
    $('businessStatus').textContent = running
      ? '隔离只读服务已运行；请从 18021 打开记录工作区（登录入口由启动脚本生成）。'
      : '本页当前为受限状态；记录工作区需要隔离只读服务。';
  }

  async function load(params = null) {
    leave(); active = true;
    pendingParams = params || pendingParams;
    const t = token();
    $('businessStatus').textContent = '正在核对服务与访问范围…';
    try {
      const d = await json('/business/availability', t); if (!current(t)) return;
      $('businessScope').textContent = d.notice;
      if (!d.enabled_here) { restricted(d, pendingParams); return; }
      enabled = true;
      $('businessControls').hidden = false;
      if (pendingParams) {
        if (names[pendingParams.kind]) { kind = pendingParams.kind; $('businessKind').value = kind; }
        if (typeof pendingParams.query === 'string') $('businessQuery').value = pendingParams.query;
        pendingParams = null;
      }
      await list();
    } catch (error) { failure(error, t); }
  }
  function credentialsChanged() { const reload = active; leave(); $('businessQuery').value = ''; if (reload) load(); }
  function init(options) {
    config = options;
    $('businessRefresh').onclick = () => load(pendingParams);
    $('businessKind').onchange = () => { kind = $('businessKind').value; list(); };
    $('businessFilter').onsubmit = e => { e.preventDefault(); list(); };
    $('businessNext').onclick = () => list(cursor);
    window.addEventListener('storage', e => { if (e.key === 'api_key' || e.key === null) credentialsChanged(); });
  }
  return { init, load, leave, credentialsChanged };
})();
