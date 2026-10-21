/* 按任务分开问答问题、案例审核、资料待办与 FAQ 回归。
   问题单与案例走服务端；资料待办保存在本机 localStorage，可导出。 */
window.GovernanceKbWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text); return n;
  };
  const gapStore = 'knowledge_gaps_v1';
  const fmtNum = v => (v == null ? '—' : Number(v).toLocaleString('zh-CN'));
  const fmtTime = v => { if (!v) return '—'; const d = new Date(v); return isNaN(d) ? String(v) : d.toLocaleString('zh-CN', {hour12: false}); };
  let config, seq = 0;

  /* ---------------- 缺口登记（本地闭环） ---------------- */
  const loadGaps = () => { try { return JSON.parse(localStorage.getItem(gapStore) || '[]'); } catch { return []; } };
  const saveGaps = list => localStorage.setItem(gapStore, JSON.stringify(list));
  const gapStatusLabel = {open: '待补资料', supplied: '已补待回归', closed: '已关闭'};

  /* ---------------- tab 框架 ---------------- */
  const TABS = [
    {id:'issues',label:'问答问题',render:async panel=>{const token=++seq;panel.replaceChildren();await renderKbFixIssues(panel,'issues',token);}},
    {id:'cases',label:'案例审核',render:async panel=>{const token=++seq;panel.replaceChildren();await renderCaseDrafts(panel,token);}},
    {id:'gap',label:'资料待办',render:renderGap},
    {id:'qa',label:'FAQ与回归',render:renderQa},
  ];
  function frame() {
    const host=$('gkBody');if(!host)return null;
    const bar=el('div','sub-tabs gk-tabs');bar.setAttribute('role','tablist');bar.setAttribute('aria-label','知识问题处理');
    const panel=el('div','gk-panel');panel.id='gkTaskPanel';panel.setAttribute('role','tabpanel');
    let show;
    for(const tab of TABS){const button=el('button','gk-tab',tab.label);button.type='button';button.dataset.tab=tab.id;button.setAttribute('role','tab');button.setAttribute('aria-controls',panel.id);button.onclick=()=>show(tab.id);bar.append(button);}
    show=id=>{const tab=TABS.find(item=>item.id===id)||TABS[0];bar.querySelectorAll('button').forEach(button=>{const on=button.dataset.tab===tab.id;button.classList.toggle('active',on);button.setAttribute('aria-selected',String(on));});return Promise.resolve(tab.render(panel,tab.id)).catch(error=>panel.append(el('p','notice warn','暂时无法读取：'+error.message+'，请刷新重试。')));};
    host.append(bar,panel);return {panel,show};
  }

  /* ---------------- 问答 tab ---------------- */
  async function renderQa(panel, tab) {
    const token = ++seq;
    panel.replaceChildren(el('p', 'meta-line', '正在读取 FAQ 与最近回归结果…'));
    const [faqRes, reportRes] = await Promise.all([
      fetch('/kb/catalog?page=1&page_size=30&kind=faq', {headers: config.headers(), cache: 'no-store'}).catch(() => null),
      fetch('/admin/eval/reports', {headers: config.headers(), cache: 'no-store'}).catch(() => null),
    ]);
    if (token !== seq) return;
    panel.replaceChildren();
    const faq = faqRes && faqRes.ok ? await faqRes.json().catch(() => null) : null;
    if (!faq) { panel.append(el('p', 'notice warn', 'FAQ 目录读取失败，请稍后重试。')); return; }
    const items = faq.items || [];
    const head = el('div', 'gk-qa-head');
    head.append(el('div', '', `FAQ ${fmtNum(faq.total)} 条 · 当前版本 ${faq.kb_version || '—'}`));
    panel.append(head);
    if (!items.length) { panel.append(el('p', 'notice', '当前权限下没有可读 FAQ。')); return; }
    const list = el('div', 'gk-faq-list');
    for (const item of items) {
      const row = el('div', 'gk-faq-row');
      row.append(el('span', 'gk-faq-q', item.question || item.title || '（无题）'));
      const meta = el('span', 'gk-faq-meta', item.source || '—');
      row.append(meta);
      const ask = el('button', 'ui-btn', '去问答'); ask.type = 'button';
      ask.onclick = () => { if (config.go) config.go('qa', {query: item.question || item.title || ''}); };
      row.append(ask);
      list.append(row);
    }
    panel.append(list);
    if (reportRes && !reportRes.ok) {
      panel.append(el('p', 'notice', '回归状态仅管理员 / 审计员可查看；当前账号不展示评测网格。'));
    } else if (reportRes && reportRes.ok) {
      const report = await reportRes.json().catch(() => null);
      const latest = (report && report.reports || [])[0];
      if (latest) {
        const detailRes = await fetch('/admin/eval/reports/' + encodeURIComponent(latest.id), {headers: config.headers(), cache: 'no-store'}).catch(() => null);
        if (token !== seq) return;
        const detail = detailRes && detailRes.ok ? await detailRes.json().catch(() => null) : null;
        const box = el('section', 'surface gk-regression');
        box.append(el('h3', '', `最近回归：${latest.kb_version || '—'} · ${fmtNum(latest.sample_count)} 题 · ${fmtTime(latest.created_at)}`));
        const checks = (detail && detail.checks) || {};
        const grid = el('div', 'gk-check-grid');
        const labelMap = {route_ok: '路由', hit_ok: '命中', status_ok: '状态', body_ok: '正文', degraded: '未降级', reject_ok: '拒答'};
        for (const [key, value] of Object.entries(checks)) {
          const ratio = value.ratio;
          const cell = el('div', 'gk-check' + (ratio === 1 ? ' ok' : ratio == null ? ' na' : ' warn'));
          cell.append(el('b', '', `${labelMap[key] || key}`),
            el('span', '', `${fmtNum(value.passed)}/${fmtNum(value.evaluated)}`),
            el('span', 'gk-check-ratio', ratio == null ? '未评' : Math.round(ratio * 100) + '%'));
          grid.append(cell);
        }
        box.append(grid);
        const jump = el('button', 'ui-btn', '查看评测记录'); jump.type = 'button';
        jump.onclick = () => { if (config.go) config.go('eval', {}); };
        box.append(el('div', 'action-row', )); box.lastChild.append(jump);
        panel.append(box);
      }
    }
  }

  /* ---------------- 资料 tab ---------------- */
  async function renderMaterial(panel, tab) {
    const token = ++seq;
    panel.replaceChildren(el('p', 'meta-line', '正在读取文档资料…'));
    const res = await fetch('/kb/catalog?page=1&page_size=30&kind=document', {headers: config.headers(), cache: 'no-store'}).catch(() => null);
    if (token !== seq) return;
    panel.replaceChildren();
    if (!res || !res.ok) { panel.append(el('p', 'notice warn', '资料目录读取失败，请稍后重试。')); return; }
    const data = await res.json().catch(() => null);
    if (token !== seq) return;
    const items = (data && data.items) || [];
    const synthetic = items.filter(i => i.synthetic).length;
    panel.append(el('div', 'gk-qa-head', `文档 ${fmtNum(data && data.total)} 份 · 当前版本 ${(data && data.kb_version) || '—'} · 其中标记模拟 ${synthetic} 份`));
    if (!items.length) { panel.append(el('p', 'notice', '当前权限下没有可读文档。')); return; }
    const table = el('table', 'data-table gk-table card-table');
    table.append(el('thead', '', ''));
    table.firstChild.append(el('tr', '', ''));
    for (const h of ['标题', '来源', '属性', '片段']) table.firstChild.firstChild.append(el('th', '', h));
    const body = el('tbody', ''); table.append(body);
    for (const item of items) {
      const tr = el('tr', '');
      tr.append(el('td', '', item.title || '（无题）'), el('td', 'gk-dim', item.source || '—'),
        el('td', '', item.synthetic ? '模拟' : '未标'), el('td', 'gk-dim', fmtNum(item.fragments)));
      for (const [index, name] of ['标题', '来源', '属性', '片段'].entries()) tr.children[index].dataset.label = name;
      body.append(tr);
    }
    panel.append(table);
    const more = el('button', 'ui-btn', '在知识中心查看全部'); more.type = 'button';
    more.onclick = () => { if (config.go) config.go('kb', {}); };
    panel.append(el('div', 'action-row', )); panel.lastChild.append(more);
  }

  /* ---------------- 版本 tab ---------------- */
  async function renderVersion(panel, tab) {
    const token = ++seq;
    panel.replaceChildren(el('p', 'meta-line', '正在读取版本状态…'));
    const res = await fetch('/kb/versions', {headers: config.headers(), cache: 'no-store'}).catch(() => null);
    if (token !== seq) return;
    panel.replaceChildren();
    if (!res || !res.ok) { panel.append(el('p', 'notice warn', '版本状态读取失败，请稍后重试。')); return; }
    const data = await res.json().catch(() => null);
    if (token !== seq) return;
    const versions = (data && data.versions) || [];
    const active = versions.find(v => v.status === 'ACTIVE');
    const staged = versions.filter(v => v.status === 'STAGED').length;
    panel.append(el('div', 'gk-qa-head', `当前激活：${active ? active.version : '无'} · 候选 ${staged} 个 · 归档 ${versions.length - (active ? 1 : 0) - staged} 个`));
    const table = el('table', 'data-table gk-table card-table');
    table.append(el('thead', '', '')); table.firstChild.append(el('tr', '', ''));
    for (const h of ['版本', '状态', '发布时间', '质量报告']) table.firstChild.firstChild.append(el('th', '', h));
    const body = el('tbody', ''); table.append(body);
    const statusLabel = {ACTIVE: '激活', STAGED: '候选', ARCHIVED: '归档'};
    for (const v of versions.slice(0, 20)) {
      const tr = el('tr', '');
      tr.append(el('td', '', v.version), el('td', '', statusLabel[v.status] || v.status),
        el('td', 'gk-dim', fmtTime(v.published_at)), el('td', 'gk-dim', v.quality_report_path ? '有' : '—'));
      for (const [index, name] of ['版本', '状态', '发布时间', '质量报告'].entries()) tr.children[index].dataset.label = name;
      body.append(tr);
    }
    panel.append(table);
    const more = el('button', 'ui-btn', '去版本与发布'); more.type = 'button';
    more.onclick = () => { if (config.go) config.go('versions', {}); };
    panel.append(el('div', 'action-row', )); panel.lastChild.append(more);
  }

  /* ---------------- 缺口 tab ---------------- */
  const KBFIX_CAUSES = {missing_info: '资料缺失', retrieval_mismatch: '检索错配',
    wrong_answer: '回答错误', unsupported_citation: '引用不支持'};

  async function renderKbFixIssues(panel, tab, token) {
    const box = el('section', 'surface gk-gap-block');
    box.append(el('h3', '', '问答差评问题单'));
    box.append(el('p', 'ui-help', '差评自动登记原问题与当时回答（同一回答的重复差评合并）；补资料后点“复测原问题”，服务端用原问题重问一次，对比新旧答案与引用后人工确认关闭。'));
    const body = el('div');
    box.append(body);
    panel.append(box);
    let res;
    try {
      res = await fetch('/kb-fix/issues?status=open', {headers: config.headers(), cache: 'no-store'});
    } catch {
      body.append(el('p', 'notice warn', '问题单读取失败（网络或服务异常）。'));
      return;
    }
    if (token !== seq) return;
    if (!res.ok) {
      body.append(el('p', 'notice', '问题单仅 ops / 管理员 / 审计员可查看。'));
      return;
    }
    const data = await res.json().catch(() => ({}));
    const items = data.items || [];
    if (!items.length) { body.append(el('p', 'notice', '当前没有待处理的问答差评问题单。')); return; }
    for (const issue of items) {
      const row = el('div', 'gk-issue');
      const head = el('div', 'gk-issue-head');
      head.append(el('strong', 'gk-issue-q', '#' + issue.id + ' ' + issue.query_text),
        el('span', 'pill warn', issue.cause_label || '—'));
      if (issue.assigned_to) head.append(el('span', 'meta-line', '负责人 ' + issue.assigned_to));
      row.append(head);
      const ops = el('div', 'gk-ops');
      const detail = el('details', 'tech-details');
      detail.append(el('summary', '', '新旧答案对比'));
      detail.append(el('p', 'gk-issue-sec', '【当时回答】' + (issue.answer_text || '（无）')));
      if (issue.verify_note) {
        detail.append(el('p', 'gk-issue-sec', '【复测结果】' + issue.verify_note));
      } else {
        detail.append(el('p', 'gk-issue-sec gk-dim', '尚未复测。'));
      }
      row.append(detail);
      if (!issue.assigned_to) {
        const claim = el('button', 'ui-btn primary', '认领问题'); claim.type = 'button';
        claim.onclick = () => act(`/kb-fix/issues/${issue.id}/assign`, 'POST', {}, claim);
        ops.append(claim);
      }
      const retest = el('button', 'ui-btn'+(issue.assigned_to&&!issue.verify_note?' primary':''), issue.verify_note ? '再次复测原问题' : '复测原问题'); retest.type = 'button';
      retest.onclick = () => act(`/kb-fix/issues/${issue.id}/retest`, 'POST', {}, retest);
      ops.append(retest);
      const repair=el('button','ui-btn','补充修复资料');repair.type='button';repair.onclick=()=>config.go?.('docs',{});ops.append(repair);
      if(issue.verify_note){
        const closeButton=el('button','ui-btn primary','确认问题解决');closeButton.type='button';
        closeButton.onclick=()=>{
          if(row.querySelector('.gk-close-form'))return;
          const form=el('div','gk-close-form'),note=el('textarea','ui-textarea');note.setAttribute('aria-label','解决依据');note.placeholder='说明新答案与引用如何解决原问题，至少8字';
          const send=el('button','ui-btn primary','提交解决确认');send.type='button';
          const status=el('p','meta-line');status.setAttribute('role','status');
          send.onclick=()=>{if(note.value.trim().length<8){status.textContent='请填写至少8字的解决依据。';note.focus();return;}act(`/kb-fix/issues/${issue.id}/close`,'POST',{note:note.value.trim()},send);};
          const cancel=el('button','ui-btn','取消');cancel.type='button';cancel.onclick=()=>{form.remove();closeButton.hidden=false;};
          form.append(el('p','ui-help','对照新旧答案和引用后，记录人工确认依据。'),note,send,cancel,status);row.append(form);closeButton.hidden=true;note.focus();
        };ops.append(closeButton);
      }
      row.append(ops);
      body.append(row);
    }

    async function act(url, method, payload, btn) {
      if (btn) btn.disabled = true;
      const started=seq,key=config.key();
      try {
        const res = await fetch(url, {method, headers: {'Content-Type': 'application/json', ...config.headers()},
          body: JSON.stringify(payload), cache: 'no-store'});
        const data = await res.json().catch(() => ({}));
        if(started!==seq||key!==config.key())return;
        if (!res.ok) throw new Error(data.detail || ('操作失败（HTTP ' + res.status + '）'));
        box.append(el('p', 'notice ok', data.message || '操作成功'));
        // 刷新整个缺口 tab
        const refresh = document.querySelector('#gkBody .gk-tab.active');
        if (refresh) refresh.click();
      } catch (error) {
        if(started===seq&&key===config.key())box.append(el('p', 'notice warn', error.message));
      } finally {
        if (btn) btn.disabled = false;
      }
    }
  }

  const CASE_STATUS = {submitted: ['warn', '待审'], approved: ['ok', '已通过'], rejected: ['muted', '已驳回']};

  async function renderCaseDrafts(panel, token) {
    const box = el('section', 'surface gk-gap-block');
    box.append(el('h3', '', '案例草稿（待审 / 已通过）'));
    box.append(el('p', 'ui-help',
      '工单关闭后可生成案例；审核通过后导出带稳定来源（source_id）与审核关系的 Markdown，' +
      '再到“资料入库”工作区上传并走既有准入——这里不伪造自动入库。'));
    const body = el('div');
    box.append(body);
    panel.append(box);
    let res;
    try {
      res = await fetch('/case-drafts?status=all', {headers: config.headers(), cache: 'no-store'});
    } catch { body.append(el('p', 'notice warn', '案例草稿读取失败（网络或服务异常）。')); return; }
    if (token !== seq) return;
    if (!res.ok) { body.append(el('p', 'notice', '案例审核仅管理员 / 运维 / 审计员可查看。')); return; }
    const data = await res.json().catch(() => ({}));
    const items = data.items || [];
    if (!items.length) { body.append(el('p', 'notice', '当前没有案例草稿。')); return; }
    for (const item of items) {
      const row = el('div', 'gk-issue');
      const head = el('div', 'gk-issue-head');
      const [tone, label] = CASE_STATUS[item.status] || ['muted', item.status];
      head.append(el('strong', 'gk-issue-q', '#' + item.id + ' ' + item.title),
        el('span', 'pill ' + tone, label), el('span', 'meta-line', '工单 ' + item.ticket_id));
      row.append(head);
      const detail = el('details', 'tech-details');
      detail.append(el('summary', '', '查看案例内容'));
      detail.append(el('pre', 'code-line', item.content));
      row.append(detail);
      const ops = el('div', 'gk-ops');
      if (item.status === 'submitted') {
        const approve = el('button', 'ui-btn primary', '通过案例审核'); approve.type = 'button';
        approve.onclick = () => review(item.id, 'approve', approve, box);
        const reject = el('button', 'ui-btn', '驳回'); reject.type = 'button';
        reject.onclick = () => review(item.id, 'reject', reject, box);
        ops.append(approve, reject);
      } else if (item.status === 'approved') {
        if (item.source_id) {
          ops.append(el('span', 'meta-line', '来源 ' + item.source_id +
            (item.sha256 ? ' · sha256 ' + item.sha256.slice(0, 12) : '')));
        }
        const exportBtn = el('button', 'ui-btn primary', '导出案例原文（.md）'); exportBtn.type = 'button';
        exportBtn.onclick = () => exportDoc(item.id, exportBtn, row);
        const toDocs = el('button', 'ui-btn', '去资料入库'); toDocs.type = 'button';
        toDocs.onclick = () => { if (config.go) config.go('docs', {}); };
        ops.append(exportBtn, toDocs);
      }
      row.append(ops);
      body.append(row);
    }

    async function exportDoc(caseId, btn, host) {
      btn.disabled = true;
      try {
        const res = await fetch('/case-drafts/' + caseId + '/document', {headers: config.headers(), cache: 'no-store'});
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(payload.detail || ('导出失败（HTTP ' + res.status + '）'));
        const blob = new Blob([payload.content || ''], {type: 'text/markdown;charset=utf-8'});
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = payload.filename || ('fault_case_' + caseId + '.md');
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 4000);
        host.append(el('p', 'notice ok',
          '已导出 ' + payload.filename + ' · 来源 ' + payload.source_id + ' · sha256 ' + String(payload.sha256).slice(0, 12) +
          '。请到“资料入库”上传该文件，并用来源 ID 登记。'));
      } catch (error) { host.append(el('p', 'notice warn', error.message)); }
      finally { btn.disabled = false; }
    }

    async function review(caseId, decision, btn, host) {
      btn.disabled = true;
      try {
        const res = await fetch('/case-drafts/' + caseId + '/review', {
          method: 'POST', headers: {'Content-Type': 'application/json', ...config.headers()},
          body: JSON.stringify({decision}), cache: 'no-store'});
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(payload.detail || ('审核失败（HTTP ' + res.status + '）'));
        host.append(el('p', 'notice ok', payload.message || '已审核'));
        const refresh = document.querySelector('#gkBody .gk-tab.active');
        if (refresh) refresh.click();
      } catch (error) { host.append(el('p', 'notice warn', error.message)); }
      finally { btn.disabled = false; }
    }
  }

  async function renderGap(panel, tab) {
    const token = ++seq;
    panel.replaceChildren(el('p', 'meta-line', '正在汇总评测未过项…'));
    let failing = [], reportLabel='', reportRead=false;
    const res = await fetch('/admin/eval/reports', {headers: config.headers(), cache: 'no-store'}).catch(() => null);
    if (token !== seq) return;
    if (res && res.ok) {
      const report = await res.json().catch(() => null);
      const latest = (report && report.reports || [])[0];
      if (latest) {
        reportLabel=`${latest.kb_version||'未归属版本'} · ${fmtTime(latest.created_at)}`;
        const detailRes = await fetch('/admin/eval/reports/' + encodeURIComponent(latest.id), {headers: config.headers(), cache: 'no-store'}).catch(() => null);
        if (token !== seq) return;
        if (detailRes && detailRes.ok) {
          const detail = await detailRes.json().catch(() => null);
          reportRead=!!detail;
          for (const [key, value] of Object.entries((detail && detail.checks) || {})) {
            if (value.ratio != null && value.ratio < 1) failing.push({kind: 'check', key, passed: value.passed, evaluated: value.evaluated});
          }
          for (const row of (detail && detail.results) || []) {
            const bad = Object.entries(row.checks || {}).filter(([, v]) => v === false);
            if (bad.length) failing.push({kind: 'case', question: row.question || row.query || '（题）', bad: bad.map(([k]) => k)});
          }
        }
      }
    }
    if (token !== seq) return;
    panel.replaceChildren();

    // ⓪ 问答差评问题单（T4：差评→认领→补资料复测→人工确认关闭）

    // ⓪ 案例草稿（C4：工单关闭→案例→审核→资料入库）


    // ① 评测未过项（真实数据；无权限时不显示）
    const gapBox = el('section', 'surface gk-gap-block');
    gapBox.append(el('h3', '', '最近回归待办（仅作补充）'));
    if (!res) gapBox.append(el('p', 'notice warn', '无法读取评测记录（网络或服务异常），仅显示本地登记缺口。'));
    else if (!res.ok) gapBox.append(el('p', 'notice', '评测记录仅管理员 / 审计员可查看；未过项汇总不展示，可继续使用本地缺口登记。'));
    else if (!reportRead) gapBox.append(el('p', 'notice', '没有可读的回归报告；当前质量状态仍待验证。'));
    else if (!failing.length) gapBox.append(el('p', 'notice', '历史报告 '+reportLabel+' 未记录失败项；不代表当前版本已通过独立评测。'));
    else {
      const list = el('ul', 'gk-gap-list');
      for (const item of failing.slice(0, 12)) {
        const li = el('li', '', item.kind === 'check'
          ? `${item.key}：${item.passed}/${item.evaluated} 通过`
          : `${item.question}（${item.bad.join('、')}）`);
        list.append(li);
      }
      gapBox.append(list);
      if (failing.length > 12) gapBox.append(el('p', 'meta-line', `…另有 ${failing.length - 12} 项，详见评测记录。`));
    }
    panel.append(gapBox);

    // ② 登记表单
    const form = el('form', 'gk-gap-form surface');
    form.append(el('h3', '', '登记资料待办'));
    const title = el('input'); title.id = 'gkGapTitle'; title.maxLength = 120; title.required = true; title.placeholder = '缺口描述，例如：SMF 会话保持失败场景缺少处置依据';
    const theme = el('input'); theme.id = 'gkGapTheme'; theme.maxLength = 64; theme.placeholder = '关联主题 / 告警码（可选）';
    const priority = el('select'); priority.id = 'gkGapPriority';
    for (const [v, l] of [['high', '高'], ['medium', '中'], ['low', '低']]) { const o = el('option', '', l); o.value = v; priority.append(o); }
    const submit = el('button', 'ui-btn primary', '登记'); submit.type = 'submit';
    form.append(el('label', 'ui-field', ''));
    form.lastChild.append(el('span', 'ui-label', '缺口描述'), title);
    form.append(el('label', 'ui-field', ''));
    form.lastChild.append(el('span', 'ui-label', '关联主题 / 告警码'), theme);
    form.append(el('label', 'ui-field', ''));
    form.lastChild.append(el('span', 'ui-label', '优先级'), priority);
    form.append(el('div', 'action-row', )); form.lastChild.append(submit);
    form.onsubmit = event => {
      event.preventDefault();
      const value = title.value.trim(); if (!value) return;
      const gaps = loadGaps();
      gaps.unshift({id: 'gap-' + Date.now().toString(36), title: value, theme: theme.value.trim(),
        priority: priority.value, status: 'open', note: '', created_at: new Date().toISOString(), updated_at: new Date().toISOString()});
      saveGaps(gaps); renderGap(panel, 'gap');
    };
    panel.append(form);

    // ③ 缺口列表与闭环
    const gaps = loadGaps();
    const listBox = el('section', 'surface gk-gap-block');
    const counts = {open: 0, supplied: 0, closed: 0};
    for (const g of gaps) counts[g.status] = (counts[g.status] || 0) + 1;
    listBox.append(el('h3', '', `登记缺口（待补 ${counts.open} · 已补待回归 ${counts.supplied} · 已关闭 ${counts.closed}）`));
    if (!gaps.length) listBox.append(el('p', 'notice', '还没有登记缺口。'));
    else {
      const table = el('table', 'data-table gk-table card-table');
      table.append(el('thead', '', '')); table.firstChild.append(el('tr', '', ''));
      for (const h of ['缺口', '主题', '优先级', '状态', '操作']) table.firstChild.firstChild.append(el('th', '', h));
      const body = el('tbody', ''); table.append(body);
      for (const gap of gaps) {
        const tr = el('tr', '');
        tr.append(el('td', '', gap.title), el('td', 'gk-dim', gap.theme || '—'),
          el('td', '', {high: '高', medium: '中', low: '低'}[gap.priority] || '中'));
        for (const [index, name] of ['缺口', '主题', '优先级'].entries()) tr.children[index].dataset.label = name;
        const statusCell = el('td', '');
        statusCell.append(el('span', 'pill ' + (gap.status === 'closed' ? 'ok' : gap.status === 'supplied' ? 'info' : 'warn'), gapStatusLabel[gap.status] || gap.status));
        tr.append(statusCell);
        const ops = el('td', 'gk-ops');
        if (gap.status === 'open') {
          const supply = el('button', 'ui-btn', '标记已补'); supply.type = 'button';
          supply.onclick = () => { gap.status = 'supplied'; gap.updated_at = new Date().toISOString(); saveGaps(gaps); renderGap(panel, 'gap'); };
          ops.append(supply);
        }
        if (gap.status === 'supplied') {
          const regress = el('button', 'ui-btn', '发起回归'); regress.type = 'button';
          regress.onclick = () => { if (config.go) config.go('eval', {}); };
          ops.append(regress);
          const close = el('button', 'ui-btn', '关闭'); close.type = 'button';
          close.onclick = () => { gap.status = 'closed'; gap.updated_at = new Date().toISOString(); saveGaps(gaps); renderGap(panel, 'gap'); };
          ops.append(close);
        }
        if (gap.status === 'closed') {
          const reopen = el('button', 'ui-btn', '重新打开'); reopen.type = 'button';
          reopen.onclick = () => { gap.status = 'open'; gap.updated_at = new Date().toISOString(); saveGaps(gaps); renderGap(panel, 'gap'); };
          ops.append(reopen);
        }
        tr.append(ops); body.append(tr);
      }
      listBox.append(table);
    }
    const exportBtn = el('button', 'ui-btn', '导出缺口清单（JSON）'); exportBtn.type = 'button';
    exportBtn.onclick = () => {
      const blob = new Blob([JSON.stringify({exported_at: new Date().toISOString(), gaps}, null, 2)], {type: 'application/json'});
      const link = el('a'); link.href = URL.createObjectURL(blob); link.download = 'knowledge-gaps.json'; link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 4000);
    };
    listBox.append(el('div', 'action-row', )); listBox.lastChild.append(exportBtn);
    panel.append(listBox);
  }

  function load(params = {}) {
    const host = $('gkBody'); if (!host) return;
    // 每次进入先清空：否则往返会累积多排页签并留下旧 panel 的加载态
    host.replaceChildren();
    const built = frame(); if (!built) return;
    return built.show(params.tab || 'issues');
  }
  function leave() { ++seq; }
  function init(options) { config = options; }
  return {init, load, leave};
})();
