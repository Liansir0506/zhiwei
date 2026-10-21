"""Shared terminal outcome vocabulary for sync and stream."""
def outcome(answer, *, refusal, degraded=False, intent="", needs_clarification=False):
    if intent in {"out_of_scope", "injection"}: return "OUT_OF_SCOPE"
    if needs_clarification: return "NEEDS_CLARIFICATION"
    if not answer or refusal: return "NO_EVIDENCE"
    return "DEGRADED" if degraded else "ANSWERED"
