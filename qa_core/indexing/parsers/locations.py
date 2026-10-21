"""Conservative source anchors: no guessed pages or paragraphs."""
import hashlib
import csv
import re
import unicodedata
from pathlib import Path
from qa_core.indexing.parsers.sanitize import sanitize


def canonical(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", sanitize(text)[0]))


def source_units(path):
    path = Path(path)
    if path.suffix.lower() in {".md", ".markdown", ".txt"}:
        return [(line, {"kind": "lines", "line_start": i, "line_end": i})
                for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1)]
    if path.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(path) as document:
            units = [(page.get_text("text"), {"kind": "page", "page": i})
                     for i, page in enumerate(document, 1)]
        if not any(text.strip() for text, _ in units):
            raise ValueError("PDF has no text layer; OCR is not supported")
        return units
    if path.suffix.lower() == ".docx":
        from docx import Document
        # Count all top-level paragraphs, including blanks, matching Word body order.
        return [(p.text, {"kind": "paragraph", "paragraph": i})
                for i, p in enumerate(Document(path).paragraphs, 1)]
    return []


def locate_chunks(path, chunks):
    if Path(path).suffix.lower() == ".csv":
        return locate_csv_chunks(path, chunks)
    units = source_units(path)
    parts, spans, offset = [], [], 0
    for text, location in units:
        part = canonical(text)
        parts.append(part); spans.append((offset, offset + len(part), location))
        offset += len(part)
    full = "".join(parts)
    file_digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    result = []
    for chunk in chunks:
        needle = canonical(chunk.text)
        context_prefix = False
        if needle not in full and chunk.doc_type == "sop" and "\n" in chunk.text:
            # SOP chunker prepends a title copied from the first source line.
            title, body = chunk.text.split("\n", 1)
            first = units[0][0].strip().lstrip("# ").strip() if units else ""
            if title == first:
                needle = canonical(body); context_prefix = True
        matches = []
        if needle:
            start = full.find(needle)
            while start >= 0:
                locations = [loc for a, b, loc in spans if a < start + len(needle) and b > start]
                if locations: matches.append(locations)
                if len(matches) > 8: break
                start = full.find(needle, start + 1)
        if not matches or len(matches) > 8:
            result.append(None)
        else:
            result.append({"kind": "source_spans", "format": Path(path).suffix.lower().lstrip("."),
                "matches": matches, "ambiguous": len(matches) > 1,
                "context_prefix": context_prefix, "file_sha256": file_digest})
    return result


def locate_csv_chunks(path, chunks):
    """Reconstruct sanitized FAQ/ticket chunks before assigning a CSV record.

    Both FAQ question and answer must agree. Logical records and physical line
    ranges remain distinct for quoted multiline cells. No identifier-only match.
    """
    from qa_core.indexing.chunkers.registry import chunk_faq, chunk_ticket
    index = {}

    def key(chunk):
        return (chunk.doc_type, chunk.metadata.get("source") or Path(path).name,
                canonical(chunk.text), canonical(str(chunk.metadata.get("answer", ""))))

    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if not fields or len(fields) != len(set(fields)):
            raise ValueError("CSV headers missing or duplicated")
        previous = reader.line_num
        for record, raw in enumerate(reader, 1):
            line_start, previous = previous + 1, reader.line_num
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError("CSV record does not match headers")
            row = {k: sanitize(str(v))[0] for k, v in raw.items()}
            kind = "faq" if {"question", "answer"} <= set(fields) else "fault_case"
            required = {"question", "answer"} if kind == "faq" else {"title", "phenomenon", "root_cause", "resolution"}
            if not required <= set(fields):
                return [None] * len(chunks)
            reconstructed = (chunk_faq if kind == "faq" else chunk_ticket)([row])
            location = {"kind": "csv_record", "record": record,
                        "line_start": line_start, "line_end": previous,
                        "columns": [f for f in fields if f in required and row[f]]}
            for candidate in reconstructed:
                index.setdefault(key(candidate), []).append(location)
    checksum = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    results = []
    for chunk in chunks:
        matches = index.get(key(chunk), [])
        if not matches or len(matches) > 8:
            results.append(None)
        else:
            results.append({"kind": "source_spans", "format": "csv", "matches": [[m] for m in matches],
                            "ambiguous": len(matches) > 1, "context_prefix": False, "file_sha256": checksum})
    return results
