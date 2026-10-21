"""Publish only an explicitly approved merged demo, with verified rollback."""
import argparse
import secrets
import sqlalchemy as sa
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scripts import original_local_runtime as runtime
from scripts.merged_release_runtime import REVIEW,SELECTION,VERSION,validate_selection
from scripts.prepare_legacy_projection import read,write,digest,verify_seal
from scripts.prepare_merged_protocol_release import settings_for,OLD,REPORT


def execute(receipt_path):
    verify_seal(REVIEW)
    plan=read(REVIEW/'release-plan.json');receipt=read(receipt_path)
    validate_selection(receipt,plan,digest(REVIEW/'release-plan.json'))
    if SELECTION.exists():raise ValueError('existing selection; inspect active/runtime before any replay')
    previous=runtime.build_profile();engine=sa.create_engine(previous.mysql_dsn)
    from qa_core.quality.publication import verify_publication
    from qa_core.api.auth import create_api_key,hash_key
    from qa_core.api import route_kb
    key=secrets.token_urlsafe(32);actor='merged-release-publisher';changed=False
    try:
        with runtime.command_lock(engine):
            assert runtime.inspect()['status']=='running'
            with engine.connect() as conn:
                assert conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()==[OLD]
                candidate=conn.execute(sa.text('SELECT status,quality_report_path FROM kb_versions WHERE version=:v'),dict(v=VERSION)).one()
                assert candidate[0]=='STAGED'
            preview=settings_for()
            verify_publication(preview,VERSION,candidate[1])
            verify_publication(preview,OLD,previous.reports_dir+'/quality_'+OLD+'.json')
            runtime.owned_process(runtime.load_state())
            # Selection is written only after exact user approval and preflight.
            write(SELECTION,receipt)
            selected=runtime.build_profile();runtime.apply_profile(selected)
            create_api_key(key,actor,['admin'],tenant_id='default')
            app=FastAPI();app.include_router(route_kb.router)
            with TestClient(app) as http:
                response=http.post('/kb/activate',headers={'X-API-Key':key},json={'version':VERSION})
                response.raise_for_status();changed=True
                try:
                    result=runtime.restart(selected,engine,previous_profile=runtime.public_profile(previous))
                    with engine.connect() as conn:
                        assert conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()==[VERSION]
                    from qa_core.schemas.evidence import evidence_of
                    from qa_core.retrieval.filters import _to_doc
                    expected=read(REPORT/'expected-rows.json');positions=read(REPORT/'expected-locations.json')
                    state=runtime.load_state()
                    with httpx.Client(base_url=state['url'],trust_env=False,timeout=30,
                                      headers={'X-API-Key':runtime.read_key(state)}) as live:
                        assert [v['version'] for v in live.get('/kb/versions').json()['versions'] if v['status']=='ACTIVE']==[VERSION]
                        for number in (1,2,3):
                            row=next(r for r in expected.values() if r['source']=='faq_protocol_%02d.csv'%number)
                            doc=_to_doc(dict(id=row['id'],entity=row));doc.extra['source_location']=positions[row['id']]
                            ref=evidence_of(doc,'faq')
                            response=live.get(ref['source_url']);response.raise_for_status()
                            assert response.json()['text']==row['answer']
                    write(runtime.ROOT/'merged-release-result.json',dict(passed=True,version=VERSION,previous_active=OLD,
                        approval_sha256=digest(receipt_path),runtime=result,live_ops_source_checks=3,paid_validation_calls=0))
                except BaseException:
                    rollback=http.post('/kb/rollback',headers={'X-API-Key':key},json={'version':OLD})
                    rollback.raise_for_status()
                    state=runtime.load_state()
                    if state.get('status')=='stopped':runtime.launch(selected,engine)
                    else:runtime.restart(selected,engine,previous_profile=read(runtime.ROOT/'profile.json'))
                    raise
    finally:
        with engine.begin() as conn:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),dict(h=hash_key(key),u=actor))
        if not changed and SELECTION.exists():SELECTION.unlink()
        engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--approval-receipt',required=True)
    execute(parser.parse_args().approval_receipt)
