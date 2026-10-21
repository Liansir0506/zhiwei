from pathlib import Path
import json
import httpx
import pytest
from scripts import content_quality_release_runtime as release


def probe(monkeypatch,status,process=True,error=None):
    script=(release.PROJECT/'scripts/start_content_quality_background_v2.ps1').read_text(encoding='utf-8')
    code=script.split("$inspectCode = @'\n",1)[1].split("\n'@",1)[0]
    monkeypatch.setattr(release,'install_adapter',lambda:None)
    def inspect():
        if error:raise error
        return {'status':status}
    monkeypatch.setattr(release.owned,'inspect',inspect)
    monkeypatch.setattr(release.owned,'load_state',lambda:{'status':status})
    monkeypatch.setattr(release.owned,'owned_process',lambda state:object() if process else None)
    exec(code,{})


def test_attested_preheat_can_wait_without_lifecycle_lock(monkeypatch,capsys):
    monkeypatch.setattr(release.owned,'command_lock',lambda engine:pytest.fail('inspection cannot acquire startup lock'))
    probe(monkeypatch,'starting',error=httpx.ConnectError('not listening yet'))
    assert json.loads(capsys.readouterr().out)=={'status':'starting'}


@pytest.mark.parametrize('status,process',[('running',True),('stopped',True),('starting',False)])
def test_transport_failure_without_attested_preheat_is_not_accepted(monkeypatch,status,process):
    with pytest.raises(httpx.ConnectError):probe(monkeypatch,status,process,error=httpx.ConnectError('refused'))


def test_binding_error_never_becomes_preheat(monkeypatch):
    with pytest.raises(ValueError,match='identity mismatch'):
        probe(monkeypatch,'starting',error=ValueError('identity mismatch'))


def test_running_result_is_real_inspection(monkeypatch,capsys):
    probe(monkeypatch,'running')
    assert json.loads(capsys.readouterr().out)=={'status':'running'}
