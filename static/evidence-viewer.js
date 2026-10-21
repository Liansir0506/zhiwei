/* Read-only citation viewer. Never use admin routes or trust a supplied file URL. */
window.EvidenceViewer = {
  async loadAll(source, headers) {
    const children = source.workflow_evidence ? source.child_evidence : [source];
    if (!Array.isArray(children) || !children.length || children.length > 64)
      throw new Error("流程缺少完整、可验证的子引用。");
    const result = [];
    for (let i = 0; i < children.length; i++) {
      // Stay within ordinary-viewer rate limits; never grant admin headers.
      if (i) await new Promise(resolve => setTimeout(resolve, 250));
      result.push(await this.load(children[i], headers));
    }
    return result; // A missing/withdrawn child rejects the whole group, not a partial SOP.
  },
  async load(source, headers) {
    const id = source.evidence_id;
    if (!id || id.length > 2048 || !/^[A-Za-z0-9_-]+$/.test(id))
      throw new Error("此旧引用尚无可验证的原文入口，不通过管理接口代查。");
    const response = await fetch(`/kb/evidence/${encodeURIComponent(id)}`, {
      headers, cache: "no-store"
    });
    if (!response.ok) {
      const messages = {401: "请先配置有效的 API Key。", 404: "引用已失效、资料已撤回或无权访问。",
        429: "查看过于频繁，请稍后重试。", 503: "原文验证暂不可用，请稍后重试。"};
      throw new Error(messages[response.status] || "原文读取失败。");
    }
    return response.json();
  },
  location(location) {
    if (!location || location.kind === "legacy") return "旧资料：尚无可靠原文定位";
    if (location.kind === "step") return `步骤 ${(location.steps || []).join("、")}`;
    if (location.kind === "section") return `章节 ${location.section}`;
    if (location.kind === "source_spans") {
      const labels = (location.matches || []).map(spans => spans.map(p =>
        p.kind === "page" ? `第 ${p.page} 页` : p.kind === "paragraph" ? `第 ${p.paragraph} 段` :
          p.kind === "csv_record" ? `第 ${p.record} 条记录（第 ${p.line_start}${p.line_end !== p.line_start ? `–${p.line_end}` : ""} 行）` :
          `第 ${p.line_start} 行`).join(" · "));
      return `${location.ambiguous ? "原文多处匹配：" : "原文位置："}${labels.join(" / ")}`;
    }
    return "定位信息不可用";
  },
  reviewNotice(evidence) {
    // The successful authenticated read establishes access, not a content-review decision.
    const access = "访问状态：已通过当前版本与权限校验";
    const historical = /待人工审核|尚待人工审核|尚未完成(?:人工)?审核/.test(evidence.text || "");
    return access + (historical ? "\n原文审核声明：保留所示来源修订的记录；使用范围以资料准入记录为准。" : "");
  }
};
