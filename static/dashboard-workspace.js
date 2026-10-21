/* 总览看板：产品化首屏。数据来自 /dashboard/summary 与 /alerts（同一租户），
   图表为内联 SVG、零外呼；没有时序的指标不画假趋势。 */
window.DashboardWorkspace = (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = String(text); return n; };
  const fmt = n => new Intl.NumberFormat('zh-CN').format(n == null ? 0 : n);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  let config, epoch = 0, controller = null, loaded = null, period = 7, active = false;

  const go = (tab, params) => { if (typeof window.switchTab === 'function') window.switchTab(tab, params || {}); };
  const sevClass = s => ({ critical: 'sev-critical', major: 'sev-major', minor: 'sev-minor', warning: 'sev-warning' }[s] || 'sev-warning');

  function s(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (text != null) node.textContent = String(text);
    return node;
  }

  function sparkline(values, color = '#2563eb') {
    const width = 88, height = 28, max = Math.max(1, ...values), min = Math.min(...values);
    const span = Math.max(1, max - min);
    const svg = s('svg', { width, height, viewBox: `0 0 ${width} ${height}`, class: 'dash-spark', 'aria-hidden': 'true' });
    if (!values.length) return svg;
    const pts = values.map((v, i) => [i / Math.max(1, values.length - 1) * (width - 4) + 2, height - 3 - (v - min) / span * (height - 8)]);
    svg.append(s('path', { d: 'M' + pts.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' L'),
      fill: 'none', stroke: color, 'stroke-width': 1.7, 'stroke-linecap': 'round', pathLength: '1', class: 'chart-line' }));
    return svg;
  }

  function lineChart(labels, values, height = 190) {
    const width = 680, padL = 38, padR = 12, padT = 14, padB = 26;
    const innerW = width - padL - padR, innerH = height - padT - padB;
    const max = Math.max(1, ...values), step = max / 3;
    const svg = s('svg', { class: 'dash-chart', viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': '告警趋势' });
    for (let i = 0; i <= 3; i++) {
      const y = padT + innerH * (1 - i / 3);
      svg.append(s('line', { x1: padL, x2: width - padR, y1: y, y2: y, stroke: '#eef2f8', 'stroke-width': 1 }));
      svg.append(s('text', { x: 4, y: y + 4, fill: '#93a1b5', 'font-size': 10 }, fmt(Math.round(step * i))));
    }
    const points = values.map((v, i) => [padL + (i / Math.max(1, values.length - 1)) * innerW, padT + innerH * (1 - v / max)]);
    const line = 'M' + points.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' L');
    const area = line + ` L${(padL + innerW).toFixed(1)} ${padT + innerH} L${padL} ${padT + innerH} Z`;
    const defs = s('defs');
    const grad = s('linearGradient', { id: 'dashArea', x1: '0', y1: '0', x2: '0', y2: '1' });
    grad.append(s('stop', { offset: '0', 'stop-color': '#3b82f6', 'stop-opacity': '.22' }));
    grad.append(s('stop', { offset: '1', 'stop-color': '#3b82f6', 'stop-opacity': '0' }));
    defs.append(grad); svg.append(defs);
    svg.append(s('path', { d: area, fill: 'url(#dashArea)', class: 'chart-area' }));
    svg.append(s('path', { d: line, fill: 'none', stroke: '#2563eb', 'stroke-width': 2.2, 'stroke-linecap': 'round',
      'stroke-linejoin': 'round', pathLength: '1', class: 'chart-line' }));
    points.forEach((p, i) => { if (i % Math.max(1, Math.floor(points.length / 6)) === 0 || i === points.length - 1)
      svg.append(s('circle', { cx: p[0], cy: p[1], r: 3, fill: '#fff', stroke: '#2563eb', 'stroke-width': 2 })); });
    const every = Math.max(1, Math.floor(labels.length / 6));
    labels.forEach((label, i) => { if (i % every !== 0 && i !== labels.length - 1) return;
      svg.append(s('text', { x: padL + (i / Math.max(1, labels.length - 1)) * innerW, y: height - 6, fill: '#93a1b5', 'font-size': 10, 'text-anchor': 'middle' }, label)); });
    return svg;
  }

  function animateNumber(target, value) {
    // 读数优先：直接给最终值。数字滚动会先显示未完成数值，
    // 在运维场景里容易把中间态当成真实值，这里不再做装饰性动画。
    target.textContent = fmt(value);
  }

  function metricCard({ label, value, unit, sub, tone, actionLabel, onAction, spark, icon, delay = 0 }) {
    const card = el('article', 'dash-metric');
    card.style.setProperty('--delay', delay + 'ms');
    if (tone) card.dataset.tone = tone;
    const top = el('div', 'dash-metric-top');
    const mark=el('span','dash-metric-icon'),picture=s('svg',{viewBox:'0 0 24 24','aria-hidden':'true'});picture.append(s('use',{href:'/static/icons.svg#'+({'▣':'panel','◔':'activity','▤':'clipboard','▥':'book'}[icon]||'chart')}));mark.append(picture);top.append(el('span','dash-metric-label',label),mark);
    card.append(top);
    const number = el('div', 'dash-metric-value');
    const match = String(value).match(/^([\d,.]+)(.*)$/);
    if (match) {
      const raw = Number(match[1].replace(/,/g, ''));
      const num = el('span', 'num', fmt(raw));
      number.append(num);
      if (match[2]) number.append(el('small', null, match[2]));
    } else { number.textContent = String(value); }
    if (unit) number.append(el('small', null, unit));
    card.append(number);
    if (sub) { const line = el('div', 'dash-metric-sub'); line.append(sub); card.append(line); }
    if (spark) card.append(sparkline(spark));
    if (actionLabel) { const action = el('button', 'link-button dash-metric-action', actionLabel); action.type = 'button'; action.onclick = onAction; card.append(action); }
    return card;
  }

  function metrics(data) {
    const overview = data.overview || {};
    const devices = overview.devices || {}, alarms = overview.alarms || {}, work = overview.work_orders || {}, changes = overview.changes || {}, knowledge = overview.knowledge || {};
    const wrap = el('div', 'dash-metrics');
    wrap.append(
      metricCard({ label: '设备在线率', value: (devices.online_rate ?? 0) + '%', tone: 'ok', icon: '▣',
        sub: (() => { const p = el('span'); p.append(el('b', null, fmt(devices.online) + ' / ' + fmt(devices.total) + ' 台')); p.append(document.createTextNode(' · 当前在线')); return p; })(),
        actionLabel: '查看设备记录 →', onAction: () => go('alerts', { subtab: 'devices' }), delay: 0 }),
      metricCard({ label: '活动告警', value: fmt(alarms.open), unit: '项', tone: 'warn', icon: '◔',
        sub: (() => { const p = el('span'); p.append(el('b', null, '紧急 ' + fmt(alarms.critical_open) + ' 项')); p.append(document.createTextNode(' · 累计 ' + fmt(alarms.total))); return p; })(),
        actionLabel: '进入告警中心 →', onAction: () => go('alerts'), spark: (data.alarm_trend || {}).values, delay: 40 }),
      metricCard({ label: '工单与变更', value: fmt(work.total), unit: '单', icon: '▤',
        sub: (() => { const p = el('span'); p.append(el('b', null, 'P1 ' + fmt(work.p1))); p.append(document.createTextNode(' · 变更 ' + fmt(changes.total) + '（完成 ' + fmt(changes.completed) + '）')); return p; })(),
        actionLabel: '查看工单记录 →', onAction: () => go('alerts', {subtab:'tickets'}), delay: 80 }),
      metricCard({ label: '知识资产', value: fmt(knowledge.assets), unit: '份', icon: '▥',
        sub: (() => { const p = el('span'); if (!knowledge.active_version) { p.append(document.createTextNode('当前租户无可见知识资产')); return p; }
          p.append(document.createTextNode('片段定位 ' + fmt(knowledge.locations) + ' · 版本 '));
          const v = knowledge.versions || {}; p.append(el('b', null, (v.active || 0) + '/' + (v.staged || 0) + '/' + (v.archived || 0))); return p; })(),
        actionLabel: '浏览知识中心 →', onAction: () => go('kb'), delay: 120 }),
    );
    return wrap;
  }

  function trendCard(data) {
    const box = el('section', 'dash-card span-7');
    const head = el('div', 'dash-card-head');
    const title = el('div'); title.append(el('h2', null, '告警趋势'), el('p', 'dash-card-note', '按天统计 · 数据自身窗口 · 共 ' + (data.alarm_trend?.labels?.length || 0) + ' 天'));
    const seg = el('div', 'segmented');
    for (const [days, label] of [[3, '近 3 天'], [7, '近 7 天'], [30, '近 30 天']]) {
      const b = el('button', period === days ? 'active' : '', label); b.type = 'button';
      b.onclick = () => { if (period !== days) { period = days; load({ days }); } };
      seg.append(b);
    }
    head.append(title, seg); box.append(head);
    const values = data.alarm_trend?.values || [], labels = data.alarm_trend?.labels || [];
    const body = el('div', 'dash-card-body');
    if (!values.length) body.append(el('div', 'dash-empty', '暂无告警样本'));
    else body.append(lineChart(labels, values));
    const foot = el('div', 'dash-card-foot');
    foot.append(el('span', null, '合计 ' + fmt(values.reduce((a, b) => a + b, 0)) + ' 条 · 峰值 ' + fmt(Math.max(0, ...values)) + ' 条/天'));
    const link = el('button', 'link-button', '查看全部告警 →'); link.type = 'button'; link.onclick = () => go('alerts'); foot.append(link);
    body.append(foot); box.append(body);
    return box;
  }

  function taskCard(data, alerts) {
    const box = el('section', 'dash-card dash-tasks');
    const head = el('div', 'dash-card-head');
    head.append(el('h2', null, '待处理事项'), el('span', 'dash-card-note', '当前可见范围'));
    const all = el('button', 'link-button', '全部告警 →'); all.type = 'button'; all.onclick = () => go('alerts'); head.append(all); box.append(head);
    const items = (alerts?.items || []).filter(a => !['closed','cleared'].includes(a.status)).slice(0,4);
    if (!items.length) box.append(el('p', 'dash-empty', '当前范围暂无待处理告警。可查看工单或开始新的诊断。'));
    for (const [index,item] of items.entries()) {
      const row = el('div', 'dash-task-row'), mark = el('span', 'dash-task-mark');
      const picture=s('svg', {viewBox:'0 0 24 24','aria-hidden':'true'}); picture.append(s('use', {href:'/static/icons.svg#activity'})); mark.append(picture);
      const text = el('div', 'dash-task-copy'); text.append(el('strong', null, item.title || item.alarm_code || '待核对告警'), el('small', null, [item.device_name || item.device_id, item.severity_label || item.severity, '核对事件与当前处置进度'].filter(Boolean).join(' · ')));
      const next = el('button', 'ui-btn sm'+(index===0?' primary':''), '继续处理'); next.type = 'button';
      next.onclick = () => { go('alerts'); window.AlertsWorkspace?.open(item.alarm_id); };
      row.append(mark, text, next); box.append(row);
    }
    const shortcuts = el('div', 'dash-task-shortcuts');
    for (const [label, target, params] of [['查看工单','alerts',{subtab:'tickets'}],['开始诊断','agent',{}],['查运维知识','qa',{}]]) {
      const button = el('button','ui-btn',label); button.type='button'; button.onclick=()=>go(target,params); shortcuts.append(button);
    }
    box.append(shortcuts); return box;
  }

  function render(data, alerts) {
    const view = $('dashboardBody');
    view.replaceChildren();
    view.append(metrics(data));
    const grid = el('div', 'dash-focus-grid');
    grid.append(taskCard(data, alerts), trendCard(data)); view.append(grid);
    const stamp = el('p','dash-note');
    stamp.textContent = '数据读取：' + (data.generated_at || '未提供').replace('T',' ').slice(0,19) + ' · 事件统计窗口见趋势图'; view.append(stamp);
  }

  async function load(params = {}) {
    const view = $('dashboardBody'); if (!view) return;
    const current = ++epoch; controller?.abort(); controller = new AbortController(); active = true;
    if (params.days) period = Number(params.days);
    if (!loaded) {
      const skeleton = el('div', 'dash-metrics');
      for (let i = 0; i < 4; i++) skeleton.append(el('div', 'dash-skeleton'));
      view.replaceChildren(skeleton, el('div', 'dash-empty', '正在读取平台数据…'));
    } else {
      view.classList.add('is-loading');
    }
    try {
      const [summaryResponse, alertsResponse] = await Promise.all([
        fetch('/dashboard/summary?days=' + period, { headers: config.headers(), signal: controller.signal, cache: 'no-store' }),
        fetch('/alerts?status=open&page_size=50', { headers: config.headers(), signal: controller.signal, cache: 'no-store' }).catch(() => null),
      ]);
      if (!summaryResponse.ok) {
        const message = { 401: '登录已失效，请重新设置凭据。', 403: '当前账号无权查看总览看板。',
          404: '当前服务尚未加载看板接口：请重启平台后刷新。', 503: '数据暂不可用，请稍后刷新。' }[summaryResponse.status] || ('读取失败（HTTP ' + summaryResponse.status + '）');
        throw new Error(message);
      }
      const data = await summaryResponse.json();
      let alerts = null;
      if (alertsResponse?.ok) { try { alerts = await alertsResponse.json(); } catch { alerts = null; } }
      if (!alerts) {
        // 旧进程尚未加载 /alerts 时用看板自带的待处置告警兜底，重启后自动切换。
        const label = { '紧急': 'critical', '重要': 'major', '次要': 'minor', '警告': 'warning' };
        alerts = { items: (data.pending_alarms || []).map(item => ({ ...item, severity: label[item.severity] || 'warning', device_type: '—' })) };
      }
      if (current !== epoch) return;
      loaded = data; render(data, alerts); view.classList.remove('is-loading');
      setTimeout(() => { fetch('/kb/catalog?page=1&page_size=12', { headers: config.headers(), cache: 'no-store' }).catch(() => {}); }, 1200);
    } catch (error) {
      if (error.name === 'AbortError' || current !== epoch) return;
      view.classList.remove('is-loading');
      const box = el('div', 'empty');
      box.append(el('strong', null, '看板数据加载失败'), el('p', null, error.message));
      const retry = el('button', 'ui-btn', '重新加载'); retry.type = 'button'; retry.onclick = () => { loaded = null; load(); };
      box.append(retry); view.replaceChildren(box);
    }
  }

  function init(options) {
    config = options;
    const refresh = $('dashRefresh'); if (refresh) refresh.onclick = () => { loaded = null; load(); };
  }

  return { init, load, leave() { controller?.abort(); epoch++; loaded = null; active = false; } };
})();
