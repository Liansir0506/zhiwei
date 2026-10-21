"""Local availability orchestration; never publish, ingest, or call a paid model."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx
import psutil

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / '.runtime/platform-entry'
SERVICES = ('mysql', 'redis', 'etcd', 'minio', 'milvus')
CONTAINERS = tuple('telecom-ops-rag-' + service + '-1' for service in SERVICES)
LOCAL_ENGINES = {'npipe:////./pipe/dockerDesktopLinuxEngine', 'npipe:////./pipe/docker_engine'}


class EntryFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def run(command, timeout=20):
    try:
        return subprocess.run(command, cwd=PROJECT, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except FileNotFoundError:
        raise EntryFailure('executable_missing', '缺少本地启动工具，请检查Docker Desktop及虚拟环境。') from None
    except subprocess.TimeoutExpired:
        raise EntryFailure('command_timeout', '本地依赖检查超时，请查看Docker Desktop状态。') from None


class Dependencies:
    def __init__(self, command=run, sleep=time.sleep, clock=time.monotonic):
        self.command, self.sleep, self.clock = command, sleep, clock

    def local_engine(self):
        # Never start containers against a remote Docker context or inherited override.
        override = os.environ.get('DOCKER_HOST')
        if os.environ.get('DOCKER_CONTEXT') or override:
            raise EntryFailure('docker_override', 'Docker连接存在冲突覆盖，请使用本机Docker Desktop上下文。')
        result = self.command(['docker', 'context', 'inspect', '--format', '{{json .Endpoints.docker.Host}}'])
        try:
            host = json.loads(result.stdout)
        except (ValueError, TypeError):
            host = None
        if result.returncode or host not in LOCAL_ENGINES:
            raise EntryFailure('docker_context', '当前Docker连接不是本机Windows引擎；未启动任何容器。')

    def engine_ready(self):
        result = self.command(['docker', 'info', '--format', '{{json .ServerVersion}}'])
        # Some Docker CLI versions return zero plus JSON "" on a missing pipe.
        # A successful exit code alone is not proof that the daemon answered.
        try:
            version = json.loads(result.stdout)
        except (ValueError, TypeError):
            return False
        return result.returncode == 0 and isinstance(version, str) and bool(version.strip())

    def launch_desktop(self):
        executable = Path('C:/Program Files/Docker/Docker/Docker Desktop.exe')
        if not executable.is_file():
            raise EntryFailure('docker_desktop_missing', '未找到Docker Desktop，请安装或启动本机Docker。')
        result = self.command(['powershell.exe', '-NoProfile', '-Command',
                              "$desktopShell = New-Object -ComObject Shell.Application; "
                              "$desktopShell.ShellExecute('C:/Program Files/Docker/Docker/Docker Desktop.exe', '', "
                              "'C:/Program Files/Docker/Docker', 'open', 0)"])
        if result.returncode:
            raise EntryFailure('docker_desktop_start', 'Docker Desktop启动失败，请检查本机Docker/WSL状态。')

    def containers(self):
        template = '{{json .State}}|{{json .Config.Labels}}'
        results = []
        for service, name in zip(SERVICES, CONTAINERS):
            result = self.command(['docker', 'inspect', '--format', template, name])
            if result.returncode:
                if not self.engine_ready():
                    raise EntryFailure('docker_unavailable', 'Docker引擎在准备期间断开；不能据此判断容器缺失，应用尚未启动。')
                raise EntryFailure('container_missing', '缺少既有存储容器：' + name + '；未创建容器或下载镜像。')
            try:
                state_text, label_text = result.stdout.strip().split('|', 1)
                state, labels = json.loads(state_text), json.loads(label_text)
                valid = labels.get('com.docker.compose.project') == 'telecom-ops-rag' and labels.get('com.docker.compose.service') == service
            except (ValueError, AttributeError):
                valid = False
            if not valid:
                raise EntryFailure('container_identity', '存储容器归属不匹配：' + name + '；未执行启动。')
            results.append({'service': service, 'name': name, 'status': state['Status'],
                            'health': state.get('Health', {}).get('Status', 'missing')})
        return results

    def prepare(self, notify, engine_timeout=300, dependency_timeout=240):
        self.local_engine()
        if not self.engine_ready():
            notify('docker_starting', 'Docker引擎未运行，正在启动本机Docker Desktop。')
            self.launch_desktop()
            deadline = self.clock() + engine_timeout
            while not self.engine_ready():
                if self.clock() >= deadline:
                    raise EntryFailure('docker_start_timeout', 'Docker/WSL引擎启动超时；应用尚未启动。')
                self.sleep(3)
        initial = self.containers()  # Attest all five before starting any.
        stopped = [row['name'] for row in initial if row['status'] not in ('running', 'restarting')]
        if stopped:
            result = self.command(['docker', 'start', *stopped], timeout=60)
            if result.returncode:
                raise EntryFailure('storage_start_failed', '既有存储启动失败；请查看Docker Desktop，不要启动旧API或Worker。')
        notify('dependencies_warming', '等待五个存储健康检查通过；此时应用尚未就绪。')
        deadline = self.clock() + dependency_timeout
        while True:
            rows = self.containers()
            if all(row['status'] == 'running' and row['health'] == 'healthy' for row in rows):
                return rows
            if self.clock() >= deadline:
                waiting = '、'.join(row['service'] + ':' + row['status'] + '/' + row['health'] for row in rows
                                   if row['status'] != 'running' or row['health'] != 'healthy')
                raise EntryFailure('dependency_timeout', '依赖预热超时：' + waiting + '；未启动应用。')
            self.sleep(3)


class RuntimeProbe:
    def __init__(self):
        from scripts import demo_release_runtime_r2 as release
        release.install_adapter()
        self.release = release

    def sample(self):
        owned = self.release.owned
        try:
            dependencies = Dependencies()
            dependencies.local_engine()
            if not dependencies.engine_ready():
                return {'status': 'unavailable', 'code': 'docker_unavailable', 'message': 'Docker引擎不可用；请使用统一入口恢复。'}
            storage = dependencies.containers()
            unhealthy_storage = [row['service'] for row in storage if row['status'] != 'running' or row['health'] != 'healthy']
            if unhealthy_storage:
                return {'status': 'degraded', 'code': 'storage_unhealthy', 'message': '存储未就绪：' + '、'.join(unhealthy_storage), 'storage': storage}
            result = owned.inspect()
            if result['status'] != 'running':
                phase = bootstrap_phase()
                if phase:
                    return {'status': 'starting', 'code': 'application_preparing', 'message': '应用正在准备：' + phase + '；完成前不可用。'}
                return {'status': 'unavailable', 'code': 'runtime_' + result['status'], 'message': '应用进程未运行；请使用统一入口恢复。'}
            settings = self.release.selected_settings()
            response = httpx.get(result['url'] + '/health?refresh=1', timeout=12, trust_env=False)
            response.raise_for_status()
            health = response.json()
            if health.get('generation_enabled') != settings.generation_enabled or health.get('agent_enabled') is not False:
                raise EntryFailure('runtime_mode_mismatch', '运行模式与获准配置不一致；未执行自动恢复。')
            components = health.get('components', {})
            bad = [name for name in ('mysql', 'redis', 'milvus', 'models')
                   if not str(components.get(name, '')).startswith('ok')]
            # Health errors can contain DSNs; only persist normalized availability.
            components = {name: 'unhealthy' if name in bad else 'ok' for name in ('mysql', 'redis', 'milvus', 'models')}
            if health.get('status') != 'ok' or bad:
                return {'status': 'degraded', 'code': 'component_unhealthy', 'message': '组件不可用：' + '、'.join(bad or ['health']), 'components': components}
            state = owned.load_state()
            key = owned.read_key(state)
            access = httpx.get(result['url'] + '/admin/capabilities', headers={'X-API-Key': key}, timeout=8, trust_env=False)
            if access.status_code != 200:
                return {'status': 'blocked', 'code': 'local_auth_failed', 'message': '本机凭据鉴权失败；请先用stop_content_quality.ps1停止自有入口，再用统一入口恢复。'}
            return {'status': 'ready', 'code': 'healthy', 'message': '本机依赖与应用可用；不代表答案质量验收。', 'pid': result['pid'], 'components': components}
        except EntryFailure:
            raise
        except httpx.TransportError:
            state = owned.load_state()
            if state and state.get('status') == 'starting' and owned.owned_process(state) is not None:
                return {'status': 'starting', 'code': 'application_warming', 'message': '已鉴定的应用进程正在预热，入口尚未就绪。'}
            return {'status': 'unavailable', 'code': 'runtime_unreachable', 'message': '应用入口不可达或仍在预热；请查看启动状态。'}
        except (httpx.HTTPStatusError, ValueError, KeyError, TypeError):
            return {'status': 'blocked', 'code': 'runtime_identity_or_health', 'message': '运行归属、配置绑定或健康响应不符合预期；未执行自动恢复。'}


def bootstrap_phase():
    path = ROOT / 'bootstrap.json'
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('status') != 'starting':
        return None
    try:
        process = psutil.Process(state['pid'])
        command = process.cmdline()
        if (abs(process.create_time() - state['created_at']) > .01 or Path(process.cwd()).resolve() != PROJECT
            or 'scripts.platform_runtime_bootstrap' not in command or '--token' not in command
            or command[command.index('--token') + 1] != state['token']):
            raise EntryFailure('bootstrap_identity', '启动进程归属不符；未继续启动或终止其他进程。')
    except psutil.NoSuchProcess:
        return None
    return {'validate_bindings': '校验获准配置', 'verify_active_snapshot': '核验已发布知识快照',
            'active_snapshot_verified': '快照已通过，准备启动', 'application_warmup': '模型与应用预热'}.get(state['phase'], '启动校验')


def record(sample, *, root=ROOT):
    sample = dict(sample, checked_at_utc=stamp())
    write_json(root / 'availability.json', sample)
    log_path = root / 'observations.jsonl'
    if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
        os.replace(log_path, root / 'observations.previous.jsonl')
    with log_path.open('a', encoding='utf-8') as log:
        log.write(json.dumps(sample, ensure_ascii=False) + '\n')
    message = html.escape(sample['message'])
    login = (PROJECT / '.runtime/original-local/open-platform.html').as_uri()
    (root / 'status.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="10"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>智维平台启动状态</title><style>body{font:17px/1.7 system-ui;background:#f3f6fb;color:#19304f;padding:40px}'
        'main{max-width:700px;margin:auto;background:white;padding:30px;border-radius:12px}</style><main><h1>平台可用状态</h1>'
        '<p>' + message + '</p><p>检查时间（UTC）：' + sample['checked_at_utc'] + '</p>'
        + ('<p id="login"><a href="' + html.escape(login, quote=True) + '">使用本机凭据进入平台</a></p>' if sample['status'] == 'ready' else '')
        + '<p id="freshness"></p><p>页面每10秒刷新；后台约每30秒检查。观察停止后，时间戳不会继续更新。</p></main>'
        '<script>const checked=' + json.dumps(sample['checked_at_utc']) + ';function freshness(){'
        'if(Date.now()-Date.parse(checked)>90000){document.getElementById("freshness").textContent='
        '"状态已超过90秒未更新，不能据此确认当前可用；请重新执行统一入口。";'
        'const login=document.getElementById("login");if(login)login.hidden=true;}}freshness();setInterval(freshness,1000);</script></html>', encoding='utf-8')
    return sample


def observe(probe, *, count=0, interval=30, root=ROOT, sleep=time.sleep):
    failures = 0
    for index in range(count or sys.maxsize):
        try:
            sample = probe.sample()
        except EntryFailure as error:
            sample = {'status': 'blocked', 'code': error.code, 'message': error.message}
        except Exception as error:
            sample = {'status': 'blocked', 'code': 'probe_failed', 'message': '可用性检查未完成；请检查本地工具、凭据文件和运行状态。', 'error_type': type(error).__name__}
        failures = failures + 1 if sample['status'] != 'ready' else 0
        record(dict(sample, consecutive_failures=failures), root=root)
        if count and index + 1 == count:
            break
        sleep(interval)


def prepare():
    def progress(code, message):
        record({'status': 'starting', 'code': code, 'message': message})
        print(message, flush=True)
    with exclusive(ROOT / 'prepare.lock'):
        # Bindings must be valid before any dependency mutation.
        probe = RuntimeProbe()
        Dependencies().prepare(progress)
        progress('application_starting', '存储已健康，正在验证并启动获准应用；等待模型预热。')
        result = run(['powershell.exe', '-NoProfile', '-File', str(PROJECT / 'scripts/start_platform_runtime.ps1')], timeout=420)
        if result.returncode:
            write_json(ROOT / 'startup-command-failure.json', {'checked_at_utc': stamp(), 'returncode': result.returncode,
                       'stdout': result.stdout, 'stderr': result.stderr})
            raise EntryFailure('application_start_failed', '应用启动失败；请查看.runtime/original-local/server.log及运行绑定，未自动发布或回滚。')
        sample = probe.sample()
        if sample['status'] != 'ready':
            raise EntryFailure(sample['code'], sample['message'])
        record(sample)
        # Desktop shell survives parent console exit. The watcher owns its own lock.
        result = run(['powershell.exe', '-NoProfile', '-File', str(PROJECT / 'scripts/start_platform_observer.ps1'), '-Instance', uuid.uuid4().hex])
        if result.returncode:
            raise EntryFailure('observer_start_failed', '应用已可用，但后台存活观察启动失败；请运行watch命令。')
        deadline = time.monotonic() + 30
        while observer_process() is None:
            if time.monotonic() >= deadline:
                raise EntryFailure('observer_start_timeout', '应用已可用，但观察进程未确认启动；请查看观察状态。')
            time.sleep(1)
        print(json.dumps(sample, ensure_ascii=False))


def observer_process():
    path = ROOT / 'observer.json'
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('status') != 'running':
        return None
    try:
        process = psutil.Process(state['pid'])
        command = process.cmdline()
        if (abs(process.create_time() - state['created_at']) > .01 or Path(process.cwd()).resolve() != PROJECT
            or 'scripts.platform_entry' not in command or 'watch' not in command
            or (command[command.index('--instance') + 1] if '--instance' in command else 'foreground') != state['instance']):
            raise EntryFailure('observer_identity', '观察进程归属不符；未终止其他进程。')
        return process
    except psutil.NoSuchProcess:
        return None


def stop_observer():
    process = observer_process()
    if process is not None:
        process.terminate(); process.wait(timeout=10)
    write_json(ROOT / 'observer.json', {'status': 'stopped', 'stopped_at_utc': stamp()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check', 'watch', 'stop-observer'])
    parser.add_argument('--count', type=int, default=0)
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--instance', default='foreground')
    args = parser.parse_args()
    if args.count < 0 or args.interval < 1:
        parser.error('count must be >=0 and interval >=1')
    try:
        if args.action == 'prepare':
            prepare()
        elif args.action == 'stop-observer':
            stop_observer()
        elif args.action == 'check':
            sample = record(RuntimeProbe().sample())
            print(json.dumps(sample, ensure_ascii=False))
            return 0 if sample['status'] == 'ready' else 1
        else:
            try:
                with exclusive(ROOT / 'observer.lock'):
                    write_json(ROOT / 'observer.json', {'status': 'running', 'pid': os.getpid(),
                               'created_at': psutil.Process().create_time(), 'instance': args.instance, 'started_at_utc': stamp()})
                    try:
                        observe(RuntimeProbe(), count=args.count, interval=args.interval)
                    finally:
                        write_json(ROOT / 'observer.json', {'status': 'stopped', 'stopped_at_utc': stamp()})
            except OSError:
                print('已有后台观察或观察文件不可写；未启动重复观察。')
                return 1
    except EntryFailure as error:
        record({'status': 'blocked', 'code': error.code, 'message': error.message})
        print(error.message, file=sys.stderr)
        return 1
    except (ValueError, OSError):
        record({'status': 'blocked', 'code': 'binding_or_local_state', 'message': '获准绑定、本地状态或启动锁检查失败；未继续启动。'})
        print('获准绑定、本地状态或启动锁检查失败。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
