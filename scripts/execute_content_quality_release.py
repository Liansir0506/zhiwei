"""Approved independent-collection publication with full profile restoration."""
import argparse
import json
import secrets
import subprocess
from pathlib import Path
import httpx
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts import content_quality_release_runtime as release
from scripts.prepare_legacy_projection import read,write,digest


def transition(operations):
    """Recover even when stop, activation, launcher or live checks fail."""
    operations.preflight()
    try:
        operations.install()
        operations.start()
        operations.verify_live()
    except BaseException as original:
        try:
            operations.restore()
            operations.verify_restored()
        except BaseException as recovery:
            raise RuntimeError('publication failed and recovery could not be verified; preserve owned journal') from recovery
        raise


class Operations:
    def __init__(self,receipt_path):
        self.receipt_path=Path(receipt_path).resolve();self.receipt=read(self.receipt_path)
        self.engine=sa.create_engine(release.BASE.mysql_dsn,pool_pre_ping=True)
        self.key=secrets.token_urlsafe(32);self.actor='content-quality-release-publisher'
        self.key_created=False;self.changed=False;self.started_install=False

    def active(self):
        with self.engine.connect() as conn:
            rows=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
        if len(rows)!=1:raise ValueError('requires unique ACTIVE')
        return rows[0]

    def preflight(self):
        plan=release.validate_review()
        release.validate_receipt(self.receipt,plan,digest(release.REVIEW/'release-plan.json'))
        if release.SELECTION.exists():raise ValueError('selection exists; no replay')
        release.install_adapter()
        from qa_core.quality.publication import verify_publication
        if self.active()!=release.OLD or release.owned.inspect()['status']!='running':
            raise ValueError('old healthy owned entry required')
        with self.engine.connect() as conn:
            candidate=conn.execute(sa.text('SELECT status,quality_report_path FROM kb_versions WHERE version=:v'),dict(v=release.VERSION)).one()
            previous=conn.execute(sa.text('SELECT quality_report_path FROM kb_versions WHERE version=:v'),dict(v=release.OLD)).scalar_one()
        if candidate[0]!='STAGED':raise ValueError('candidate must remain STAGED')
        verify_publication(release.profile(self.receipt['mode']),release.VERSION,candidate[1])
        verify_publication(release.BASE,release.OLD,previous)

    def call(self,action,version,settings):
        release.owned.apply_profile(settings)
        from qa_core.api import route_kb
        app=FastAPI();app.include_router(route_kb.router)
        with TestClient(app) as http:
            response=http.post('/kb/'+action,headers={'X-API-Key':self.key},json=dict(version=version))
            response.raise_for_status()

    def install(self):
        with release.owned.command_lock(self.engine):
            # Repeat preflight under the lifecycle lock immediately before stop.
            self.preflight()
            release.owned.apply_profile(release.BASE)
            from qa_core.api.auth import create_api_key
            create_api_key(self.key,self.actor,['admin'],tenant_id='default');self.key_created=True
            self.started_install=True
            self.journal('stopping_previous')
            release.owned.stop(self.engine)
            write(release.SELECTION,self.receipt)
            release.install_adapter()
            self.call('activate',release.VERSION,release.selected_settings())
            self.changed=True
            self.journal('candidate_active_start_pending')

    def journal(self,phase):
        write(release.owned.ROOT/'content-quality-release-journal.json',dict(
            phase=phase,previous_active=release.OLD,candidate=release.VERSION,
            receipt_sha256=digest(self.receipt_path),mode=self.receipt['mode'],
            paid_validation_calls=0))

    def start(self):
        # Launcher takes its own lifecycle lock; never wait while holding ours.
        path=release.PROJECT/'scripts/start_content_quality_background.ps1'
        result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(path)],
            cwd=release.PROJECT,capture_output=True,text=True,timeout=210,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise RuntimeError('owned hidden launcher failed; inspect runtime state')

    def verify_live(self):
        release.install_adapter();settings=release.selected_settings()
        assert self.active()==release.VERSION
        status=release.owned.inspect();assert status['status']=='running'
        state=release.owned.load_state();ops_key=release.owned.read_key(state)
        from pymilvus import MilvusClient
        from scripts.migrate_legacy_collections import read_rows
        from qa_core.retrieval.filters import _to_doc
        from qa_core.schemas.evidence import evidence_of
        client=MilvusClient(uri=settings.milvus_uri,timeout=120)
        try:rows=read_rows(client,settings.faq_collection)
        finally:client.close()
        assert len(rows)==31
        with httpx.Client(base_url=state['url'],headers={'X-API-Key':ops_key},trust_env=False,timeout=30) as live:
            identity=live.get('/local/runtime');identity.raise_for_status()
            assert identity.json()['generation_enabled']==(self.receipt['mode']=='generation')
            catalog=live.get('/qa/recommendations');catalog.raise_for_status()
            assert catalog.json()['kb_version']==release.VERSION and catalog.json()['items']
            for row in rows:
                ref=evidence_of(_to_doc(dict(id=row['id'],entity=row)),'faq')
                result=live.get(ref['source_url']);result.raise_for_status()
                assert result.json()['text']==row['answer']
        # No QA POST: paid generation cannot be accidentally triggered by smoke checks.
        write(release.owned.ROOT/'content-quality-release-result.json',dict(passed=True,version=release.VERSION,
            mode=self.receipt['mode'],previous_active=release.OLD,receipt_sha256=digest(self.receipt_path),
            live_ops_source_checks=31,live_recommendations=True,paid_validation_calls=0,
            generation_question_validation_performed=False,independent_quality_approved=False))
        self.journal('live_read_checks_passed')

    def restore(self):
        if not self.started_install:return
        with release.owned.command_lock(self.engine):
            current=self.active()
            if current not in (release.OLD,release.VERSION):raise ValueError('unexpected ACTIVE; preserve recovery state')
            release.install_adapter()
            release.owned.stop(self.engine)
            self.journal('restoring_previous')
            release.SELECTION.unlink(missing_ok=True)
            release.install_adapter()
            try:
                if current==release.VERSION:self.call('rollback',release.OLD,release.BASE)
            except BaseException:
                # A failed HTTP response may follow a committed transaction.
                # Preserve the profile matching the actual unique ACTIVE.
                if self.active()==release.VERSION:write(release.SELECTION,self.receipt)
                release.install_adapter()
                self.journal('rollback_failed_manual_recovery_required')
                raise
        self.start()

    def verify_restored(self):
        if not self.started_install:return
        release.install_adapter()
        assert not release.SELECTION.exists() and self.active()==release.OLD
        status=release.owned.inspect();assert status['status']=='running'
        release.owned.verify_active(release.BASE,self.engine)
        write(release.owned.ROOT/'content-quality-release-recovery.json',dict(passed=True,
            restored_active=release.OLD,restored_original_collections=True,restored_original_generation=release.BASE.generation_enabled,
            failed_activation_replayed=False,paid_validation_calls=0))
        self.journal('previous_restoration_verified')

    def close(self):
        try:
            if self.key_created:
                from qa_core.api.auth import hash_key
                with self.engine.begin() as conn:
                    conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),dict(h=hash_key(self.key),u=self.actor))
        finally:self.engine.dispose()


def execute(receipt_path):
    operations=Operations(receipt_path)
    try:transition(operations)
    finally:operations.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--approval-receipt',required=True)
    execute(parser.parse_args().approval_receipt)
