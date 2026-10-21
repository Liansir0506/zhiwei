import hashlib
import json
from pathlib import Path
import pytest
from qa_core.config.settings import Settings
from scripts.original_generation import generation_settings
from scripts.prepare_original_citation_release import public_profile
from qa_core.generation.request_budget import reserve_validation_request


def prepared(tmp_path):
    source=tmp_path/'.env.runtime'
    source.write_text('LLM_BASE_URL=https://api.deepseek.com/v1\nLLM_MODEL=deepseek-flash\nLLM_API_KEY=test-private-key\n',encoding='utf-8')
    base=Settings(_env_file=None,generation_enabled=False,agent_enabled=False,auth_required=True,
                  llm_api_key='',llm_base_url='http://127.0.0.1:1/disabled',llm_model='disabled-model')
    receipt={'approved':True,'scope':'original_local_demo_generated_answers','base_profile':public_profile(base),
        'source_config_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'credential_sha256':hashlib.sha256(b'test-private-key').hexdigest(),
        'provider_url':'https://api.deepseek.com/v1','model':'deepseek-flash'}
    return base,receipt


def test_generation_approval_changes_only_model_fields(tmp_path):
    base,receipt=prepared(tmp_path)
    (tmp_path/'generation-approval.json').write_text(json.dumps(receipt),encoding='utf-8')
    enabled=generation_settings(base,tmp_path,tmp_path)
    changes={k for k,v in base.model_dump().items() if enabled.model_dump()[k]!=v}
    assert changes=={'generation_enabled','llm_api_key','llm_base_url','llm_model'}
    assert enabled.auth_required and not enabled.agent_enabled


@pytest.mark.parametrize('change',['approval','base','source','credential'])
def test_generation_approval_drift_fails_closed(tmp_path,change):
    base,receipt=prepared(tmp_path)
    if change=='approval':receipt['approved']=False
    if change=='base':receipt['base_profile']['doc_collection']='wrong'
    if change=='source':receipt['source_config_sha256']='wrong'
    if change=='credential':receipt['credential_sha256']='wrong'
    (tmp_path/'generation-approval.json').write_text(json.dumps(receipt),encoding='utf-8')
    with pytest.raises(ValueError):generation_settings(base,tmp_path,tmp_path)


def test_actual_attempt_budget_refuses_fifth_and_preserves_count(monkeypatch,tmp_path):
    path=tmp_path/'budget.json'
    path.write_text(json.dumps({'mode':'validating','limit':4,'used':0}),encoding='utf-8')
    monkeypatch.setenv('LLM_VALIDATION_BUDGET_FILE',str(path))
    for _ in range(4):reserve_validation_request()
    with pytest.raises(RuntimeError,match='exhausted'):reserve_validation_request()
    assert json.loads(path.read_text())['used']==4
    path.write_text(json.dumps({'mode':'interactive_approved','limit':4,'used':4}),encoding='utf-8')
    reserve_validation_request()
    assert json.loads(path.read_text())['used']==4


def test_deepseek_grounded_answers_send_non_thinking_payload_without_network():
    from unittest.mock import Mock
    from qa_core.generation.llm import LLMClient
    client=LLMClient('https://api.deepseek.com/v1','private','deepseek-flash')
    client._client.close()
    client._client=Mock()
    client._client.post.return_value.json.return_value={'choices':[{'message':{'content':'answer'}}]}
    assert client.chat([{'role':'user','content':'question'}],retries=0)=='answer'
    assert client._client.post.call_args.kwargs['json']['thinking']=={'type':'disabled'}


def test_stream_connection_failure_uses_llm_error_for_safe_fallback():
    import httpx
    from unittest.mock import Mock
    from qa_core.generation.llm import LLMClient, LLMError
    client=LLMClient('https://api.deepseek.com/v1','private','deepseek-flash')
    client._client.close()
    client._client=Mock()
    client._client.stream.return_value.__enter__=Mock(side_effect=httpx.ConnectTimeout('timeout'))
    client._client.stream.return_value.__exit__=Mock()
    with pytest.raises(LLMError,match='ConnectTimeout'):
        list(client.chat_stream([{'role':'user','content':'question'}]))
