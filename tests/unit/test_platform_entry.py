"""Availability failure/recovery scenarios; no real Docker mutations or paid calls."""
import json
from types import SimpleNamespace
import pytest
from scripts import platform_entry as entry


class DockerScenario:
    def __init__(self, engine=(True,), health=('healthy',), missing=None, foreign=None, stopped=False):
        self.engine = iter(engine)
        self.current_engine = False
        self.health = iter(health)
        self.current_health = 'starting'
        self.missing, self.foreign, self.stopped = missing, foreign, stopped
        self.commands = []
        self.now = 0
        self.launched = 0

    def sleep(self, seconds):
        self.now += seconds

    def command(self, args, **kwargs):
        self.commands.append(args)
        if args[1] == 'context':
            return SimpleNamespace(returncode=0, stdout=json.dumps(next(iter(entry.LOCAL_ENGINES))))
        if args[1] == 'info':
            self.current_engine = next(self.engine, self.current_engine)
            return SimpleNamespace(returncode=0 if self.current_engine else 1,
                                   stdout=json.dumps('28.0.0' if self.current_engine else ''))
        if args[1] == 'start':
            self.stopped = False
            return SimpleNamespace(returncode=0)
        name = args[-1]
        service = entry.SERVICES[entry.CONTAINERS.index(name)]
        if service == 'mysql':
            self.current_health = next(self.health, self.current_health)
        return SimpleNamespace(returncode=int(name == self.missing), stdout=json.dumps(
            {'Status': 'exited' if self.stopped else 'running', 'Health': {'Status': self.current_health}})
            + '|' + json.dumps({'com.docker.compose.project': 'foreign' if name == self.foreign else 'telecom-ops-rag',
                               'com.docker.compose.service': service}))

    def dependencies(self):
        deps = entry.Dependencies(self.command, self.sleep, lambda: self.now)
        deps.launch_desktop = lambda: setattr(self, 'launched', self.launched + 1)
        return deps


@pytest.fixture(autouse=True)
def no_override(monkeypatch):
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)


def test_engine_off_then_storage_preheat_waits_for_health():
    scenario = DockerScenario(engine=(False, False, True), health=('starting', 'starting', 'healthy'), stopped=True)
    phases = []
    result = scenario.dependencies().prepare(lambda code, message: phases.append(code))
    assert scenario.launched == 1 and scenario.now == 6
    assert phases == ['docker_starting', 'dependencies_warming']
    assert all(row['health'] == 'healthy' for row in result)
    assert [cmd for cmd in scenario.commands if cmd[1] == 'start'] == [['docker', 'start', *entry.CONTAINERS]]
    assert all(not any(service in ' '.join(cmd) for service in ('ingest-worker', 'enterprise-mock', '-api-')) for cmd in scenario.commands)


def test_engine_never_ready_is_actionable_timeout():
    scenario = DockerScenario(engine=(False,))
    with pytest.raises(entry.EntryFailure, match='Docker/WSL') as error:
        scenario.dependencies().prepare(lambda *args: None, engine_timeout=6)
    assert error.value.code == 'docker_start_timeout'
    assert not any(cmd[1] in ('inspect', 'start') for cmd in scenario.commands)


@pytest.mark.parametrize('stdout',['""','null','{}','invalid','   '])
def test_zero_exit_without_server_version_is_not_ready(stdout):
    deps=entry.Dependencies(command=lambda *a,**k:SimpleNamespace(returncode=0,stdout=stdout))
    assert deps.engine_ready() is False


def test_engine_disappearing_during_attestation_is_not_container_missing():
    scenario=DockerScenario(engine=(True,False),missing=entry.CONTAINERS[0])
    with pytest.raises(entry.EntryFailure) as error:scenario.dependencies().prepare(lambda *a:None)
    assert error.value.code=='docker_unavailable'
    assert not any(c[1]=='start' for c in scenario.commands)


def test_desktop_launch_uses_hidden_shell_outside_caller_job(monkeypatch):
    calls=[]
    monkeypatch.setattr(entry.Path,'is_file',lambda self:True)
    deps=entry.Dependencies(command=lambda command,**kw:(calls.append(command) or SimpleNamespace(returncode=0)))
    deps.launch_desktop()
    assert 'ShellExecute' in calls[0][-1] and "'open', 0" in calls[0][-1]


def test_storage_never_healthy_never_reports_ready():
    scenario = DockerScenario(health=('starting',))
    with pytest.raises(entry.EntryFailure, match='依赖预热超时') as error:
        scenario.dependencies().prepare(lambda *args: None, dependency_timeout=6)
    assert error.value.code == 'dependency_timeout'
    assert 'milvus:running/starting' in error.value.message


@pytest.mark.parametrize('field,code', [('missing', 'container_missing'), ('foreign', 'container_identity')])
def test_attest_all_containers_before_any_start(field, code):
    scenario = DockerScenario(stopped=True, **{field: entry.CONTAINERS[-1]})
    with pytest.raises(entry.EntryFailure) as error:
        scenario.dependencies().prepare(lambda *args: None)
    assert error.value.code == code
    assert not any(cmd[1] == 'start' for cmd in scenario.commands)


@pytest.mark.parametrize('variable', ['DOCKER_CONTEXT', 'DOCKER_HOST'])
def test_connection_override_cannot_mutate_other_docker(monkeypatch, variable):
    monkeypatch.setenv(variable, 'remote-machine')
    scenario = DockerScenario()
    with pytest.raises(entry.EntryFailure) as error:
        scenario.dependencies().prepare(lambda *args: None)
    assert error.value.code == 'docker_override' and not scenario.commands


