/* Deliberately small Markdown subset; all untrusted content enters text nodes. */
window.AnswerRenderer = (() => {
  function inline(target, text) {
    const pattern = /(\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[(\d{1,2})\])/g;
    let last = 0, match;
    while ((match = pattern.exec(text))) {
      target.append(document.createTextNode(text.slice(last, match.index)));
      const part = document.createElement(match[2] ? 'strong' : match[3] ? 'code' : 'span');
      if (match[4]) part.className = 'ref';
      part.textContent = match[2] || match[3] || match[0]; target.append(part);
      last = pattern.lastIndex;
    }
    target.append(document.createTextNode(text.slice(last)));
  }
  function render(container, value) {
    const answer = document.createElement('div'); answer.className = 'answer';
    const lines = String(value || '').replace(/\r\n?/g, '\n').split('\n');
    let list = null, paragraph = null;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        list = paragraph = null;
        const pre = document.createElement('pre'), code = document.createElement('code');
        pre.className = 'code'; const content = [];
        while (++i < lines.length && !/^\s*```/.test(lines[i])) content.push(lines[i]);
        code.textContent = content.join('\n'); pre.append(code); answer.append(pre); continue;
      }
      if (!line.trim()) { list = paragraph = null; continue; }
      const heading = line.match(/^\s*(#{1,4})\s+(.+)$/) || line.match(/^\s*(【[^】]{1,12}】)\s*$/);
      if (heading) {
        list = paragraph = null; const h = document.createElement('h3');
        inline(h, heading[2] || heading[1]); answer.append(h); continue;
      }
      const item = line.match(/^\s*(?:([-*+])\s+|(\d+)[.)、]\s+)(.+)$/);
      if (item) {
        paragraph = null; const kind = item[2] ? 'OL' : 'UL';
        if (!list || list.tagName !== kind) {
          list = document.createElement(kind.toLowerCase());
          if (item[2]) list.start = Number(item[2]); answer.append(list);
        }
        const li = document.createElement('li'); inline(li, item[3]); list.append(li); continue;
      }
      list = null;
      if (!paragraph) { paragraph = document.createElement('p'); answer.append(paragraph); }
      else paragraph.append(document.createElement('br'));
      inline(paragraph, line);
    }
    container.replaceChildren(answer);
  }
  return {render};
})();
