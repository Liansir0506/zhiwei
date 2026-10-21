"""Explicitly approved generation overrides for the owned local runtime only."""
import hashlib
import json
from dotenv import dotenv_values
from scripts.prepare_original_citation_release import public_profile


def generation_settings(base, project, runtime_root):
    receipt_path = runtime_root / 'generation-approval.json'
    if not receipt_path.exists():
        return base
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if (receipt.get('approved') is not True
            or receipt.get('scope') != 'original_local_demo_generated_answers'
            or receipt.get('base_profile') != public_profile(base)):
        raise ValueError('generation requires approval for this exact base profile')
    source = project / '.env.runtime'
    if hashlib.sha256(source.read_bytes()).hexdigest() != receipt['source_config_sha256']:
        raise ValueError('approved generation source configuration changed')
    configured = dotenv_values(source)
    key = configured.get('LLM_API_KEY') or ''
    if (not key or hashlib.sha256(key.encode()).hexdigest() != receipt['credential_sha256']
            or configured.get('LLM_BASE_URL') != receipt['provider_url']
            or configured.get('LLM_MODEL') != receipt['model']):
        raise ValueError('approved generation provider or credential changed')
    if not base.auth_required or base.agent_enabled:
        raise ValueError('local generation requires existing authentication and disabled Agent')
    return base.model_copy(update={'generation_enabled': True, 'llm_api_key': key,
        'llm_base_url': receipt['provider_url'], 'llm_model': receipt['model']})
