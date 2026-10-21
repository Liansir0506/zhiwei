"""Count each attempted provider POST, including retries, during paid validation."""
import json
import os
from pathlib import Path
import threading

_lock = threading.Lock()


def reserve_validation_request():
    configured = os.environ.get('LLM_VALIDATION_BUDGET_FILE')
    if not configured:
        return
    path = Path(configured)
    with _lock:
        budget = json.loads(path.read_text(encoding='utf-8'))
        if budget.get('mode') == 'interactive_approved':
            return
        if budget.get('mode') != 'validating' or budget['used'] >= budget['limit']:
            raise RuntimeError('paid validation request budget exhausted')
        budget['used'] += 1
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(budget, indent=2), encoding='utf-8')
        temporary.replace(path)
