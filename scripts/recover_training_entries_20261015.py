"""Recover existing approved local entries, retaining sanitized lifecycle evidence."""
from datetime import datetime, timezone
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import traceback

PROJECT = Path(__file__).resolve().parents[1]
REPORT = PROJECT / '.runtime/test/reports/entry-recovery-20261015'
SERVICES = ('mysql', 'redis', 'etcd', 'minio', 'milvus')
RUN = subprocess.run


def hidden_run(*args, **kwargs):
    kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
    return RUN(*args, **kwargs)


def write(name, value):
    if (REPORT/'artifact-hashes.json').exists():
        raise ValueError('Sealed recovery evidence must not be overwritten')
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding='utf-8')


def event(stage, **extra):
    if (REPORT/'artifact-hashes.json').exists():
        raise ValueError('Sealed recovery evidence must not be appended')
    row = dict(stage=stage, at_utc=datetime.now(timezone.utc).isoformat(), **extra)
    with (REPORT / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=True) + '\n')
    print(json.dumps(row, ensure_ascii=True), flush=True)


def fingerprints():
    names = ('.env.runtime', '.runtime/original-local/profile.json',
             '.runtime/original-local/content-quality-release-selection.json',
             '.runtime/original-local/generation-validation-budget.json')
    return {name: hashlib.sha256((PROJECT/name).read_bytes()).hexdigest()
            for name in names if (PROJECT/name).is_file()}


def containers():
    names = [f'{prefix}-{service}-1' for prefix in ('telecom-ops-rag', 'telecom-ops-rag-test')
             for service in SERVICES]
    result = hidden_run(['docker', 'inspect', *names], capture_output=True, text=True,
                        encoding='utf-8', timeout=20, check=True)
    rows = []
    for item in json.loads(result.stdout):
        labels = item['Config']['Labels']
        state = item['State']
        rows.append(dict(name=item['Name'].lstrip('/'), id=item['Id'],
            project=labels.get('com.docker.compose.project'), service=labels.get('com.docker.compose.service'),
            status=state['Status'], health=state.get('Health', {}).get('Status'),
            exit_code=state['ExitCode'], oom_killed=state['OOMKilled'], finished_at=state['FinishedAt'],
            mounts_sha256=hashlib.sha256(json.dumps(item['Mounts'], sort_keys=True).encode()).hexdigest()))
    return rows


def verify_container_identity(before, after):
    matches = []
    for previous, current in zip(before, after, strict=True):
        assert previous['id'] == current['id'] and previous['name'] == current['name']
        # Docker may reorder the Mounts array across starts. Prove the original
        # digest matches an ordering of the same mount objects; never rewrite it.
        result = hidden_run(['docker','inspect','--format','{{json .Mounts}}',current['name']],
                            capture_output=True,text=True,encoding='utf-8',check=True,timeout=10)
        mounts = json.loads(result.stdout)
        assert len(mounts) <= 5
        hashes = {hashlib.sha256(json.dumps(list(order),sort_keys=True).encode()).hexdigest()
                  for order in itertools.permutations(mounts)}
        assert previous['mounts_sha256'] in hashes and current['mounts_sha256'] in hashes
        matches.append(dict(name=current['name'],same_id=True,same_mount_objects=True,
                            order_changed=previous['mounts_sha256']!=current['mounts_sha256']))
    write('container-identity-verification.json',matches)
    return matches


def main(resume=False):
    if (REPORT/'before.json').exists() and not resume:
        raise ValueError('Recovery report already exists; inspect it before another attempt')
    REPORT.mkdir(parents=True, exist_ok=True)
    if resume:
        before = json.loads((REPORT/'before.json').read_text('utf-8'))
        if before['files'] != fingerprints(): raise ValueError('Protected files changed')
    else:
        before = dict(files=fingerprints(), containers=containers())
        write('before.json', before)
    # Existing controllers also invoke Docker; hide every inherited console child.
    subprocess.run = hidden_run
    stage = 'original_unified_entry'
    try:
        event(stage)
        result = hidden_run(['powershell.exe', '-NoProfile', '-File', str(PROJECT/'scripts/start_platform.ps1')],
                            cwd=PROJECT, capture_output=True, timeout=650)
        write('original-start.json', dict(returncode=result.returncode))
        if result.returncode:
            raise RuntimeError('Original startup failed; private controller diagnostics retained')
        event(stage, status='ready')
        stage = 'isolated_storage'
        event(stage)
        from scripts.platform_entry import Dependencies
        Dependencies().local_engine()
        from scripts import readonly_workspace_runtime as business_core
        business_core.prepare_storage()
        event(stage, status='ready')
        stage = 'business_workspace'
        event(stage)
        from scripts import training_business_runtime as business
        with business.core.lifecycle_lock():
            result = business.start()
        write('business-start.json', result)
        if result['status'] != 'running': raise RuntimeError('Business not ready')
        event(stage, status='ready')
        for stage, module in [('guide_r1', 'sop_guide_support_runtime'), ('guide_r2', 'sop_record_topic_runtime')]:
            event(stage)
            from scripts.preview_recovery_runtime import runtime_for
            runtime = runtime_for('guide-r1' if stage == 'guide_r1' else 'guide-r2')
            current = runtime.status()
            if current['status'] != 'running':
                runtime.stop()
            result = runtime.start()
            write(stage+'-start.json', result)
            if result['status'] != 'running': raise RuntimeError('Guide not ready')
            event(stage, status='ready')
        after = dict(files=fingerprints(), containers=containers())
        write('after-start.json', after)
        assert before['files'] == after['files'], 'Protected configuration or paid-call budget changed'
        verify_container_identity(before['containers'],after['containers'])
        event('completed', protected_files_unchanged=True, same_containers_and_mounts=True, paid_calls=0)
    except Exception as error:
        frames = [dict(file=Path(f.filename).name, line=f.lineno, function=f.name)
                  for f in traceback.extract_tb(error.__traceback__)]
        write('failure.json', dict(stage=stage, error_type=type(error).__name__, frames=frames))
        event(stage, status='failed', error_type=type(error).__name__)
        raise SystemExit(1) from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true')
    main(parser.parse_args().resume)
