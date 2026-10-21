"""Actual stored SOP assembly, source binding and unchanged-state checks; zero models.

This is NOT a question/answer or independent-quality evaluation. Every current
SOP is read directly, without recall, reranking, generation or embeddings.
"""
from collections import defaultdict
from datetime import datetime, timezone
import time
import uuid
import httpx
from scripts import content_quality_release_runtime as rt, readonly_workspace_runtime as readonly
from scripts.prepare_sop_device_support import OUTPUT, validate
from scripts.prepare_legacy_projection import read, write, digest, verify_seal
from scripts.verify_readonly_business_workspace import sql_snapshot
from scripts.migrate_legacy_collections import read_rows
from qa_core.quality.version_workspace import Workspace
from qa_core.retrieval.filters import _to_doc
from qa_core.retrieval.workflow import assemble_sop
from qa_core.indexing.chunkers.registry import chunk_sop
from qa_core.indexing.parsers.parsers import parse_dispatch
from qa_core.indexing.parsers.sanitize import sanitize
from scripts.rebuild_kb import normalize_text

REPORT=rt.PROJECT/'.runtime/test/reports/sop-device-support-step2-20261013'


def main():
    folder=REPORT/('attempt-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
    write(folder/'state.json',dict(state='running',paid_calls=0))
    validate();rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
    workspace=Workspace(settings)
    protected=[rt.merge.CANDIDATE,rt.merge.REPORT,rt.REVIEW,OUTPUT,
               rt.PROJECT/'.runtime/test/reports/runtime-handoff-step1-20261013']
    for p in protected:verify_seal(p)
    def snapshot():
        return dict(sql=sql_snapshot(settings),readonly_sql=sql_snapshot(readonly.profile()),
            active=workspace.snapshot(rt.VERSION),env=digest(rt.PROJECT/'.env.runtime'),
            budget=digest(rt.owned.ROOT/'generation-validation-budget.json'),
            seals={str(p.relative_to(rt.PROJECT)):digest(p/'artifact-hashes.json') for p in protected})
    before=snapshot();write(folder/'before.json',before)
    stage='stored_sop_assembly'
    try:
        with workspace.client() as client:all_rows=read_rows(client,settings.doc_collection)
        sop_rows=defaultdict(list)
        for row in all_rows:
            if row['kb_version']==rt.VERSION and row['doc_type']=='sop':sop_rows[row['source']].append(row)
        audit=read(OUTPUT/'sop-support-audit.json')
        assert set(sop_rows)=={e['filename'] for e in audit['entries']}
        checks=[]
        for entry in audit['entries']:
            rows=sop_rows[entry['filename']]
            docs=[_to_doc(dict(id=r['id'],entity=r)) for r in rows]
            result=assemble_sop(docs,docs[0])
            assert not result.ready and result.reason=='missing_safety_controls'
            assert result.packet.extra['workflow_steps']==list(range(1,11))
            assert result.packet.extra['workflow_controls']==dict(approval=False,rollback=False,stop=True)
            assert all(d.asset_id==entry['asset_id'] and d.source_version==entry['source_version'] for d in docs)
            # Stored chunks use NFKC punctuation and repeated title context.
            # Re-run only the frozen parser/chunker, without tokenizer or embedding.
            expected=chunk_sop(parse_dispatch('sop',rt.merge.CANDIDATE/'raw'/entry['filename']),source=entry['filename'])
            expected_text={c.metadata['step_index'][0]:normalize_text(sanitize(c.text)[0]) for c in expected}
            stored_text={r['step_index'][0]:r['text'] for r in rows}
            assert expected_text==stored_text
            checks.append(dict(source=entry['filename'],raw_sha256=entry['file_sha256'],
                stored_chunks=len(rows),child_chunk_ids=result.packet.extra['child_chunk_ids'],
                ready=False,reason=result.reason,controls=result.packet.extra['workflow_controls'],
                exact_frozen_chunking_replay_match=True,ordered_steps_complete=True,recall_tested=False))
        write(folder/'stored-sop-assembly.json',dict(passed=True,checks=checks,
            scope='200 actual stored full-source packets; no QA, recall, generation or independent quality'))
        stage='faq_source_binding';faq_audit=[]
        with workspace.client() as client:faq_rows=read_rows(client,settings.faq_collection)
        for entry in read(OUTPUT/'device-review-package.json')['entries']:
            source=entry['current_faq_binding']['filename']
            rows=[r for r in faq_rows if r['kb_version']==rt.VERSION and r['source']==source]
            assert len(rows)==1 and rows[0]['question']==entry['current_faq_binding']['question']
            faq_audit.append(dict(source=source,stored_row_id=rows[0]['id'],
                raw_sha256=entry['current_faq_binding']['file_sha256'],partial_triage_only=True))
        write(folder/'faq-source-bindings.json',dict(passed=True,checks=faq_audit,qa_runtime_tested=False))
        stage='runtime_health';observations=[]
        for index in range(3):
            original=rt.owned.inspect();isolated=readonly.inspect()
            assert original['status']==isolated['status']=='running'
            row=dict(at=datetime.now(timezone.utc).isoformat(),original_pid=original['pid'],readonly_pid=isolated['pid'],health=[])
            for url in ('http://127.0.0.1:8010','http://localhost:8010','http://127.0.0.1:18021'):
                response=httpx.get(url+'/health',timeout=20,trust_env=False);response.raise_for_status()
                data=response.json();assert data['status']=='ok'
                assert data['generation_enabled']==('8010' in url) and data['agent_enabled']==('18021' in url)
                row['health'].append(dict(url=url,status=data['status']))
            observations.append(row);write(folder/'health-observations.json',dict(samples=observations,scope='bounded observations, not long-term availability'))
            if index<2:time.sleep(10)
        assert len({(r['original_pid'],r['readonly_pid']) for r in observations})==1
        stage='protected_after_snapshot';after=snapshot();write(folder/'after.json',after);assert before==after
        for p in protected:verify_seal(p)
        write(folder/'state.json',dict(state='completed',stored_sops=200,faq_bindings=8,
              original_state_unchanged=True,new_admissions=0,vector_writes=0,paid_calls=0,
              independent_frozen=0,operational_sop_approved=0))
        print('Completed: actual 200 SOP packets blocked; 8 FAQ bindings; protected state unchanged; paid calls 0',flush=True)
    except Exception as exc:
        # Keep failed evidence; never print DSNs or provider credentials.
        write(folder/'state.json',dict(state='failed',paid_calls=0,stage=stage,error_type=type(exc).__name__,
              detail='inspect local attempt evidence; no raw exception persisted'))
        raise RuntimeError('SOP/device verification did not complete; no acceptance claim') from None


if __name__=='__main__':main()
