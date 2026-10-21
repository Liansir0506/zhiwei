/* 查询工作台：自然语言 → QuerySpec 预览 → 受控只读执行 → 导出。
   边界：表白名单/字段白名单、强制租户过滤、limit ≤ 200、只 SELECT；解析来源（llm/rule）明示。 */
window.QueryWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text); return n;
  };
  const EXAMPLES = [
    {label: '最近的紧急告警', question: '最近的紧急告警 20 条'},
    {label: '未处理的重要告警', question: '未处理的重要告警 30 条'},
    {label: '最近的监控采样', question: '最近的监控采样 20 条'},
    {label: '在网设备清单', question: '在网设备清单 50 台'},
    {label: '处理中的工单', question: '处理中的工单 20 条'},
  ];
  let config, seq = 0, currentSpec = null, lastResult = null, mode = 'stats',request=null;
  function stopRequest() {
    request?.abort();request=null;
    for(const id of ['queryParse','queryExecute'])window.UIMotion?.clearBusy($(id));
    if($('queryCancel'))$('queryCancel').hidden=true;
  }
  function beginRequest(button){stopRequest();request=new AbortController();$('queryCancel').hidden=false;return {controller:request,done:window.UIMotion?.busy(button)};}
  function showMode(value) {
    mode=value;
    for(const key of ['stats','custom']) { const on=key===value;const button=$('queryMode-'+key);button.classList.toggle('is-active',on);button.setAttribute('aria-selected',String(on));$('queryPanel-'+key).hidden=!on; }
  }
  function invalidate() {
    stopRequest();
    ++seq;currentSpec=null;lastResult=null;
    if(!$('queryExecute'))return;
    $('queryExecute').disabled=true;$('queryExecuteRow').hidden=true;$('querySpecBox').hidden=true;$('queryResult').hidden=true;
    $('queryParse').disabled=false;$('queryParse').classList.add('primary');
    $('queryStatus').textContent='';
  }
  function credentialsChanged() { stopRequest();++seq;currentSpec=null;lastResult=null;const host=$('queryBody');if(host){host.replaceChildren();delete host.dataset.built;} }

  const fmtNum = v => (v == null ? '—' : Number(v).toLocaleString('zh-CN'));
  const FIELD_LABELS = {alarm_id: '告警事件', device_id: '设备', alarm_code: '告警码', severity: '级别', status: '状态',
    occurred_at: '发生时间', cleared_at: '清除时间', source_system: '来源系统', name: '名称', device_type: '设备类型',
    site_id: '站点', vendor: '厂家', software_version: '软件版本', created_at: '创建时间', ticket_id: '工单', title: '标题',
    priority: '优先级', assignee_user_id: '负责人', opened_at: '开单时间', closed_at: '关单时间', change_id: '变更',
    change_type: '变更类型', window_start: '窗口开始', window_end: '窗口结束', operator_user_id: '操作人',
    observation_id: '观测', metric_name: '指标', metric_value: '值', unit: '单位', sampled_at: '采样时间'};
  const TABLE_LABELS = {alarms: '告警', devices: '设备', work_orders: '工单', change_records: '变更', monitoring_metrics: '监控指标'};
  const VALUE_LABELS = {critical: '紧急', major: '重要', minor: '次要', warning: '警告', open: '未处理', acknowledged: '已确认',
    cleared: '已清除', closed: '已关闭', pending:'待处理', in_progress:'处理中', online: '在线', offline: '离线', maintenance: '维护', retired: '退网'};
  const WINDOW_LABELS={today:'今天',yesterday:'昨天',this_week:'本周',last_week:'上周',last_7d:'近7天',last_30d:'近30天'};
  const label = (field, value) => {
    if (value == null || value === '') return '—';
    if (FIELD_LABELS[value] && (field === 'severity' || field === 'status' || field === 'device_type' || field === 'priority' || field === 'change_type')) return FIELD_LABELS[value];
    return VALUE_LABELS[value] || value;
  };
  const fieldLabel = f => FIELD_LABELS[f] || f;

  async function parse() {
    const question = $('queryInput').value.trim();
    if (question.length < 2) { $('queryStatus').textContent = '请先输入至少 2 个字的查询意图。'; return; }
    const token = ++seq;
    const pending=beginRequest($('queryParse'));
    const key=config.key();currentSpec=null;lastResult=null;
    $('queryParse').disabled=true;$('querySpecBox').hidden=true;$('queryExecuteRow').hidden=true;
    $('queryStatus').textContent = '正在解析查询意图…';
    $('querySpecBox').replaceChildren(); $('queryResult').replaceChildren(); $('queryResult').hidden = true;
    $('queryExecute').disabled = true;
    try {
      const response = await fetch('/query/parse', {method: 'POST', headers: {'Content-Type': 'application/json', ...config.headers()},
        body: JSON.stringify({question}), cache: 'no-store',signal:pending.controller.signal});
      if (token !== seq || key!==config.key()) return;
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : '解析失败（HTTP ' + response.status + '）');
      }
      const data = await response.json();
      if (token !== seq || key!==config.key()) return;
      if (data.supported === false || !data.spec) {
        // 后端明确拒绝（聚合/无表意图等）：显示中文原因并引导到固定统计卡
        currentSpec = null; lastResult = null;
        $('querySpecBox').replaceChildren();
        const box = el('section', 'surface qspec-box');
        box.append(el('h3', '', '该查询未生成计划'),
          el('p', 'notice warn', data.reason || '当前问题无法解析为受控明细查询。'),
          el('p', 'ui-help', data.source === 'gate'
            ? '这类汇总统计请使用“常用统计”页签；或改写为明细条件（表 + 筛选 + 限量）。'
            : '请改写为更具体的明细条件（表 + 筛选 + 限量）。'));
        const stats=el('button','ui-btn','查看常用统计');stats.type='button';stats.onclick=()=>showMode('stats');box.append(stats);
        $('querySpecBox').append(box);$('querySpecBox').hidden=false;
        $('queryStatus').textContent = '解析被拒绝：原因见上方说明。';
        $('queryExecute').disabled = true;
        return;
      }
      currentSpec = data.spec; lastResult = null;
      renderSpec(data);
      $('querySpecBox').hidden=false;$('queryExecuteRow').hidden=false;$('queryParse').classList.remove('primary');
      $('queryStatus').textContent = data.source === 'llm'
        ? '已生成查询计划（模型解析）；请核对后再执行。'
        : '已生成查询计划（规则解析，未调用模型）；请核对后再执行。';
      $('queryExecute').disabled = false;
    } catch (error) {
      if (token !== seq || key!==config.key()) return;
      $('queryStatus').textContent = error.name==='AbortError'?'已取消等待，未取得查询计划。':error.message;
    } finally { pending.done?.();if(token===seq&&key===config.key()){$('queryParse').disabled=false;$('queryCancel').hidden=true;request=null;} }
  }

  function renderSpec(data) {
    const spec = data.spec, resolved = data.resolved || {};
    const box = $('querySpecBox'); box.replaceChildren();
    box.append(el('h2','','确认查询条件'),el('p','ui-help','核对对象、范围与条数后再查询；此操作只读。'));
    const table = el('table', 'data-table qspec-table');
    table.append(el('tbody', ''));
    const rows = [
      ['查询对象', TABLE_LABELS[spec.table] || spec.table],
      ['输出字段', (resolved.select || spec.select || []).map(fieldLabel).join('、') || '默认字段'],
      ['过滤条件', (resolved.filters || spec.filters || []).length
        ? (resolved.filters || spec.filters).map(f => `${fieldLabel(f.field)} ${{'=':'为','!=':'不为','>=':'至少','<=':'至多','>':'大于','<':'小于',eq:'为',ne:'不为',gte:'至少',lte:'至多',gt:'大于',lt:'小于',in:'属于',like:'包含',between:'介于'}[f.op]||f.op} ${label(f.field, f.value)}`).join('；')
        : '无'],
      ['时间范围', WINDOW_LABELS[resolved.time_window||spec.time_window]||resolved.time_window||spec.time_window||'未限定时间'],
      ['排序', resolved.order_by ? `${fieldLabel(resolved.order_by.field)} ${resolved.order_by.dir === 'asc' ? '升序' : '降序'}` : '无'],
      ['行数上限', String(spec.limit)],
      ['计划说明', spec.explain || '—'],
    ];
    for (const [key, value] of rows) {
      const tr = el('tr', ''); tr.append(el('td', 'qspec-key', key), el('td', '', value));
      if(key==='行数上限')tr.lastChild.dataset.planLimit='true';
      table.firstChild.append(tr);
    }
    box.append(table);
    const details = el('details', 'tech-details');
    details.append(el('summary', '', '查看解析方式与技术条件'));
    details.append(el('p','meta-line',data.source==='llm'?'模型解析，执行前需核对。':'规则解析，未调用模型。'));
    details.append(el('pre', 'tech-block', JSON.stringify(spec, null, 2)));
    box.append(details);
    const limitRow = el('div', 'qspec-limit');
    limitRow.append(el('span', '', '行数上限'));
    const limitInput = el('input'); limitInput.type = 'number'; limitInput.id = 'queryLimit';
    limitInput.min = '1'; limitInput.max = '200'; limitInput.value = String(spec.limit);
    limitInput.onchange = () => { stopRequest();++seq;currentSpec.limit = Math.max(1, Math.min(200, Math.trunc(Number(limitInput.value)) || 50));limitInput.value=String(currentSpec.limit);box.querySelector('[data-plan-limit]').textContent=String(currentSpec.limit);lastResult=null;$('queryResult').hidden=true;$('queryExecute').disabled=false;$('queryParse').disabled=false;$('queryStatus').textContent='条数已更新，请重新查询。'; };
    limitRow.append(limitInput, el('span', 'qspec-hint', '上限 200；超出只返回前 200 行'));
    box.append(limitRow);
  }

  async function execute() {
    if (!currentSpec) { $('queryStatus').textContent = '请先解析生成查询计划。'; return; }
    const token = ++seq;
    const pending=beginRequest($('queryExecute'));
    const key=config.key();const executionSpec=JSON.parse(JSON.stringify(currentSpec));
    lastResult=null;$('queryResult').replaceChildren();$('queryResult').hidden=true;
    $('queryStatus').textContent = '正在只读执行…';
    $('queryExecute').disabled = true;
    try {
      const response = await fetch('/query/execute', {method: 'POST', headers: {'Content-Type': 'application/json', ...config.headers()},
        body: JSON.stringify({spec: executionSpec}), cache: 'no-store',signal:pending.controller.signal});
      if (token !== seq || key!==config.key()) return;
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : '执行失败（HTTP ' + response.status + '）');
      }
      const data = await response.json();
      if (token !== seq || key!==config.key()) return;
      lastResult = data;
      renderResult(data);
      $('queryStatus').textContent = `返回 ${fmtNum(data.row_count)} 行 · 耗时 ${data.elapsed_ms} ms${data.truncated ? ' · 已截断至上限' : ''}`;
      $('queryExecute').disabled = false;
    } catch (error) {
      if (token !== seq || key!==config.key()) return;
      $('queryStatus').textContent = error.name==='AbortError'?'已取消等待；服务端查询可能仍在退出，请核对后手动重试。':error.message;
      $('queryExecute').disabled = false;
    } finally {pending.done?.();if(token===seq&&key===config.key()){$('queryCancel').hidden=true;request=null;} }
  }

  function renderResult(data) {
    const host = $('queryResult'); host.replaceChildren(); host.hidden = false;
    if(data.scope)host.append(el('p','meta-line',data.scope));
    if (!data.rows.length) {
      host.append(el('p', 'notice', '没有符合条件的记录。可调整条件或行数上限后重试。'));
      return;
    }
    const table = el('table', 'data-table qresult-table card-table');
    table.append(el('thead', '')); table.firstChild.append(el('tr', ''));
    for (const column of data.columns) table.firstChild.firstChild.append(el('th', '', fieldLabel(column)));
    const body = el('tbody', ''); table.append(body);
    for (const row of data.rows) {
      const tr = el('tr', '');
      for (const [index, column] of data.columns.entries()) {
        const value = row[column];
        const cell = el('td', column === data.columns[0] ? 'qresult-id' : '', value == null ? '—' : String(label(column,value)));
        cell.dataset.label = fieldLabel(column);
        tr.append(cell);
      }
      body.append(tr);
    }
    host.append(table);
    const actions = el('div', 'action-row');
    const csv = el('button', 'ui-btn', '导出 CSV'); csv.type = 'button';
    csv.onclick = () => download((currentSpec.table || 'query') + '.csv', toCsv(data), 'text/csv;charset=utf-8');
    const jsonBtn = el('button', 'ui-btn', '导出 JSON'); jsonBtn.type = 'button';
    jsonBtn.onclick = () => download((currentSpec.table || 'query') + '.json', JSON.stringify(data.rows, null, 2), 'application/json');
    actions.append(csv, jsonBtn);
    host.append(actions);
  }

  function toCsv(data) {
    const escape = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
    const lines = [data.columns.map(escape).join(',')];
    for (const row of data.rows) lines.push(data.columns.map(c => escape(row[c])).join(','));
    return '\uFEFF' + lines.join('\r\n');
  }
  function download(name, text, mime) {
    const blob = new Blob([text], {type: mime});
    const link = el('a'); link.href = URL.createObjectURL(blob); link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 4000);
  }

  function load() {
    const host=$('queryBody');if(!host)return;
    if(host.dataset.built==='1')return;host.dataset.built='1';
    const tabs=el('div','sub-tabs query-modes');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','查询类型');
    for(const [key,title] of [['stats','常用统计'],['custom','自定义查询']]){const button=el('button','sub-tab',title);button.id='queryMode-'+key;button.type='button';button.setAttribute('role','tab');button.setAttribute('aria-controls','queryPanel-'+key);button.onclick=()=>showMode(key);tabs.append(button);}
    const stats=el('section','qstats');stats.id='queryPanel-stats';stats.setAttribute('role','tabpanel');stats.setAttribute('aria-labelledby','queryMode-stats');
    const intro=el('div','query-intro');intro.append(el('h2','','常用业务统计'),el('p','ui-help','直接查看当前范围数据；统计口径与分母可展开核对。'));stats.append(intro);
    const grid=el('div','qstats-grid');grid.id='qstatsGrid';stats.append(grid);
    const custom=el('section','query-custom');custom.id='queryPanel-custom';custom.setAttribute('role','tabpanel');custom.setAttribute('aria-labelledby','queryMode-custom');
    custom.append(el('h2','','你想查什么？'),el('p','ui-help','描述对象、时间和条件。先核对条件，再执行查询。'));
    const form=el('form','qinput-row surface'),input=el('textarea','ui-textarea');input.id='queryInput';input.rows=3;input.maxLength=300;input.placeholder='例如：最近7天未处理的紧急告警，最多20条';input.setAttribute('aria-label','查询描述');input.oninput=invalidate;
    const parseButton=el('button','ui-btn primary','生成查询');parseButton.id='queryParse';parseButton.type='submit';form.append(input,parseButton);form.onsubmit=e=>{e.preventDefault();parse();};custom.append(form);
    const examples=el('div','qexamples');
    for(const example of EXAMPLES){const button=el('button','qexample',example.label);button.type='button';button.onclick=()=>{input.value=example.question;invalidate();input.focus();};examples.append(button);}custom.append(examples);
    const status=el('p','query-status meta-line');status.id='queryStatus';status.setAttribute('role','status');custom.append(status);
    const cancel=el('button','ui-btn','取消等待');cancel.id='queryCancel';cancel.type='button';cancel.hidden=true;cancel.onclick=()=>{stopRequest();++seq;$('queryParse').disabled=false;$('queryExecute').disabled=!currentSpec;$('queryStatus').textContent='已取消等待；服务端请求可能仍在退出。不会自动重试。';};custom.append(cancel);
    const box=el('section','surface qspec-box');box.id='querySpecBox';box.hidden=true;custom.append(box);
    const row=el('div','action-row');row.id='queryExecuteRow';row.hidden=true;const executeButton=el('button','ui-btn primary','确认并查询');executeButton.id='queryExecute';executeButton.type='button';executeButton.disabled=true;executeButton.onclick=execute;row.append(executeButton,el('span','ui-help','只读，不修改业务记录'));custom.append(row);
    const results=el('section','qresult');results.id='queryResult';results.hidden=true;custom.append(results);
    host.append(tabs,stats,custom);showMode(mode);renderStats(grid);
  }

  const STAT_CARDS = [
    {kind: 'alarm_count', label: '告警数量', window: '7d'},
    {kind: 'severity_distribution', label: '级别分布', window: '7d'},
    {kind: 'device_online_rate', label: '设备在线率', window: null},
    {kind: 'ticket_backlog', label: '工单积压', window: null},
    {kind: 'kpi_trend', label: 'KPI 趋势', window: '7d'},
  ];

  async function renderStats(grid) {
    for (const card of STAT_CARDS) {
      const box = el('div', 'qstat-card');
      box.append(el('h4', '', card.label));
      box.append(el('p', 'meta-line', '读取中…'));
      grid.append(box);
      loadStat(box, card);
    }
  }

  async function loadStat(box, card) {
    const key=config.key();
    const done=window.UIMotion?.busy(box);
    const query = card.window ? `?window=${card.window}` : '';
    try {
      const response = await fetch('/stats/' + card.kind + query, {headers: config.headers(), cache: 'no-store'});
      const data = await response.json().catch(() => ({}));
      if(key!==config.key()||!box.isConnected)return;
      if (!response.ok) {
        if (response.status === 404) {
          throw new Error('统计接口未在当前进程加载（404）：请双击“启动运维平台.bat”重启后再试。');
        }
        throw new Error(data.detail || ('读取失败（HTTP ' + response.status + '）'));
      }
      box.replaceChildren(el('h4', '', card.label));
      const r = data.result || {};
      if (card.kind === 'alarm_count') {
        const numbers = el('div', 'qstat-numbers');
        numbers.append(statNumber(r.active, '活动'), statNumber(r.recent, '近 7 天新增'), statNumber(r.total, '累计'));
        box.append(numbers);
      } else if (card.kind === 'severity_distribution') {
        const numbers = el('div', 'qstat-numbers');
        for (const item of (r.items || []).slice(0, 4)) numbers.append(statNumber(item.count, item.label + (item.ratio ? ` ${Math.round(item.ratio * 100)}%` : '')));
        if(!r.items?.length)numbers.append(el('p','meta-line','窗口内暂无级别统计'));
        box.append(numbers);
      } else if (card.kind === 'device_online_rate') {
        const numbers = el('div', 'qstat-numbers');
        numbers.append(statNumber(r.rate == null ? '—' : r.rate + r.unit, '在线率'), statNumber(r.online, '在线'), statNumber(r.total, '在册'));
        box.append(numbers);
      } else if (card.kind === 'ticket_backlog') {
        const numbers = el('div', 'qstat-numbers');
        numbers.append(statNumber(r.backlog, '积压'), statNumber(r.overdue_24h, '超24小时'));
        box.append(numbers);
      } else {
        const trend = r.trend || [];
        if (trend.length) {
          const numbers = el('div', 'qstat-numbers');
          numbers.append(statNumber(trend[trend.length - 1].avg + (r.unit || ''), `${r.metric} 最近均值`));
          box.append(numbers);
          box.append(el('p', 'meta-line', `${r.metric} · ${trend.length} 天 · 采样 ${r.samples} 点`));
        } else {
          box.append(el('p', 'meta-line', r.metric + '：窗口内无采样'));
        }
      }
      if (r.data_window) {
        box.append(el('p', 'qstat-window', '数据窗口 ' + String(r.data_window.from).slice(0, 10) + ' ~ ' + String(r.data_window.to).slice(0, 10)));
      } else if (r.time_window) {
        box.append(el('p', 'qstat-window', r.time_window));
      }
      const metadata=el('details','tech-details');metadata.append(el('summary','','统计口径与分母'),el('p','qstat-formula',r.formula||'接口未提供计算公式'));
      metadata.append(el('pre','tech-block',JSON.stringify(r,null,2)));box.append(metadata);
      // 后端返回 detail（目标对象 + 同一条件）；按钮文字与实际能力一致
      const detail = data.detail || {};
      if (detail.view) {
        const link = el('button', 'ui-btn qstat-link', detail.label || '查看明细'); link.type = 'button';
        link.onclick = () => {
          if (typeof window.switchTab !== 'function') return;
          window.switchTab(detail.view, detail.params || {});
        };
        box.append(link);
      }
      box.append(el('p', 'qstat-scope', data.scope || ''));
    } catch (error) {
      if(key!==config.key()||!box.isConnected)return;
      box.replaceChildren(el('h4', '', card.label), el('p', 'notice warn', error.message));
      const retry=el('button','ui-btn','重新读取');retry.type='button';retry.onclick=()=>loadStat(box,card);box.append(retry);
    } finally {done?.();}
  }

  function statNumber(value, label) {
    const item = el('div', 'qstat-number');
    item.append(el('b', '', String(value == null ? '—' : value)), el('span', '', label));
    return item;
  }
  function leave() { stopRequest();++seq;if($('queryParse'))$('queryParse').disabled=false;if($('queryExecute'))$('queryExecute').disabled=!currentSpec; }
  function init(options) { config = options;window.addEventListener('storage',e=>{if(e.key==='api_key'||e.key===null)credentialsChanged();}); }
  return {init, load, leave, credentialsChanged};
})();
