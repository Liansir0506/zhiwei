/* 统一时间显示：服务端时间戳为 UTC（无时区标记按 UTC 解析），本地时区友好展示。 */
window.FmtTime = (() => {
  const pad = value => String(value).padStart(2, '0');
  const parse = value => {
    if (value == null || value === '') return null;
    let text = String(value).trim();
    if (!/([Zz]|[+-]\d{2}:?\d{2})$/.test(text)) text = text.replace(' ', 'T') + 'Z';
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const full = value => {
    const date = parse(value);
    if (!date) return value == null || value === '' ? '—' : String(value);
    return pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  };
  const clock = value => {
    const date = parse(value);
    return date ? pad(date.getHours()) + ':' + pad(date.getMinutes()) : '—';
  };
  const relative = value => {
    const date = parse(value);
    if (!date) return '';
    const diff = (Date.now() - date.getTime()) / 1000;
    if (diff < 0) return full(value);
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' 天前';
    return full(value);
  };
  const friendly = value => {
    const date = parse(value);
    if (!date) return value == null || value === '' ? '—' : String(value);
    const diff = (Date.now() - date.getTime()) / 1000;
    return full(value) + (diff >= 0 && diff < 86400 ? ' · ' + relative(value) : '');
  };
  const hourLabel = value => {
    const match = /^(\d{2})-(\d{2}) (\d{2}):00$/.exec(String(value == null ? '' : value));
    if (!match) return value == null || value === '' ? '—' : String(value);
    const now = new Date();
    let date = new Date(now.getFullYear() + '-' + match[1] + '-' + match[2] + 'T' + match[3] + ':00:00Z');
    if (date.getTime() - now.getTime() > 86400000) date = new Date((now.getFullYear() - 1) + '-' + match[1] + '-' + match[2] + 'T' + match[3] + ':00:00Z');
    return pad(date.getHours()) + ':' + pad(date.getMinutes());
  };
  return {parse, full, clock, relative, friendly, hourLabel};
})();
