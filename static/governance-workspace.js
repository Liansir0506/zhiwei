/* Authenticated governance views; late responses cannot restore cleared data.
   运行状态 / 评测记录 / 访问管理：当前与历史分组，技术细节折叠，错误给原因与操作路径。 */
window.GovernanceWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const UI_CLASSES = new Set(['detail-list','mt-3','mt-2','mt-4','action-row','section-head','code-line','surface','table-wrap','data-table','kv-grid','tech-details','tech-block','empty','notice','pill','ui-btn','ui-help','ui-input','ui-select','blocker-list','timeline','filter-bar','governance-row','knowledge-card','stack','two-col','version-item','version-item-top','surface version-summary','knowledge-meta','meta-line']);
  const node = (tag, a, b) => {
    const n = document.createElement(tag);
    if (b !== undefined) { if (a != null) n.textContent = String(a); if (b) n.className = b; }
    else if (a != null) { const text = String(a); if (UI_CLASSES.has(text)) n.className = text; else n.textContent = text; }
    return n;
  };
  const areas = {
    ops: ['opsCards', 'opsRoute', 'opsHealth', 'opsHourly', 'opsBad', 'opsSummary', 'opsIssues'],
    eval: ['evalIndex', 'evalWorkflow', 'evalSla', 'evalGate', 'evalCards', 'evalCats', 'evalCases', 'evalSummary', 'evalGaps'],
    perm: ['permMatrix', 'permKeys', 'permCount'],
  };
  const features = { ops: 'can_read_ops', eval: 'can_read_eval', perm: 'can_read_permissions', business: 'can_read_business' };
  const titles = { ops: '运行状态', eval: '评测记录', perm: '访问管理', business: '业务与集成' };
  let config, active = null, epoch = 0, controller = null, caps = {}, busy = false;
  let navEpoch = 0, navController = null, navPending = null, evalEpoch = 0;
  const current = t => t.epoch === epoch && t.key === config.key() && active === t.view && $('view-' + t.view).classList.contains('active');

  function clear() {
    for (const ids of Object.values(areas)) for (const id of ids) $(id)?.replaceChildren();
    for (const v of Object.keys(areas)) {
      $(v + 'Snapshot').textContent = ''; $(v + 'Status').textContent = '';
      $(v + 'Body').hidden = true; $(v + 'Access').replaceChildren(); $(v + 'Access').hidden = true; $(v + 'Refresh').hidden = true;
    }
    $('keyIssuePanel').hidden = true; $('nkKey').value = ''; $('nkUser').value = ''; $('nkMsg').textContent = ''; caps = {};
    if($('permNewKey')){$('permNewKey').hidden=true;$('permNewKey').textContent='签发新凭据';$('permNewKey').classList.add('primary');}
  }
  function leave() { epoch++; evalEpoch++; controller?.abort(); controller = null; active = null; clear(); }

  function navigation(value = {}, notice) {
    const visible = [];
    for (const [view, flag] of Object.entries(features)) {
      const button = document.querySelector('.nav-item[data-tab="' + view + '"]');
      const allowed = value[flag] === true; button.hidden = !allowed; if (allowed) visible.push(titles[view]);
    }
    $('governanceNavHeading').hidden = !visible.length;
    $('capabilitiesStatus').textContent = notice || (visible.length ? '当前账号可查看：' + visible.join('、') : '当前账号未开放治理功能；可继续使用已授权的工作台。');
  }
  function invalidateNavigation() { navEpoch++; navController?.abort(); navController = null; navPending = null; navigation({}, '正在核对可用功能…'); }
  function credentialsChanged() { const v = active; invalidateNavigation(); leave(); config.credentialsReset?.(); if (v) load(v); else readCapabilities().catch(() => {}); }

  async function json(url, options = {}) {
    const r = await fetch(url, { cache: 'no-store', ...options, headers: { ...config.headers(), ...options.headers } });
    if (!r.ok) {
      const error = new Error(({ 401: '登录已失效，请重新设置凭据。', 403: '当前账号无此操作权限。', 409: '记录状态已变化，请刷新后重试。', 422: '填写内容未通过校验。', 429: '请求过于频繁，请稍后刷新。', 503: '服务暂不可用，请稍后刷新。' })[r.status] || '请求失败（HTTP ' + r.status + '）');
      error.status = r.status; throw error;
    }
    return r.json();
  }
  function readCapabilities() {
    const key = config.key(); if (navPending?.key === key) return navPending.promise;
    const token = ++navEpoch; navController?.abort(); const request = new AbortController(); navController = request;
    const promise = (async () => {
      try {
        const value = await json('/admin/capabilities', { signal: request.signal });
        if (token !== navEpoch || key !== config.key()) return null;
        if (!value || !Array.isArray(value.roles) || typeof value.tenant_id !== 'string' || [...Object.values(features), 'can_manage_keys'].some(flag => typeof value[flag] !== 'boolean')) throw new Error('权限信息不完整，请重新核对。');
        navigation(value); return value;
      } catch (error) {
        if (token === navEpoch && key === config.key() && error.name !== 'AbortError') navigation({}, error.message);
        throw error;
      } finally { if (token === navEpoch) { navPending = null; navController = null; } }
    })();
    navPending = { key, promise }; return promise;
  }

  function access(view, title, message, actions = true) {
    $(view + 'Body').hidden = true; $(view + 'Refresh').hidden = true;
    const box = $(view + 'Access'); box.replaceChildren(node('h2', title), node('p', message)); box.hidden = false;
    if (actions) {
      const buttons = node('div', 'action-row');
      const retry = node('button', '重新核对权限', 'ui-btn'); retry.onclick = () => load(view); buttons.append(retry);
      const back = node('button', '返回运维问答', 'ui-btn'); back.onclick = () => config.showQa?.(); buttons.append(back);
      const change = node('button', '更换凭据', 'ui-btn ghost'); change.onclick = () => config.showCredentials?.(); buttons.append(change);
      box.append(buttons);
    }
  }
  function denied(view) {
    const rules = { ops: '运行状态包含全局统计，仅 default 租户的运维、审计或管理员可查看。', eval: '评测记录仅 default 租户的审计员或管理员可查看。', perm: '访问管理仅审计员或管理员可查看，范围限当前租户。' };
    $(view + 'Status').textContent = '当前账号无此板块权限。';
    access(view, '当前账号无法查看' + titles[view], `当前角色：${caps.roles.join('、') || '无'}；当前租户：${caps.tenant_id}。${rules[view]}请使用具备权限的账号，或返回已授权的工作台。`);
  }
  function row(target, name, value) { const r = node('div', 'governance-row'); r.append(node('span', name), node('strong', value ?? '—')); target.append(r); }
  function kv(target, pairs) {
    const grid = node('div', 'kv-grid');
    for (const [name, value] of pairs) { const cell = node('div'); cell.append(node('span', name), node('b', value ?? '—')); grid.append(cell); }
    target.append(grid);
  }
  function card(target, name, value, note) { const c = node('article', 'knowledge-card'); c.append(node('h3', name), node('strong', value ?? '—', 'governance-value'), node('p', note, 'knowledge-meta')); target.append(c); }
  const pct = v => Number.isFinite(v) ? (v * 100).toFixed(1) + '%' : '—';
  function snapshot(view, data) { $(view + 'Snapshot').textContent = `读取时间：${FmtTime.full(data.snapshot?.at || new Date().toISOString())} · ${view === 'perm' ? '当前租户 ' + caps.tenant_id : data.snapshot?.scope || '范围未标注'}`; }

  function ops(d, o, h) {
    snapshot('ops', d);
    $('opsStatus').textContent = `环境 ${h.environment || '未知'} · 文档生成${h.generation_enabled ? '开启' : '关闭'} · 只读诊断${h.agent_enabled ? '开启' : '关闭'}；开启不代表质量验收通过。`;
    const components = h.components || {};
    const bad = Object.entries(components).filter(([, value]) => !String(value).startsWith('ok'));
    // 当前状态摘要
    const summary = node('section', 'surface');
    const head = node('div', 'section-head');
    head.append(node('h2', '当前服务状态'), node('span', bad.length ? bad.length + ' 个组件异常' : '全部组件可用', 'pill ' + (bad.length ? 'bad' : 'ok')));
    summary.append(head);
    kv(summary, [['环境', h.environment || '未知'], ['文档生成', h.generation_enabled ? '开启' : '关闭'],
      ['只读诊断', h.agent_enabled ? '开启' : '关闭'], ['激活知识版本', o.kb_active || '—'],
      ['缓存命中', `${d.cache.hits || 0}/${(d.cache.hits || 0) + (d.cache.misses || 0)}（当前进程）`],
      ['模型调用', `${d.llm_calls}（外部生成 API，重启后重置）`]]);
    $('opsSummary').replaceChildren(summary);
    // 异常与恢复路径
    const issues = node('section', 'surface');
    const ihead = node('div', 'section-head');
    ihead.append(node('h2', '异常与恢复路径'), node('span', bad.length || d.totals.bad_open ? '需要处理' : '无当前异常', 'pill ' + (bad.length || d.totals.bad_open ? 'warn' : 'ok')));
    issues.append(ihead);
    if (!bad.length && !d.totals.bad_open) {
      issues.append(node('p', '当前没有组件异常或未关闭的 bad case；历史统计不代表实时健康。', 'meta-line'));
    } else {
      const list = node('ul', 'blocker-list');
      for (const [name, value] of bad) list.append(node('li', `${name}：${value}。处理：检查对应容器/模型进程，修复后点“刷新”。`));
      if (d.totals.bad_open) list.append(node('li', `未关闭 bad case ${d.totals.bad_open} 条。处理：在“评测记录”查看来源，必要时回归验证。`));
      issues.append(list);
      const actions = node('div', 'action-row');
      const retry = node('button', '重新读取状态', 'ui-btn'); retry.onclick = () => load('ops'); actions.append(retry);
      issues.append(actions);
    }
    $('opsIssues').replaceChildren(issues);
    // 组件
    const health = node('div', 'stack');
    for (const [name, value] of Object.entries(components)) {
      const line = node('div', 'governance-row');
      const text = String(value);
      // 短状态词用 pill；长文本（错误详情等）用可换行的普通值，避免 pill 撑破移动端布局
      if (text.length <= 12) {
        line.append(node('span', name), node('span', text, 'pill ' + (text.startsWith('ok') ? 'ok' : 'bad')));
      } else {
        line.append(node('span', name), node('strong', text));
      }
      health.append(line);
    }
    if (!Object.keys(components).length) health.append(node('p', '服务未返回组件明细。', 'meta-line'));
    const tech = node('details', 'tech-details');
    tech.append(node('summary', '原始健康响应'), node('pre', JSON.stringify(h, null, 2), 'tech-block'));
    health.append(tech);
    $('opsHealth').replaceChildren(health);
    // 计数
    card($('opsCards'), '历史会话', d.totals.sessions, '数据库累计');
    card($('opsCards'), '历史消息', d.totals.messages, '数据库累计，所有消息角色');
    card($('opsCards'), '缓存命中率', pct(d.cache.hit_rate), `当前进程 ${d.cache.hits || 0}/${(d.cache.hits || 0) + (d.cache.misses || 0)}，重启后重置`);
    card($('opsCards'), '未关闭 Bad Case', d.totals.bad_open, '来自反馈与评测记录');
    // 路由分布（历史）
    const total = Object.values(d.route_distribution).reduce((a, b) => a + Number(b), 0);
    for (const [name, count] of Object.entries(d.route_distribution)) row($('opsRoute'), name, `${count}/${total} 条历史 assistant 消息`);
    if (!Object.keys(d.route_distribution).length) $('opsRoute').append(node('p', '暂无历史路由记录', 'empty'));
    // 消息趋势
    if ((d.hourly_messages || []).length) {
      const list = node('div', 'timeline');
      for (const rowItem of d.hourly_messages) {
        const line = node('div', 'governance-row');
        line.append(node('span', rowItem.hour || rowItem.at || '—'), node('strong', rowItem.count ?? rowItem.total ?? '—'));
        list.append(line);
      }
      $('opsHourly').append(list);
    } else {
      $('opsHourly').append(node('p', '最近 24 小时没有消息记录（数据库时钟）。', 'empty'));
    }
    if (d.totals.bad_open) {
      const link = node('button', '查看评测记录', 'ui-btn'); link.type = 'button'; link.onclick = () => load('eval');
      $('opsBad').append(node('p', `未关闭 bad case ${d.totals.bad_open} 条，建议按版本核对报告与反馈。`, 'notice warn'), link);
    } else {
      $('opsBad').append(node('p', '没有未关闭的 bad case。', 'meta-line'));
    }
  }

  function evaluation(d) {
    $('evalSummary').replaceChildren();
    const summary = node('section', 'surface');
    const head = node('div', 'section-head');
    head.append(node('h2', '报告摘要'), node('span', d.kind === 'publication_gate' ? '结构门禁' : '开发回归', 'pill info'));
    summary.append(head);
    const sourceName = String(d.source || '').split(/[\\/]/).pop() || '未记录';
    const modelMode = d.llm_mode === 'no_llm' ? '零模型（不调用 LLM）' : (d.llm_mode || '未记录');
    kv(summary, [['报告文件', sourceName], ['覆盖范围', d.scope], ['实际问答结果条数', d.sample_count],
      ['版本归属', d.attribution === 'current' ? '当前版本' : d.attribution === 'historical' ? '历史版本' : '归属不明'],
      ['报告时间', d.modified_at ? FmtTime.full(d.modified_at) : '未记录'], ['模型模式', modelMode]]);
    const tech = node('details', 'tech-details');
    tech.append(node('summary', '文件、SHA256 与版本归属依据'),
      node('pre', JSON.stringify({ file: d.file, sha256: d.sha256, version_basis: d.version_basis, dataset_sha256: d.dataset_sha256, body_check: d.body_check, split: d.split }, null, 2), 'tech-block'));
    summary.append(tech);
    $('evalSummary').append(summary);
    $('evalSla').replaceChildren();
    for (const [field, title] of [['route_ok', '路由检查'], ['hit_ok', '命中检查'], ['reject_ok', '拒答检查'], ['degraded', '降级比例']]) {
      const c = d.checks?.[field] || {}; card($('evalCards'), title, c.evaluated ? pct(c.ratio) : '未评测', `明确布尔记录 ${c.passed || 0}/${c.evaluated || 0}；不作为独立质量结论`);
    }
    for (const [field, title] of [['status_ok', '回答状态检查'], ['body_ok', 'FAQ 正文完整检查']]) {
      const c = d.checks?.[field] || {}; card($('evalCards'), title, c.evaluated ? pct(c.ratio) : '未评测', `${c.passed || 0}/${c.evaluated || 0} 条明确布尔记录；不证明语义正确`);
    }
    const durations = (d.results || []).map(r => r.elapsed_ms).filter(v => typeof v === 'number' && Number.isFinite(v) && v >= 0).sort((a, b) => a - b);
    if (durations.length) card($('evalCards'), '历史端到端 P95', durations[Math.ceil(durations.length * .95) - 1] + ' ms', `${durations.length} 个有效延迟样本`);
    $('evalGate').replaceChildren();
    for (const check of d.gate_checks || []) {
      const box = node('article', 'surface');
      const line = node('div', 'section-head'); line.append(node('strong', check.check), node('span', check.passed ? '通过' : '阻断', 'pill ' + (check.passed ? 'ok' : 'bad'))); box.append(line);
      const tech = node('details', 'tech-details'); tech.append(node('summary', '检查明细'), node('pre', JSON.stringify(check, null, 2), 'tech-block')); box.append(tech);
      $('evalGate').append(box);
    }
    if (!d.gate_checks?.length) $('evalGate').append(node('p', '此报告没有结构门禁记录。', 'empty'));
    $('evalCats').replaceChildren();
    for (const [name, c] of Object.entries(d.by_category || {})) row($('evalCats'), name, `${c.pass}/${c.evaluated ?? c.total} 条有效联合检查；总记录 ${c.total} 条`);
    if (!Object.keys(d.by_category || {}).length) $('evalCats').append(node('p', '暂无分类样本', 'empty'));
    $('evalCases').replaceChildren();
    for (const r of d.results || []) {
      const box = node('details', '', 'tech-details');
      box.append(node('summary', `${r.id || '未编号'} · ${r.category || '未分类'} · ${r.route || '未记录路由'}`),
        node('pre', `route_ok=${r.route_ok ?? '未评测'}\nhit_ok=${r.hit_ok ?? '未评测'}\nreject_ok=${r.reject_ok ?? '未评测'}\ndegraded=${r.degraded ?? '未记录'}\nelapsed_ms=${r.elapsed_ms ?? '未测量'}`, 'tech-block'));
      $('evalCases').append(box);
    }
    if (!d.results?.length) $('evalCases').append(node('p', '暂无评测用例；未显示达标状态', 'empty'));
  }

  async function evaluationIndex(index, t) {
    snapshot('eval', index);
    const target = $('evalIndex'); target.replaceChildren();
    const versions = node('select', null, 'ui-select'), list = node('select', null, 'ui-select');
    versions.id = 'evalVersion'; versions.setAttribute('aria-label', '评测知识版本');
    list.id = 'evalReport'; list.setAttribute('aria-label', '评测报告');
    const names = new Set([index.active_version, ...index.versions.map(v => v.version), ...index.reports.map(r => r.kb_version)].filter(Boolean));
    for (const name of names) { const option = node('option', name + (name === index.active_version ? '（当前激活）' : '')); option.value = name; versions.append(option); }
    const unknown = node('option', '未归属版本'); unknown.value = ''; versions.append(unknown);
    versions.value = index.active_version || '';
    const form = node('div', 'filter-bar'); form.append(versions, list); target.append(form);
    $('evalGaps').replaceChildren();
    const warnings = index.warnings || [];
    if (warnings.length) {
      const details = node('details', '', 'tech-details');
      details.append(node('summary', `无法读取的报告（${warnings.length}）`));
      for (const warning of warnings) details.append(node('p', warning.file + '：' + warning.reason, 'meta-line'));
      $('evalGaps').append(details);
    }
    const gapText = node('p', '缺少已验证的独立评测结果；结构门禁与开发自回归均不填补这一缺口。', 'notice warn');
    $('evalGaps').append(gapText);

    async function selectReport() {
      const selection = ++evalEpoch; $('evalSummary').replaceChildren();
      for (const id of ['evalSla', 'evalGate', 'evalCards', 'evalCats', 'evalCases']) $(id).replaceChildren();
      const id = list.value;
      if (!id) { $('evalSummary').append(node('p', '该版本没有可读报告；没有评测结果。', 'notice')); return; }
      $('evalSla').append(node('p', '正在读取报告…', 'meta-line'));
      try {
        const d = await json('/admin/eval/reports/' + encodeURIComponent(id), { signal: controller.signal });
        if (!current(t) || selection !== evalEpoch) return;
        $('evalSla').replaceChildren();
        evaluation({ ...d, snapshot: index.snapshot, report: { ...d, dataset: d.dataset, independent_evaluation: d.independent_evaluation } });
        $('evalStatus').textContent = (d.attribution === 'current' ? '当前版本' : d.attribution === 'historical' ? '历史版本' : '归属不明') + ' · ' + d.purpose + '。' + d.independent_evaluation + '。';
      } catch (error) {
        if (current(t) && selection === evalEpoch && error.name !== 'AbortError') { $('evalSla').replaceChildren(node('p', error.message, 'notice bad')); }
      }
    }
    function workflow() {
      const box = $('evalWorkflow'); box.replaceChildren(node('p', index.workflow, 'meta-line'));
      box.append(node('p', '独立评测缺口：需新未暴露样本、人工真值和冻结审核；本入口不会读取 pending/frozen 题，也不会调用 DeepSeek。', 'notice warn'));
      for (const job of index.jobs || []) {
        const entry = node('div', 'governance-row');
        entry.append(node('span', `${job.id} · ${job.kb_version} · 发起人 ${job.requested_by || '未记录'}`), node('strong', `${job.state} · ${job.completed}/${job.case_count}`));
        if (job.error) entry.append(node('p', job.error, 'notice bad'));
        box.append(entry);
      }
      if (!index.can_run) { box.append(node('p', '当前审计员只读；只有 default 租户管理员可准备和执行。', 'meta-line')); return; }
      if (versions.value !== index.active_version) { box.append(node('p', '仅允许对当前激活版本准备计划；历史报告保留原归属。', 'meta-line')); return; }
      const prepare = node('button', '准备零模型 FAQ 回归计划', 'ui-btn'), status = node('p', '', 'meta-line');
      prepare.id = 'evalPrepare'; status.id = 'evalJobStatus';
      const actions = node('div', 'action-row'); actions.append(prepare); box.append(actions);
      box.append(status);
      const token = evalEpoch; const valid = () => current(t) && token === evalEpoch;
      prepare.onclick = async () => {
        if (!valid()) return; prepare.disabled = true; status.textContent = '正在准备当前版本与可读 FAQ 绑定…';
        try {
          const plan = await json('/admin/eval/plans', { method: 'POST' });
          if (!valid()) return;
          status.textContent = `计划 ${plan.id} · ${plan.kb_version} · ${plan.case_count} 题 · no_llm · 有效期至 ${new Date(plan.expires_at * 1000).toLocaleTimeString()}。${plan.scope}`;
          const execute = node('button', '执行本计划（零模型）', 'ui-btn primary'); execute.id = 'evalExecute'; box.append(execute);
          execute.onclick = async () => {
            if (!valid()) return; execute.disabled = true;
            try {
              const job = await json('/admin/eval/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan_id: plan.id }) });
              if (!valid()) return;
              async function poll() {
                if (!valid()) return;
                try {
                  const state = await json('/admin/eval/jobs/' + job.id, { signal: controller.signal });
                  if (!valid()) return;
                  status.textContent = `任务 ${state.id} · ${state.state} · ${state.completed}/${state.case_count} · 模型调用 0。${state.error || ''}`;
                  if (['queued', 'running'].includes(state.state)) setTimeout(poll, 1000);
                  else {
                    prepare.disabled = false; execute.remove();
                    if (state.report_id) {
                      const refresh = node('button', '查看本次报告', 'ui-btn'); refresh.id = 'evalViewResult';
                      refresh.onclick = () => load('eval'); box.append(refresh);
                    }
                  }
                } catch (error) { if (valid()) { status.textContent = error.message + '；可刷新查看任务。任务标识：' + job.id; prepare.disabled = false; } }
              }
              poll();
            } catch (error) { if (valid()) { status.textContent = error.message + '；请重新准备。'; execute.remove(); prepare.disabled = false; } }
          };
        } catch (error) { if (valid()) { status.textContent = error.message; prepare.disabled = false; } }
      };
    }
    async function selectVersion() {
      list.replaceChildren();
      const selected = index.reports.filter(r => (r.kb_version || '') === versions.value);
      for (const report of selected) {
        const option = node('option', `${report.file} · ${report.kind === 'publication_gate' ? '结构门禁' : '开发回归'} · ${report.sample_count} 条问答记录`);
        option.value = report.id; list.append(option);
      }
      if (!selected.length) { const option = node('option', '该版本暂无报告'); option.value = ''; list.append(option); }
      $('evalGaps').lastChild.textContent = `${versions.value === index.active_version ? '当前激活版本' : versions.value ? '历史版本' : '未归属版本'}：${selected.length} 份报告。缺少已验证的独立评测结果。`;
      workflow();
      selectReport();
    }
    versions.onchange = () => { evalEpoch++; $('evalWorkflow').replaceChildren(); selectVersion(); };
    list.onchange = () => { evalEpoch++; $('evalWorkflow').replaceChildren(); selectReport(); };
    selectVersion();
  }

  function permissions(d, t) {
    snapshot('perm', d);
    $('permStatus').textContent = `当前角色：${caps.roles.join('、')} · 当前租户：${caps.tenant_id} · ${caps.can_manage_keys ? '可管理同租户凭据' : '只读，不可签发或吊销凭据'}。实际接口逐次鉴权。`;
    const count = $('permCount'); if (count) count.textContent = d.keys.length + ' 条';
    $('permKeys').replaceChildren();
    const wrapper = node('div', 'table-wrap'), table = node('table', 'data-table');
    const head = node('thead'), hr = node('tr');
    for (const title of ['用户', '角色', '租户 / 部门', '凭据标识', '状态', '操作']) hr.append(node('th', title));
    head.append(hr); table.append(head);
    const body = node('tbody');
    for (const key of d.keys) {
      const r = node('tr');
      for (const text of [key.user_id, key.roles.join('、'), `${key.tenant_id || caps.tenant_id} / ${key.dept_id || '未限定'}`, key.key_hint, key.enabled ? '启用' : '已吊销'])
        r.append(node('td', text));
      const action = node('td');
      if (caps.can_manage_keys && key.enabled) {
        const button = node('button', '吊销此凭据', 'ui-btn sm danger');
        button.onclick = async () => {
          if (!current(t) || !confirm(`吊销 ${key.user_id} 的这条凭据？操作会立即失效。`)) return;
          button.disabled = true;
          try { await json('/admin/key-records/' + encodeURIComponent(key.id), { method: 'DELETE' }); if (current(t)) await load('perm'); }
          catch (e) { if (current(t)) { action.append(node('p', e.message, 'notice bad')); button.disabled = false; } }
        };
        action.append(button);
      } else action.textContent = '只读';
      r.append(action); body.append(r);
    }
    table.append(body); wrapper.append(table);
    $('permKeys').append(wrapper);
    if (!d.keys.length) $('permKeys').append(node('p', '当前租户暂无凭据记录', 'empty'));
    $('permMatrix').replaceChildren();
    for (const entry of d.matrix) {
      const c = node('article', 'surface');
      const line = node('div', 'section-head'); line.append(node('strong', entry.role), node('span', entry.desc, 'meta-line')); c.append(line);
      c.append(node('p', entry.apis.join(' · '), 'meta-line'));
      $('permMatrix').append(c);
    }
    $('keyIssuePanel').hidden = true;
    $('permNewKey').hidden = !caps.can_manage_keys;
    $('nkCreate').disabled = busy;
  }

  async function load(view) {
    leave(); active = view; controller = new AbortController();
    const t = { epoch: ++epoch, key: config.key(), view };
    $(view + 'Status').textContent = '正在核对权限并读取数据…';
    access(view, '正在核对可用功能', '读取完成后显示当前账号可查看的内容。', false);
    try {
      const freshCaps = await readCapabilities(); if (!freshCaps || !current(t)) return;
      caps = freshCaps;
      if (!(caps[features[view]] === true)) { denied(view); return; }
      if (view === 'ops') {
        const [d, o, h] = await Promise.all([json('/admin/ops', { signal: controller.signal }), json('/admin/overview', { signal: controller.signal }), json('/health?refresh=1', { signal: controller.signal })]);
        if (current(t)) ops(d, o, h);
      } else {
        const d = await json(view === 'eval' ? '/admin/eval/reports' : '/admin/permissions', { signal: controller.signal });
        if (current(t)) {
          if (view === 'eval') {
            await evaluationIndex(d, t);
            if (current(t)) {
              const count = (d.reports || []).length;
              $('evalStatus').textContent = `已读取 ${count} 份报告；选择版本与报告查看来源、范围、结果与门禁。报告按显式知识版本归属，历史不转移。`;
            }
          } else permissions(d, t);
        }
      }
      if (current(t)) { $(view + 'Access').hidden = true; $(view + 'Body').hidden = false; $(view + 'Refresh').hidden = false; }
    } catch (error) {
      if (current(t) && error.name !== 'AbortError') {
        clear(); navigation({}, error.message);
        $(view + 'Status').textContent = error.message;
        access(view, '暂时无法查看' + titles[view], error.message);
      }
    }
  }

  async function issue() {
    if (busy || active !== 'perm' || !caps.can_manage_keys) return;
    const key = $('nkKey').value.trim(), user = $('nkUser').value.trim(), roles = $('nkRoles').value.split(',').map(v => v.trim()).filter(Boolean);
    if (key.length < 8 || key.length > 64 || !user || !roles.length || roles.some(v => !['guest', 'ops', 'auditor', 'admin'].includes(v))) {
      $('nkMsg').textContent = '凭据需 8～64 字符，用户必填，角色仅支持 guest/ops/auditor/admin。'; return;
    }
    if (!confirm(`为同租户 ${caps.tenant_id} 的 ${user} 签发凭据（${roles.join('、')}）？`)) return;
    const t = { epoch, key: config.key(), view: active }; busy = true; $('nkCreate').disabled = true;
    try {
      await json('/admin/keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, user_id: user, roles, tenant_id: caps.tenant_id }) });
      if (current(t)) { await load('perm'); if (active === 'perm' && config.key() === t.key) $('nkMsg').textContent = '已签发。请使用提交前自行保存的凭据；页面已清除明文。'; }
    } catch (error) { if (current(t)) $('nkMsg').textContent = error.message; }
    finally { busy = false; if (active === 'perm') $('nkCreate').disabled = false; }
  }

  function init(options) {
    config = options;
    for (const view of Object.keys(areas)) $(view + 'Refresh').onclick = () => load(view);
    $('nkCreate').onclick = issue;
    $('permNewKey').onclick=()=>{if(active!=='perm'||!caps.can_manage_keys)return;const open=$('keyIssuePanel').hidden;$('keyIssuePanel').hidden=!open;$('permNewKey').textContent=open?'取消签发':'签发新凭据';$('permNewKey').classList.toggle('primary',!open);if(open)$('nkUser').focus();else{$('nkKey').value='';$('nkUser').value='';$('nkMsg').textContent='';}};
    $('capabilitiesRetry').onclick = credentialsChanged;
    window.addEventListener('storage', event => { if (event.key === 'api_key' || event.key === null) credentialsChanged(); });
    clear(); navigation({}, '正在核对可用功能…'); readCapabilities().catch(() => {});
  }
  return { init, load, leave, credentialsChanged };
})();
