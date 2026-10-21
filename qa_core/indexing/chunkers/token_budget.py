"""Real-tokenizer limits; never truncate atomic procedures."""
import copy

def bounded_chunks(chunks, tokenizer, max_tokens=450, atomic_tokens=1200):
    def fits(text, budget):
        return len(tokenizer.encode(text, add_special_tokens=True)) <= budget and len(text.encode("utf-8")) <= 7900
    result = []
    for chunk in chunks:
        if chunk.doc_type in {"sop", "fault_case", "faq"}:
            if chunk.doc_type == "faq" and len(str(chunk.metadata.get("answer", "")).encode("utf-8")) > 4096:
                raise ValueError("FAQ answer exceeds storage budget")
            if not fits(chunk.text, atomic_tokens): raise ValueError("atomic chunk exceeds token/storage budget")
            result.append(chunk); continue
        remaining = chunk.text
        while remaining.strip():
            if fits(remaining, max_tokens): piece, remaining = remaining, ""
            else:
                lo, hi = 0, len(remaining)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if fits(remaining[:mid], max_tokens): lo = mid
                    else: hi = mid - 1
                if not lo: raise ValueError("token budget cannot hold a character")
                piece, remaining = remaining[:lo], remaining[lo:]
            if not fits(piece, max_tokens): raise ValueError("token budget verification failed")
            part = copy.deepcopy(chunk); part.text = piece
            part.metadata["chunking_policy"] = "tokenizer-budget-v1"
            result.append(part)
    return result
