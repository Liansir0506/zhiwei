const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
vm.runInThisContext(fs.readFileSync(path.join(root, 'static/sse-client.js'), 'utf8'));
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(inline, 'page inline script missing');
new Function(inline); // syntax check without a browser DOM

function stream(chunks) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const part of chunks) controller.enqueue(encoder.encode(part));
      controller.close();
    }
  });
}

(async () => {
  const seen = [];
  await SseClient.consume(stream([
    'event: me', 'ta\r', '\ndata: {"kb_version":"v1"}\r', '\n\r', '\n',
    'event: token\ndata: {"text":"暂态"}\n\n',
    'event: sources\ndata: {"sources":[]}\n\n',
    'event: done\ndata: {"status":"ANSWERED","answer":"最终"}\n\n'
  ]), (name, data) => seen.push([name, data]));
  assert.deepEqual(seen.map(x => x[0]), ['meta', 'token', 'sources', 'done']);
  assert.equal(seen.at(-1)[1].answer, '最终');
  await assert.rejects(() => SseClient.consume(stream([
    'event: meta\ndata: {}\n\n', 'event: token\ndata: {"text":"暂态"}\n\n'
  ]), () => {}), /未正常结束/);
  const errors = [];
  await SseClient.consume(stream(['event: error\ndata: {"code":"E504"}\n\n']),
    (name, data) => errors.push([name, data]));
  assert.equal(errors[0][0], 'error');
  console.log('sse parser and page syntax: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
