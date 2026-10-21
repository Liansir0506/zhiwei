"""Read stored FAQ answers without changing their source identity or content."""
import re


def evidence_text(doc):
    if getattr(doc, "doc_type", "") == "faq" and getattr(doc, "answer", ""):
        return (getattr(doc, "question", "") or doc.text or "") + "\n" + doc.answer
    return doc.text or ""


def rerank_text(query, doc):
    text = evidence_text(doc)
    if getattr(doc, "doc_type", "") != "faq" or not getattr(doc, "answer", ""):
        return text
    # Put query-bearing paragraphs before the model's token truncation. Keep
    # selection generic; this does not bypass rerank or coverage thresholds.
    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9-]+|[\u4e00-\u9fff]{2}", query.casefold()))
    paragraphs = [p for p in doc.answer.splitlines() if p.strip()]
    ranked = sorted(enumerate(paragraphs),
                    key=lambda item: (-sum(t in item[1].casefold() for t in tokens), item[0]))
    return (doc.question or doc.text or "") + "\n" + "\n".join(p for _, p in ranked)


def source_annotation_text(text):
    # Stored footnotes belong to the original document, whereas [n] in the
    # outer answer identifies a knowledge-base source. Preserve raw evidence.
    return re.sub(r"\[(\d{1,2})\]", r"〔原文注\1〕", text)
