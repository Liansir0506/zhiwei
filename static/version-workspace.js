/* Version workflow: authenticated, epoch guarded, no automatic mutation/retry.
   主屏只放：当前版本摘要、版本列表、详情与有效下一步；JSON/哈希/历史全部折叠。 */
window.VersionWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const UI_CLASSES = new Set(['detail-list','mt-3','mt-2','mt-4','action-row','section-head','code-line','surface','table-wrap','data-table','kv-grid','tech-details','tech-block','empty','notice','pill','ui-btn','ui-help','ui-input','ui-select','blocker-list','timeline','filter-bar','governance-row','knowledge-card','stack','two-col','version-item','version-item-top','surface version-summary','knowledge-meta','meta-line']);
  const el = (tag, a, b) => {
    const n = document.createElement(tag);
    if (b !== undefined) { if (a != null) n.textContent = String(a); if (b) n.className = b; }
    else if (a != null) { const text = String(a); if (UI_CLASSES.has(text)) n.className = text; else n.textContent = text; }
    return n;
  };
  let config, epoch = 0, navEpoch = 0, controller = null, active = false, caps = {}, index = null, selected = null;
  const valid = t => active && t === epoch && $('view-versions').classList.contains('active');
  const statusMap = { ACTIVE: ['当前激活', 'ok'], STAGED: ['候选', 'warn'], ARCHIVED: ['归档', 'muted'] };
  const go = (tab, params) => { if (typeof window.switchTab === 'function') window.switchTab(tab, params || {}); };

  function clear() {
    for (const id of ['versionList', 'versionDetail', 'versionHistory', 'versionSummary', 'versionCount']) {
      const node = $(id); if (node) node.replaceChildren();
    }
    $('versionStatus').textContent = ''; selected = null; index = null; caps = {};
    $('versionRuntimeHandoff')?.remove();
  }
  function leave() { epoch++; controller?.abort(); controller = null; active = false; clear(); }

  async function json(path, options = {}) {
    const request = new AbortController(), outer = options.signal || controller?.signal;
    const abort = () => request.abort(); outer?.addEventListener('abort', abort, { once: true }); if (outer?.aborted) abort();
    const timer = setTimeout(abort, 30000);
    try {
      const response = await fetch('/admin/version-workspace' + path, { cache: 'no-store', ...options, signal: request.signal, headers: { ...config.headers(), ...options.headers } });
      if (!response.ok) {
        let detail; try { detail = (await response.json()).detail; } catch {}
        throw new Error(typeof detail === 'string' ? detail : ({ 401: '登录已失效，请重新设置凭据', 403: '当前账号无权限', 503: '版本依赖暂不可用' })[response.status] || '请求失败 HTTP ' + response.status);
      }
      return response.json();
    } finally { clearTimeout(timer); outer?.removeEventListener('abort', abort); }
  }
  async function navigation() {
    // 版本与发布不再是独立侧栏入口（收拢到“知识管理”的子页签），这里只管理开发工具分组标题。
    const token = ++navEpoch, key = config.key();
    const heading = $('versionNavHeading');
    if (heading) heading.hidden = false;
    try {
      const c = await json('/capabilities');
      if (token === navEpoch && key === config.key()) { /* 可读性由页面自身 403 说明承担 */ }
    } catch { /* 能力探测失败：保持子页签可达，由页面提示受限 */ }
  }
  function credentialsChanged() { leave(); navigation(); }
  const post = (path, data) => json(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const pill = status => { const [label, cls] = statusMap[status] || [status, 'muted']; return el('span', label, 'pill ' + cls); };
  function note(box, text, cls = 'meta-line') { box.append(el('p', text, cls)); }
  function action(box, label, id, fn, primary = false) {
    const button = el('button', label, 'ui-btn' + (primary ? ' primary' : '')); if (id) button.id = id; box.append(button);
    button.onclick = async () => {
      const t = epoch, key = config.key(); button.disabled = true;
      const done=window.UIMotion?.busy(button);
      try { await fn(); } catch (error) {
        if (valid(t) && key === config.key()) $('versionStatus').textContent = error.name === 'AbortError' ? '读取超时或已离开；操作结果请刷新核对，不会自动重试' : error.message;
      } finally { done?.();if (button.isConnected) button.disabled = button.canEnable ? !button.canEnable() : false; }
    };
    return button;
  }

  function summaryCard() {
    const box = $('versionSummary');
    box.replaceChildren();
    if (!index) return;
    const active = index.versions.find(v => v.status === 'ACTIVE');
    const counts = index.versions.reduce((acc, v) => (acc[v.status] = (acc[v.status] || 0) + 1, acc), {});
    const card = el('section', 'surface version-summary');
    const head = el('div', 'section-head');
    head.append(el('h2', '当前知识版本'), active ? pill('ACTIVE') : el('span', '无激活版本', 'pill warn'));
    card.append(head);
    const kv = el('div', 'kv-grid');
    const entries = [['激活版本', active?.version || '—'],
      ['版本总数', '候选 ' + (counts.STAGED || 0) + ' · 归档 ' + (counts.ARCHIVED || 0)],
      ['最近发布', active?.published_at ? FmtTime.full(active.published_at) : '—'],
      ['发布人', active?.activated_by || '—']];
    for (const [name, value] of entries) { const cell = el('div'); cell.append(el('span', name), el('b', value)); kv.append(cell); }
    card.append(kv);
    const actions = el('div', 'action-row');
    actions.append(actionLink('浏览知识中心', () => go('kb')), actionLink('查看评测记录', () => go('eval')),
      actionLink('运行状态', () => go('ops')));
    card.append(actions);
    const notice = index.gaps?.length ? el('p', '版本存在长期质量缺口；不改变当前激活版本。', 'notice warn') : null;
    if (notice) card.append(notice);
    box.append(card);
  }
  function actionLink(label, handler) {
    const b = el('button', label, 'link-button'); b.type = 'button'; b.onclick = handler; return b;
  }

  function listCard(v) {
    const button = el('button', 'version-item'); button.type = 'button'; button.dataset.version = v.version;
    if (v.version === selected) button.dataset.selected = 'true';
    const top = el('div', 'version-item-top');
    top.append(el('strong', v.version), pill(v.status));
    button.append(top);
    const meta = el('p', 'meta-line');
    meta.textContent = v.status === 'ACTIVE'
      ? '发布于 ' + (v.published_at ? FmtTime.full(v.published_at) : '未记录') + ' · ' + (v.activated_by || '发布人未记录')
      : v.status === 'STAGED' ? '待审核候选 · 发布前需通过审核与门禁' : '归档版本 · 仅可追溯或经回滚重新激活';
    button.append(meta);
    button.onclick = () => {
      selected = v.version;
      for (const other of document.querySelectorAll('#versionList .version-item')) {
        if (other === button) other.dataset.selected = 'true'; else delete other.dataset.selected;
      }
      detail(v.version);
    };
    return button;
  }

  function handoffRecord(box, record, t) {
    const plan = record.inspection || {};
    const row = el('article', 'surface');
    const head = el('div', 'section-head');
    head.append(el('h3', '交接记录 ' + record.id), el('span', '历史 · 需重新核验', 'pill muted'));
    row.append(head);
    const kv = el('div', 'kv-grid');
    for (const [name, value] of [['当前服务', plan.current_version], ['恢复配置', plan.recovery_version],
      ['状态', '准备完成，未执行发布或回滚'], ['有效期', '准备后 15 分钟内需重新核验']]) {
      const cell = el('div'); cell.append(el('span', name), el('b', value || '—')); kv.append(cell);
    }
    row.append(kv);
    for (const blocker of plan.execution_blockers || []) row.append(el('p', blocker, 'notice bad'));
    const details = el('details', null, 'tech-details');
    details.append(el('summary', '完整配置、门禁与恢复阶段（JSON）'), el('pre', JSON.stringify(plan, null, 2), 'tech-block'));
    row.append(details);
    if (caps.can_manage_versions) {
      const actions = el('div', 'action-row');
      action(actions, '重新核验完整交接', null, async () => {
        const clickEpoch = epoch; const job = await post('/runtime-handoffs/' + record.id + '/revalidate', {});
        if (valid(clickEpoch)) handoffPoll(job.id, box, clickEpoch);
      });
      row.append(actions);
    }
    box.append(row);
  }
  async function handoffPoll(id, box, t) {
    if (!valid(t)) return;
    try {
      const job = await json('/runtime-handoff-jobs/' + id); if (!valid(t)) return;
      $('versionStatus').textContent = `完整交接任务 ${id} · ${job.state} · ${job.error || ''}`;
      if (['queued', 'running'].includes(job.state)) { setTimeout(() => handoffPoll(id, box, t), 1200); return; }
      if (job.result?.inspection) handoffRecord(box, job.result, t);
      if (job.result?.state === 'revalidated') note(box, '完整配置、双方快照和运行归属重新核验通过；未执行切换。');
    } catch (error) { if (valid(t)) $('versionStatus').textContent = '交接查询中断；请刷新核对持久结果。' + error.message; }
  }

  async function renderGate(version, t) {
    const gate = await json('/versions/' + encodeURIComponent(version) + '/gate');
    if (!valid(t) || selected !== version) return;
    const target = $('versionGate'); target.replaceChildren();
    if (!gate.recorded) { note(target, gate.note, 'empty'); return gate; }
    const head = el('div', 'section-head');
    head.append(el('h3', '结构门禁'), el('span', gate.report.passed ? '通过' : '阻断', 'pill ' + (gate.report.passed ? 'ok' : 'bad')));
    target.append(head);
    note(target, '门禁报告 SHA256 已记录；发布前仍须实时重验快照、撤回与绑定。', 'ui-help');
    const grid = el('div', 'two-col');
    for (const check of gate.report.checks || []) {
      const card = el('article', 'surface');
      card.append(el('strong', check.check));
      card.append(el('p', check.passed ? '通过' : '阻断', 'pill ' + (check.passed ? 'ok' : 'bad')));
      const details = el('details', null, 'tech-details');
      details.append(el('summary', '检查明细'), el('pre', JSON.stringify(check, null, 2), 'tech-block'));
      card.append(details); grid.append(card);
    }
    const details=el('details','tech-details');details.append(el('summary','查看各项检查结果'),grid);target.append(details);
    return gate;
  }

  async function detail(version, tIn = epoch) {
    selected = version;
    const token = ++epoch; const t = token;
    controller?.abort(); controller = new AbortController();
    const target = $('versionDetail');
    const loading = el('p', '正在核对 ' + version + ' 的绑定、台账与向量元数据（通常约 10 秒；期间可继续查看左侧列表）…', 'meta-line');
    target.replaceChildren(loading);
    try {
      const data = await json('/versions/' + encodeURIComponent(version));
      if (!valid(t) || selected !== version) return;
      target.replaceChildren();
      const head = el('div', 'section-head');
      head.append(el('h2', data.version), pill(data.status));
      target.append(head);
      note(target, data.scope);
      const snapshotCount = data.snapshot ? Object.values(data.snapshot.collections).reduce((a, b) => a + Number(b.count || 0), 0) : null;
      const kv = el('div', 'kv-grid');
      for (const [name, value] of [['来源文件', data.sources.length + ' 份'],
        ['快照片段', snapshotCount == null ? '未核验' : fmtNum(snapshotCount)],
        ['候选类型', data.candidate_kind === 'upload_delta' ? '增量上传候选（非完整替换库）' : '登记版本']]) {
        const cell = el('div'); cell.append(el('span', name), el('b', value)); kv.append(cell);
      }
      target.append(kv);
      const nextStep = data.status === 'STAGED'
        ? (caps.can_manage_versions ? '审核资料 → 检查门禁 → 准备发布 → 明确确认' : '联系管理员审核')
        : data.status === 'ACTIVE' ? '无需动作；如需变更请创建候选' : '如需恢复请准备回滚';
      const guidance=el('p', '下一步：' + nextStep, 'notice info mt-3');guidance.id='versionNextStep';target.append(guidance);

      const blockers = [...(data.blockers || []), ...(data.gaps || [])];
      if (blockers.length) {
        const box = el('section', 'mt-3');
        box.append(el('h3', '阻断项与缺口', 'section-head h2'));
        const ul = el('ul', 'blocker-list');
        for (const reason of blockers) ul.append(el('li', reason));
        box.append(ul); target.append(box);
      }

      // 来源：列表只显示文件名/准入/片段；完整字段折叠
      const sources = el('details', 'tech-details');
      sources.append(el('summary', '具体来源与批准范围（' + data.sources.length + '）'));
      const sourceGrid = el('div', 'stack');
      for (const source of data.sources) {
        const card = el('article', 'surface');
        const line = el('div', 'section-head');
        line.append(el('strong', source.filename), el('span', source.admission === 'inherited_approved' ? '沿用批准原件' : source.admission, 'pill ' + (source.admission === 'inherited_approved' ? 'ok' : 'warn')));
        card.append(line);
        card.append(el('p', `${source.chunks} 片段 · 来源 ID ${source.source_id}`, 'meta-line'));
        const tech = el('details', null, 'tech-details');
        tech.append(el('summary', 'SHA256 与批准范围'), el('pre', JSON.stringify({ sha256: source.sha256, scope: source.scope, approval: source.approval }, null, 2), 'tech-block'));
        card.append(tech);
        if (caps.can_manage_versions && source.admission === 'inherited_approved') {
          const actions = el('div', 'action-row');
          action(actions, '查看获准原件', null, async () => {
            const original = await json('/versions/' + encodeURIComponent(version) + '/sources/' + encodeURIComponent(source.filename));
            if (!valid(t)) return;
            const box = el('details', '', 'tech-details'); box.open = true;
            box.append(el('summary', '获准原件正文'), el('pre', original.text || original.note, 'tech-block'));
            card.append(box);
            if (original.truncated) note(card, '原件超过 64,000 字符，此处为截断预览。', 'ui-help');
          }, false);
          card.append(actions);
        }
        sourceGrid.append(card);
      }
      sources.append(sourceGrid); target.append(sources);

      for (const upload of data.uploads) {
        const row = el('p', `${upload.filename} · ${upload.admission} · ${upload.note}`, upload.admission === 'pending' ? 'notice warn' : 'meta-line');
        row.dataset.admission = upload.admission; target.append(row);
      }

      const gateBox = el('section', 'mt-3'); gateBox.id = 'versionGate';
      gateBox.append(el('h3', '结构门禁', 'section-head h2'));
      target.append(gateBox);
      await renderGate(version, t); if (!valid(t)) return;

      // 下一步动作
      const workflow = el('section', 'mt-3'); workflow.id = 'versionWorkflow';
      workflow.append(el('h3', '发布流程', 'section-head h2'));
      if(data.status==='STAGED'){
        const steps=el('ol',null,'flow-steps');steps.classList.add('version-steps');
        for(const [number,label] of ['工程审核','检查门禁','发布计划','确认执行'].entries()){const step=el('li');step.dataset.versionStep=String(number);step.dataset.state=number===0?'active':'waiting';step.append(el('b',String(number+1)),el('span',label));steps.append(step);}workflow.append(steps);
      }
      note(workflow, index.runtime_note || '审核、门禁与发布均为受控动作；任何修改后需重新审核。');
      const actions = el('div', 'action-row');

      if (caps.can_manage_versions && data.status === 'STAGED') {
        action(actions, '记录工程审核', 'versionReview', async () => {
          workflow.querySelectorAll('.version-step-actions,.version-plan-confirmation').forEach(node=>node.remove());
          const review = await post('/reviews', { version });
          if (!valid(t)) return;
          note(workflow, `审核 ${review.id} · 发起人 ${review.actor} · 绑定 ${review.fingerprint}；新资料准入 0。`);
          if (review.inspection.blockers.length) { note(workflow, '先解决以上来源、任务或绑定阻断，再重新审核。', 'notice bad'); return; }
          guidance.textContent='下一步：运行门禁检查；通过后才能准备发布计划。';
          $('versionReview')?.classList.remove('primary');
          workflow.querySelectorAll('[data-version-step]').forEach((step,index)=>step.dataset.state=index===0?'done':index===1?'active':'waiting');
          workflow.querySelectorAll('.version-step-actions').forEach(node=>node.remove());
          const next = el('div', 'action-row');
          next.classList.add('version-step-actions');
          if (data.candidate_kind === 'upload_delta') {
            action(next, '准备原件审核包', 'versionPreparePackage', async () => {
              const packet = await post('/packages', { review_id: review.id });
              if (!valid(t)) return;
              note(workflow, `审核包 ${packet.id} · ${packet.file_count} 份 / ${packet.chunk_count} 片段 · SHA256 ${packet.package_seal_sha256}`);
              note(workflow, packet.note);
              for (const item of packet.pending_excluded) note(workflow, `${item.filename} · pending，未纳入原件包`, 'notice warn');
            });
          }
          action(next, '运行门禁检查', 'versionRunGate', async () => {
            workflow.querySelector('[data-phase="plan"]').hidden=true;
            workflow.querySelectorAll('.version-plan-confirmation').forEach(node=>node.remove());
            const job = await post('/gate-jobs', { review_id: review.id });
            if (!valid(t)) return;
            poll(job.id, t, version);
          }, true);
          workflow.append(next);
          const pub = el('div', 'action-row');
          pub.classList.add('version-step-actions');pub.dataset.review=review.id;pub.dataset.phase='plan';pub.hidden=true;
          action(pub, '准备发布计划', 'versionPreparePlan', async () => preparePlan(workflow, version, 'publish', t), true);
          workflow.append(pub);
        }, true);
      }

      for (const job of data.gate_jobs || []) {
        note(workflow, `门禁任务 ${job.id} · ${job.state} · ${job.completed}/${job.total} · ${job.error || ''}`);
        if (['queued', 'running'].includes(job.state)) poll(job.id, t, version);
      }
      for (const packet of data.packages || []) note(workflow, `原件包 ${packet.id} · ${packet.state}（未入库） · ${packet.file_count} 份 / ${packet.chunk_count} 片段 · SHA256 ${packet.package_seal_sha256}。${packet.note}`);

      if (caps.can_manage_versions && data.status === 'ARCHIVED') {
        action(actions, '准备回滚计划', 'versionPreparePlan', async () => preparePlan(workflow, version, 'rollback', t), true);
      }
      if (actions.childElementCount) workflow.append(actions);
      if (!caps.can_manage_versions) note(workflow, '当前账号只读：审计员可查阅；审核、门禁与发布写操作需全局管理员执行。', 'ui-help');
      target.append(workflow);

      // 绑定与哈希（折叠）
      const binding = el('details', 'tech-details');
      binding.append(el('summary', '版本绑定与快照哈希（技术详情）'), el('pre', JSON.stringify({ binding: data.binding, snapshot: data.snapshot }, null, 2), 'tech-block'));
      target.append(binding);

      $('versionStatus').textContent = '核对完成；任何修改后需重新审核，刷新不会自动发布。';
    } catch (error) {
      if (valid(t)) { target.replaceChildren(el('p', error.name === 'AbortError' ? '读取超时；请手动刷新' : error.message, 'notice bad')); }
    }
  }

  async function preparePlan(workflow, version, mode, t) {
    workflow.querySelectorAll('.version-plan-confirmation').forEach(node=>node.remove());
    const plan = await post('/plans', { version, action: mode });
    if (!valid(t)) return;
    $('versionPreparePlan')?.classList.remove('primary');
    $('versionNextStep').textContent=plan.pointer_execution_enabled?'下一步：核对发布计划，输入完整确认内容后执行。':'下一步：先完成服务配置交接；当前计划尚不能执行发布或回滚。';
    const panel=el('section',null,'version-plan-confirmation');workflow.append(panel);
    workflow.querySelectorAll('[data-version-step]').forEach((step,index)=>step.dataset.state=index<3?'done':'active');
    note(panel, `${plan.from_version} → ${plan.version} · ${plan.scope} · 10 分钟内有效`);
    const input = el('input', null, 'ui-input'); input.id = 'versionConfirm'; input.placeholder = plan.confirmation;
    input.setAttribute('aria-label', '输入完整操作及版本确认字符串');
    panel.append(input);
    const actions = el('div', 'action-row');
    const executeButton=action(actions, '确认执行' + (mode === 'publish' ? '发布' : '回滚'), 'versionExecute', async () => {
      const result = await post('/executions', { plan_id: plan.id, confirmation: input.value });
      if (!valid(t)) return;
      const expectedEpoch = epoch + 2;
      await load();
      if (valid(expectedEpoch)) $('versionStatus').textContent = `已提交 ${result.from_version} → ${result.to_version} · 发起人 ${result.actor}；缓存${result.cache_invalidation_ok ? '已清理' : '清理失败，需运维处理'}；账本${result.receipt_saved ? '已写入' : '以SQL审计为准'}`;
    },true);
    executeButton.disabled=true;
    executeButton.canEnable=()=>plan.pointer_execution_enabled&&input.value===plan.confirmation;
    input.addEventListener('input',()=>{executeButton.disabled=!plan.pointer_execution_enabled||input.value!==plan.confirmation;});
    panel.append(actions);
    if (!plan.pointer_execution_enabled) {
      const button = actions.querySelector('button'); button.disabled = true;
      button.title = '需要完整服务配置发布交接，禁止仅改指针';
      note(panel, '原入口的发布/回滚暂不可执行：当前候选尚未具备完整服务配置交接。', 'notice warn');
    }
  }

  async function poll(id, t, version) {
    if (!valid(t) || selected !== version) return;
    try {
      const job = await json('/gate-jobs/' + id); if (!valid(t)) return;
      const status={queued:'排队中',running:'检查中',passed:'通过',blocked:'阻断',failed:'失败'}[job.state]||job.state;
      $('versionStatus').textContent = `门禁 ${id} · ${status} · ${job.completed}/${job.total} · ${job.error || ''}`;
      if (['queued', 'running'].includes(job.state)) { setTimeout(() => poll(id, t, version), 1200); return; }
      const gate=await renderGate(version, t);
      if(valid(t))$('versionNextStep').textContent=job.state==='passed'?'下一步：准备发布计划，核对范围与执行条件。':'下一步：解决门禁阻断项，再重新检查。';
      const next=document.querySelector('#versionWorkflow [data-phase="plan"]');
      if(valid(t)&&next&&next.dataset.review===String(job.review_id)&&job.state==='passed'&&gate?.report?.passed){next.hidden=false;$('versionRunGate')?.classList.remove('primary');document.querySelectorAll('#versionWorkflow [data-version-step]').forEach((step,index)=>step.dataset.state=index<2?'done':index===2?'active':'waiting');}
    } catch (error) { if (valid(t)) $('versionStatus').textContent = `任务 ${id} 查询中断；请手动刷新核对结果。${error.message}`; }
  }

  const fmtNum = n => new Intl.NumberFormat('zh-CN').format(n == null ? 0 : n);

  async function load(params = {}) {
    leave(); active = true; const t = epoch; controller = new AbortController();
    $('versionStatus').textContent = '正在核对版本权限…';
    try {
      caps = await json('/capabilities'); if (!valid(t)) return;
      if (!caps.can_read_versions) {
        $('versionStatus').textContent = '正在读取版本只读摘要…';
        try {
          const summary = await json('/summary'); if (!valid(t)) return;
          const box = $('versionList');
          box.replaceChildren();
          const counts = summary.counts || {};
          const total = (counts.ACTIVE || 0) + (counts.STAGED || 0) + (counts.ARCHIVED || 0);
          const head = el('article', 'surface');
          head.append(el('h3', '当前激活：' + (summary.active || '—')));
          head.append(el('p', `版本 ${total} 个 · 激活 ${counts.ACTIVE || 0} / 候选 ${counts.STAGED || 0} / 归档 ${counts.ARCHIVED || 0}`, 'meta-line'));
          head.append(el('p', '版本明细与发布/回滚操作记录仅管理员与审计员可见；当前账号只显示激活版本与状态计数。', 'meta-line'));
          box.append(head);
          if (summary.active) {
            const row = el('div', 'version-item');
            const top = el('div', 'version-item-top');
            top.append(el('strong', summary.active), pill('ACTIVE'));
            row.append(top);
            row.append(el('p', '当前服务正在读取的知识快照。', 'meta-line'));
            box.append(row);
          }
          $('versionStatus').textContent = summary.note || '只读摘要读取完成：仅显示激活版本与状态计数。';
        } catch (error) { $('versionStatus').textContent = '当前账号受限：版本审核仅 default 租户的全局管理员或审计员可查看；只读摘要暂不可用。'; }
        return;
      }
      index = await json(''); if (!valid(t)) return;
      $('versionStatus').textContent = index.scope;
      summaryCard();
      for (const gap of index.gaps || []) $('versionSummary').append(el('p', gap, 'notice warn'));
      const list = $('versionList');
      for (const v of index.versions) list.append(listCard(v));
      const count = $('versionCount'); if (count) count.textContent = index.versions.length + ' 个版本';

      if (index.execution_mode === 'review_only') {
        const box = el('details', null, 'surface management-details'); box.id = 'versionRuntimeHandoff';
        box.append(el('summary','完整服务配置交接（高级）'));
        const head = el('div', 'section-head'); head.append(el('h2', '完整服务交接'), el('span', '准备不切换 8010', 'pill muted'));
        box.append(head);
        note(box, '核对双方全部配置与快照；准备过程不切换 8010，历史记录保留。');
        if (caps.can_manage_versions) {
          const actions = el('div', 'action-row');
          action(actions, '准备完整服务交接', 'versionPrepareHandoff', async () => {
            const clickEpoch = epoch; const job = await post('/runtime-handoffs', { version: selected || index.versions[0]?.version });
            if (valid(clickEpoch)) handoffPoll(job.id, box, clickEpoch);
          });
          box.append(actions);
        }
        const handoffs = await json('/runtime-handoffs'); if (!valid(t)) return;
        for (const record of handoffs.items) handoffRecord(box, record, t);
        for (const job of handoffs.jobs) note(box, `交接任务 ${job.id} · ${job.state} · ${job.error || ''}`);
        $('versionDetail').closest('.two-col')?.after(box);
      }

      const history = await json('/history'); if (!valid(t)) return;
      const timeline = el('ul', 'timeline');
      for (const row of history.items) {
        const li = el('li');
        li.append(el('time', FmtTime.full(row.created_at)),
          (() => { const d = el('div'); d.append(el('strong', row.action), el('span', ' · ' + (row.from_version || '初次发布') + ' → ' + (row.to_version || '未记录') + ' · ' + row.actor, 'meta-line')); return d; })());
        timeline.append(li);
      }
      if (!history.items.length) $('versionHistory').append(el('p', '没有已提交的版本操作；不伪造历史。', 'empty'));
      else $('versionHistory').append(timeline);

      const preferred = params.version && index.versions.some(v => v.version === params.version) ? params.version : (index.versions.find(v => v.status === 'ACTIVE') || index.versions[0])?.version;
      if (preferred) await detail(preferred, t); else $('versionStatus').textContent = '当前库尚无版本记录。';
    } catch (error) {
      if (valid(t)) { clear(); $('versionStatus').textContent = error.name === 'AbortError' ? '读取超时；请手动刷新' : error.message; }
    }
  }

  function init(value) { config = value; $('versionRefresh').onclick = () => load(); navigation(); }
  return { init, load, leave, credentialsChanged };
})();