def test_continuous_observation_detects_interruption_and_recovery(tmp_path):
    samples = iter([{'status': 'ready', 'code': 'healthy', 'message': 'ready'},
                    {'status': 'unavailable', 'code': 'runtime_unreachable', 'message': 'offline'},
                    {'status': 'unavailable', 'code': 'runtime_unreachable', 'message': 'offline'},
                    {'status': 'ready', 'code': 'healthy', 'message': 'recovered'}])
    probe = SimpleNamespace(sample=lambda: next(samples))
    entry.observe(probe, count=4, interval=1, root=tmp_path, sleep=lambda _: None)
    rows = [json.loads(line) for line in (tmp_path / 'observations.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [row['consecutive_failures'] for row in rows] == [0, 1, 2, 0]
    assert json.loads((tmp_path / 'availability.json').read_text(encoding='utf-8'))['status'] == 'ready'
    assert 'recovered' in (tmp_path / 'status.html').read_text(encoding='utf-8')


def test_failed_binding_never_prepares_or_launches(monkeypatch, tmp_path):
    monkeypatch.setattr(entry, 'ROOT', tmp_path)
    monkeypatch.setattr(entry, 'Dependencies', lambda: pytest.fail('invalid binding cannot reach Docker'))
    # 入口链已切换 r2 修订运行时（绑定失效必须同样绝不触达 Docker/启动）。
    from scripts import demo_release_runtime_r2 as release
    monkeypatch.setattr(release, 'install_adapter', lambda: (_ for _ in ()).throw(ValueError('binding changed')))
    with pytest.raises(ValueError, match='binding changed'):
        entry.prepare()


def test_health_error_secrets_are_not_written(monkeypatch):
    scenario = DockerScenario()
    dependencies = scenario.dependencies()
    monkeypatch.setattr(entry, 'Dependencies', lambda: dependencies)
    probe = entry.RuntimeProbe.__new__(entry.RuntimeProbe)
    probe.release = SimpleNamespace(owned=SimpleNamespace(inspect=lambda: {'status': 'running', 'pid': 1, 'url': 'http://127.0.0.1:8010'}),
                                    selected_settings=lambda: SimpleNamespace(generation_enabled=True))
    health = {'status': 'degraded', 'generation_enabled': True, 'agent_enabled': False,
              'components': {'mysql': 'error mysql://secret-password', 'redis': 'ok', 'milvus': 'ok', 'models': 'ok'}}
    monkeypatch.setattr(entry.httpx, 'get', lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: health))
    sample = probe.sample()
    assert sample['status'] == 'degraded' and sample['components']['mysql'] == 'unhealthy'
    assert 'secret-password' not in json.dumps(sample)


def test_observer_probe_exception_is_visible_and_next_sample_can_recover(tmp_path):
    calls = 0
    def sample():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError('secret://private-configuration')
        return {'status': 'ready', 'code': 'healthy', 'message': 'recovered'}
    entry.observe(SimpleNamespace(sample=sample), count=2, interval=1, root=tmp_path, sleep=lambda _: None)
    text = (tmp_path / 'observations.jsonl').read_text(encoding='utf-8')
    records = [json.loads(line) for line in text.splitlines()]
    assert [record['status'] for record in records] == ['blocked', 'ready']
    assert records[0]['error_type'] == 'OSError' and 'private-configuration' not in text


def test_observer_stop_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(entry, 'ROOT', tmp_path)
    entry.stop_observer()
    entry.stop_observer()
    assert json.loads((tmp_path / 'observer.json').read_text(encoding='utf-8'))['status'] == 'stopped'


def test_observer_pid_reuse_never_terminates_unrelated_process(monkeypatch, tmp_path):
    monkeypatch.setattr(entry, 'ROOT', tmp_path)
    entry.write_json(tmp_path / 'observer.json', {'status': 'running', 'pid': 1, 'created_at': 1, 'instance': 'old'})
    process = SimpleNamespace(create_time=lambda: 2, cmdline=lambda: ['other-process'],
                              cwd=lambda: str(entry.PROJECT), terminate=lambda: pytest.fail('unrelated process terminated'))
    monkeypatch.setattr(entry.psutil, 'Process', lambda _: process)
    with pytest.raises(entry.EntryFailure) as error:
        entry.stop_observer()
    assert error.value.code == 'observer_identity'


def test_bootstrap_phase_requires_actual_process_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(entry, 'ROOT', tmp_path)
    token = 'a' * 32
    entry.write_json(tmp_path / 'bootstrap.json', {'status': 'starting', 'pid': 1, 'created_at': 1,
                     'token': token, 'phase': 'verify_active_snapshot'})
    process = SimpleNamespace(create_time=lambda: 1, cwd=lambda: str(entry.PROJECT),
                              cmdline=lambda: ['python', '-m', 'scripts.platform_runtime_bootstrap', '--token', token])
    monkeypatch.setattr(entry.psutil, 'Process', lambda _: process)
    assert entry.bootstrap_phase() == '核验已发布知识快照'
    process.create_time = lambda: 2
    with pytest.raises(entry.EntryFailure) as error:
        entry.bootstrap_phase()
    assert error.value.code == 'bootstrap_identity'
