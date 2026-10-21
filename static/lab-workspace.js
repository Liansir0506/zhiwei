/* 场景实验室：四个可演练场景（授权撤回 / 缓存失效 / 任务重试 / 版本回滚）+ 角色视角。
   前两个场景在本页真实执行并逐步展示结果；后两个引导到受控页面并说明边界。 */
window.LabWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text); return n;
  };
  let config, seq = 0;

  const step = (label, ok, detail) => {
    const row = el('div', 'lab-step' + (ok === true ? ' ok' : ok === false ? ' fail' : ''));
    row.append(el('span', 'lab-step-dot', ok === true ? '✓' : ok === false ? '✕' : '·'),
      el('span', 'lab-step-label', label));
    if (detail) row.append(el('span', 'lab-step-detail', detail));
    return row;
  };
  const call = async (method, path, body) => {
    const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', ...config.headers()},
      body: body ? JSON.stringify(body) : undefined, cache: 'no-store'});
    let data = null;
    try { data = await response.json(); } catch { /* 忽略非 JSON */ }
    return {status: response.status, data};
  };

  /* ---------- 场景一：授权撤回 ---------- */
  async function scenarioRevoke(panel) {
    const token = ++seq;
    const box = el('section', 'surface lab-scenario');
    box.append(el('h3', '', '场景一 · 授权撤回'), el('p', 'ui-help',
      '签发一个一次性临时凭据 → 用它读一次看板 → 吊销 → 再读应返回 401。用于演示权限即时生效与审计留痕。'));
    const trail = el('div', 'lab-trail');
    box.append(trail);
    const run = el('button', 'ui-btn primary', '开始演练'); run.type = 'button';
    run.onclick = async () => {
      run.disabled = true; trail.replaceChildren();
      // 每次演练用唯一身份与一次性凭据；finally 按该身份精确清理，不影响其他记录
      const stamp = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      const tempUser = 'lab-revoke-' + stamp;
      const tempKey = 'lab-revoke-key-' + stamp;
      let created = false;
      try {
        const issued = await call('POST', '/admin/keys', {key: tempKey, user_id: tempUser, roles: ['ops'], tenant_id: 'default'});
        created = issued.status === 200 || issued.status === 201;
        trail.append(step('签发临时凭据', created, created ? '已签发（一次性）' : `HTTP ${issued.status} ${(issued.data && issued.data.detail) || ''}`.trim()));
        if (!created) return;
        const beforeRes = await fetch('/dashboard/summary', {headers: {'X-API-Key': tempKey}, cache: 'no-store'});
        trail.append(step('临时凭据读看板', beforeRes.status === 200, `HTTP ${beforeRes.status}`));
        const perms = await call('GET', '/admin/permissions');
        const record = ((perms.data && perms.data.keys) || []).find(row => row.user_id === tempUser);
        const revoked = record ? await call('DELETE', '/admin/key-records/' + encodeURIComponent(record.id)) : {status: 0, data: null};
        created = false;  // 已按记录 ID 吊销，finally 不再重复处理
        trail.append(step('吊销该凭据', revoked.status === 200, record ? `记录 ${record.id} · HTTP ${revoked.status}` : '未找到本次签发记录'));
        const afterRes = await fetch('/dashboard/summary', {headers: {'X-API-Key': tempKey}, cache: 'no-store'});
        trail.append(step('吊销后再读', afterRes.status === 401, `HTTP ${afterRes.status}（预期 401）`));
        const mainRes = await fetch('/dashboard/summary', {headers: config.headers(), cache: 'no-store'});
        trail.append(step('当前凭据不受影响', mainRes.status === 200, `HTTP ${mainRes.status}`));
        trail.append(step('演练完成', true, `本次身份 ${tempUser} 已清理`));
      } catch (error) {
        trail.append(step('演练中断', false, error.message));
        if (created) trail.append(step('待清理', false, '本次凭据未走完流程，需要在访问管理手动吊销 ' + tempUser));
      } finally {
        // 兜底清理：无论成功或异常，都按本次唯一身份吊销
        if (created) {
          try {
            const perms = await call('GET', '/admin/permissions');
            const record = ((perms.data && perms.data.keys) || []).find(row => row.user_id === tempUser);
            if (record) {
              const cleanup = await call('DELETE', '/admin/key-records/' + encodeURIComponent(record.id));
              trail.append(step('兜底清理本次凭据', cleanup.status === 200, `记录 ${record.id} · HTTP ${cleanup.status}`));
            }
          } catch (error) {
            trail.append(step('兜底清理失败', false, error.message));
          }
        }
        run.disabled = false;
        if (token === seq) panel.append(box);
      }
    };
    const actions = el('div', 'action-row');
    actions.append(run);
    box.append(actions);
    panel.append(box);
  }

  /* ---------- 场景二：缓存失效 ---------- */
  async function scenarioCache(panel) {
    const box = el('section', 'surface lab-scenario');
    box.append(el('h3', '', '场景二 · 缓存失效'), el('p', 'ui-help',
      '连续读两次知识目录（第二次命中缓存）→ 调用失效接口 → 再读应重新计算。用于演示缓存行为与手动失效通道。'));
    const trail = el('div', 'lab-trail');
    box.append(trail);
    const run = el('button', 'ui-btn primary', '开始演练'); run.type = 'button';
    run.onclick = async () => {
      run.disabled = true; trail.replaceChildren();
      try {
        const read = async () => {
          const response = await fetch('/kb/catalog?page=1&page_size=3', {headers: config.headers(), cache: 'no-store'});
          const data = await response.json().catch(() => ({}));
          return data.cached === true;
        };
        const first = await read();
        trail.append(step('首次读取', true, first ? '命中既有缓存' : '未命中（重新计算）'));
        const second = await read();
        trail.append(step('第二次读取', true, second ? '命中缓存（cached=true）' : '仍未命中'));
        const invalidated = await call('POST', '/kb/cache/invalidate', {});
        trail.append(step('调用失效接口', invalidated.status === 200,
          invalidated.status === 200 ? `清除 ${invalidated.data.cleared_entries} 条缓存` : `HTTP ${invalidated.status}${invalidated.data && invalidated.data.detail ? '：' + invalidated.data.detail : ''}`));
        const third = await read();
        trail.append(step('失效后再读', true, third ? '仍命中（异常）' : '未命中（缓存已清空，符合预期）'));
      } catch (error) {
        trail.append(step('演练中断', false, error.message));
      } finally {
        run.disabled = false;
      }
    };
    const actions = el('div', 'action-row');
    actions.append(run);
    box.append(actions);
    panel.append(box);
  }

  /* ---------- 场景三：任务重试 ---------- */
  async function scenarioRetry(panel) {
    const box = el('section', 'surface lab-scenario');
    box.append(el('h3', '', '场景三 · 入库任务重试'), el('p', 'ui-help',
      '展示最近上传任务；失败任务可在“文档与入库”页发起真实重试。本页只读展示，不代触发。'));
    const holder = el('div', 'lab-jobs');
    box.append(holder);
    holder.append(el('p', 'meta-line', '正在读取上传任务…'));
    panel.append(box);
    try {
      const response = await fetch('/admin/ingest-workspace', {headers: config.headers(), cache: 'no-store'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      const uploads = (data.uploads || []).slice(0, 6);
      holder.replaceChildren();
      if (!uploads.length) { holder.append(el('p', 'notice', '当前没有上传任务。')); return; }
      const table = el('table', 'data-table lab-table card-table');
      table.append(el('thead', '')); table.firstChild.append(el('tr', ''));
      for (const h of ['文件', '状态', '片段', '时间']) table.firstChild.firstChild.append(el('th', '', h));
      const body = el('tbody', ''); table.append(body);
      for (const item of uploads) {
        const tr = el('tr', '');
        tr.append(el('td', '', item.filename || item.upload_id || '—'),
          el('td', '', item.status || '—'),
          el('td', '', item.chunk_count == null ? '—' : String(item.chunk_count)),
          el('td', 'lab-dim', item.created_at ? String(item.created_at).slice(0, 19).replace('T', ' ') : '—'));
        for (const [index, name] of ['文件', '状态', '片段', '时间'].entries()) tr.children[index].dataset.label = name;
        body.append(tr);
      }
      holder.append(table);
      const jump = el('button', 'ui-btn', '去文档与入库重试'); jump.type = 'button';
      jump.onclick = () => { if (config.go) config.go('docs', {}); };
      const actions = el('div', 'action-row');
      actions.append(jump);
      holder.append(actions);
    } catch (error) {
      holder.replaceChildren(el('p', 'notice warn', '上传任务读取失败：' + error.message));
    }
  }

  /* ---------- 场景四：版本回滚 ---------- */
  async function scenarioRollback(panel) {
    const box = el('section', 'surface lab-scenario');
    box.append(el('h3', '', '场景四 · 版本回滚'), el('p', 'ui-help',
      '回滚会切换全平台读取的知识快照，属于受控治理动作：只能在“版本与发布”页发起，需二次确认与门禁。本页只说明边界。'));
    const holder = el('div', 'lab-jobs');
    box.append(holder);
    holder.append(el('p', 'meta-line', '正在读取版本状态…'));
    panel.append(box);
    try {
      const response = await fetch('/kb/versions', {headers: config.headers(), cache: 'no-store'});
      const data = response.ok ? await response.json() : null;
      const versions = (data && data.versions) || [];
      const active = versions.find(v => v.status === 'ACTIVE');
      const archived = versions.filter(v => v.status === 'ARCHIVED');
      holder.replaceChildren();
      holder.append(el('p', '', `当前激活版本：${active ? active.version : '无'} · 可回滚目标（归档版）${archived.length} 个`));
      if (!active) holder.append(el('p', 'notice', '当前没有激活版本，暂无可回滚状态。'));
      const jump = el('button', 'ui-btn', '去版本与发布'); jump.type = 'button';
      jump.onclick = () => { if (config.go) config.go('versions', {}); };
      const actions = el('div', 'action-row');
      actions.append(jump);
      holder.append(actions);
    } catch (error) {
      holder.replaceChildren(el('p', 'notice warn', '版本状态读取失败：' + error.message));
    }
  }

  /* ---------- 场景五：演示数据备份 ---------- */
  async function scenarioBackup(panel) {
    const box = el('section', 'surface lab-scenario');
    box.append(el('h3', '', '场景五 · 业务样本导出'), el('p', 'ui-help',
      '按当前租户与授权范围导出关键业务表样本（JSON，单表最多 500 行）。这不是完整运维备份：导出后会标注每表总量与导出量，截断时请缩小范围分批导出。只读、不导出凭据；真实重置属危险操作，不做成按钮，请用受控脚本执行。'));
    const choices = el('div', 'lab-backup-choices');
    const tables = [['alarms', '告警'], ['devices', '设备'], ['work_orders', '工单'], ['change_records', '变更'], ['monitoring_metrics', '监控指标']];
    for (const [value, label] of tables) {
      const item = el('label', 'lab-backup-choice');
      const input = el('input'); input.type = 'checkbox'; input.value = value; input.checked = true;
      item.append(input, el('span', '', label)); choices.append(item);
    }
    box.append(choices);
    const trail = el('div', 'lab-trail');
    box.append(trail);
    const run = el('button', 'ui-btn primary', '导出备份（JSON）'); run.type = 'button';
    run.onclick = async () => {
      run.disabled = true; trail.replaceChildren();
      const picked = [...choices.querySelectorAll('input:checked')].map(i => i.value);
      if (!picked.length) { trail.append(step('未选择任何表', false, '至少选择一张表')); run.disabled = false; return; }
      trail.append(step('请求导出', null, picked.join('、')));
      try {
        const response = await fetch('/backup?tables=' + encodeURIComponent(picked.join(',')),
          {headers: config.headers(), cache: 'no-store'});
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          trail.append(step('导出失败', false, response.status === 403 ? '当前凭据无导出权限（需 admin / auditor）' : (data.detail || 'HTTP ' + response.status)));
          return;
        }
        const data = await response.json();
        const totals = data.totals || {};
        const counts = Object.entries(data.row_counts || {})
          .map(([k, v]) => `${k} ${v}/${totals[k] == null ? '—' : totals[k]}`).join('、');
        const truncated = Object.entries(data.row_counts || {})
          .filter(([k, v]) => (totals[k] || 0) > v).map(([k]) => k);
        trail.append(step('样本已生成', true, counts + '（导出/总量）'));
        trail.append(step(truncated.length ? '存在截断' : '未截断', !truncated.length,
          truncated.length ? truncated.join('、') + ' 未导完；需要完整数据请缩小范围分批导出' : '所有选中表均已导完'));
        trail.append(step('备份生成', true, counts));
        const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
        const link = el('a'); link.href = URL.createObjectURL(blob);
        link.download = `demo-backup-${new Date().toISOString().slice(0, 10)}.json`; link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 4000);
        trail.append(step('已触发下载', true, link.download));
      } catch (error) {
        trail.append(step('备份中断', false, error.message));
      } finally {
        run.disabled = false;
      }
    };
    const actions = el('div', 'action-row');
    actions.append(run);
    box.append(actions);
    panel.append(box);
  }

  /* ---------- 角色视角 ---------- */  /* ---------- 角色视角 ---------- */
  async function roles(panel) {
    const box = el('section', 'surface lab-scenario');
    box.append(el('h3', '', '角色视角'), el('p', 'ui-help',
      '按当前凭据的真实能力展示可见范围；切换角色需要更换凭据（访问管理页签发），页面不做假装有权限的视图。'));
    const holder = el('div', 'lab-roles');
    box.append(holder);
    holder.append(el('p', 'meta-line', '正在核对能力…'));
    panel.append(box);
    try {
      const response = await fetch('/admin/capabilities', {headers: config.headers(), cache: 'no-store'});
      const caps = response.ok ? await response.json() : null;
      holder.replaceChildren();
      if (!caps) { holder.append(el('p', 'notice warn', '能力核对失败（HTTP ' + response.status + '）。')); return; }
      const flags = [['can_read_ops', '运行状态'], ['can_read_eval', '评测记录'], ['can_read_permissions', '访问管理'],
        ['can_manage_keys', '凭据管理'], ['can_manage_versions', '版本管理'], ['can_read_domain', '业务域数据']];
      const grid = el('div', 'lab-role-grid');
      for (const [flag, label] of flags) {
        const cell = el('div', 'lab-role' + (caps[flag] ? ' ok' : ''));
        cell.append(el('span', '', label), el('b', '', caps[flag] ? '可见' : '不可见'));
        grid.append(cell);
      }
      holder.append(grid);
      holder.append(el('p', 'meta-line', `当前身份：${caps.user_id || '—'} · 角色 ${(caps.roles || []).join('、') || '—'} · 租户 ${caps.tenant_id || '—'}`));
    } catch (error) {
      holder.replaceChildren(el('p', 'notice warn', '能力核对失败：' + error.message));
    }
  }

  function load() {
    const host = $('labBody'); if (!host) return;
    ++seq;
    host.replaceChildren();
    scenarioRevoke(host);
    scenarioCache(host);
    scenarioRetry(host);
    scenarioRollback(host);
    scenarioBackup(host);
    roles(host);
  }
  function leave() { ++seq; }
  function init(options) { config = options; }
  return {init, load, leave};
})();
