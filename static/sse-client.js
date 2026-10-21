/* Incremental POST-SSE parser: JSON frames, chunk-safe and terminal-aware. */
globalThis.SseClient = Object.freeze({
  async consume(body, onEvent) {
    if (!body) throw new Error("服务端未返回流式响应");
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", terminal = false;
    const handle = block => {
      let name = "message";
      const data = [];
      for (const line of block.split(/\r\n|\n|\r/)) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (!data.length) return;
      const payload = JSON.parse(data.join("\n"));
      if (name === "done" || name === "error") terminal = true;
      onEvent(name, payload);
    };
    const drain = () => {
      let match;
      while ((match = /\r\n\r\n|\n\n|\r\r/.exec(buffer))) {
        handle(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
      }
    };
    try {
      while (!terminal) {
        const { done, value } = await reader.read();
        if (done) {
          buffer += decoder.decode();
          drain();
          if (buffer.trim()) handle(buffer);
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        if (buffer.length > 1024 * 1024) throw new Error("流式响应超过安全长度");
        drain();
      }
      if (!terminal) throw new Error("流式响应未正常结束，请检查结果后手动重试");
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
});
