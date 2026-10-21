"""Read-only candidate QA integration of the next subject-filter import."""
from unittest.mock import Mock, patch
import argparse
from pathlib import Path
import copy
import json

from scripts import original_local_runtime as runtime
from scripts.build_original_rollback_baseline import apply_settings
from scripts.prepare_legacy_projection import read,write,verify_seal
from scripts import stage_triage_faq_demo as triage
from scripts import stage_protocol_faq_demo as protocol
from qa_core.retrieval.supported_evidence import relevant_docs
from qa_core.security.policy import AccessScope

ROOT=runtime.PROJECT
REPORT=ROOT/'.runtime/test/reports/evidence-quality-phase2-20261010'


def profile(base, *, candidate=False):
    changes=dict(generation_enabled=False,agent_enabled=False,llm_api_key='',llm_base_url='http://127.0.0.1:1/disabled',
        semantic_cache_enabled=False,redis_url='redis://127.0.0.1:6379/14',reports_dir=str(REPORT/'unused'))
    if candidate:changes.update(doc_collection=triage.DOC,faq_collection=triage.FAQ,data_dir=str(triage.CANDIDATE))
    settings=base.model_copy(update=changes);apply_settings(settings);return settings


def execute():
    if REPORT.exists():raise ValueError('new report required; preserve failed attempts')
    REPORT.mkdir(parents=True)
    base=runtime.build_profile()
    for source in [triage.CANDIDATE,triage.REPORT,protocol.REVIEW]:verify_seal(source)
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.retrieval.searcher import Searcher
    from qa_core.application.evidence_service import create_evidence_service
    guard=Mock();guard.chat.side_effect=AssertionError('paid generation forbidden');guard.chat_stream.side_effect=AssertionError('paid generation forbidden')
    scope=AccessScope.build(roles=['admin']);observations=[]
    cases=read(triage.REVIEW/'review-cases.json')['cases']
    bodies={e['id']:e for e in read(triage.CANDIDATE/'approved-bodies.json')['entries']}
    settings=profile(base,candidate=True)
    uc=QaUseCase(searcher=Searcher(settings=settings),llm=guard);evidence=create_evidence_service(settings)
    with patch('qa_core.application.qa_usecase.relevant_docs',relevant_docs):
        for case in cases:
            result=uc.ask(case['query'],roles=['admin'],scope=scope,kb_version=triage.VERSION,use_llm=False,persist=False)
            expected=bodies[case['candidate_id']]['source_identity_proposal']['source_id']
            actual=[s.get('source_id') for s in result.get('sources',[])]
            observations.append(dict(id=case['id'],expected_source=expected,result=result))
            write(REPORT/'triage-observations.json',observations)
            assert result['status']=='ANSWERED' and actual==[expected],(case['id'],result['status'],actual)
            original=evidence.source(result['sources'][0]['evidence_id'],scope)
            assert original['text']==bodies[case['candidate_id']]['answer']
            print('triage',case['id'],'single source',flush=True)
        streamed=[]
        for case in [c for c in cases if c['id'].endswith('boundary')]:
            events=list(uc.ask_stream(case['query'],roles=['admin'],scope=scope,kb_version=triage.VERSION,persist=False))
            source_frames=[e['data'] for e in events if e['event']=='sources']
            end=[e['data'] for e in events if e['event']=='end']
            expected=bodies[case['candidate_id']]['source_identity_proposal']['source_id']
            assert len(end)==1 and end[0]['status']=='ANSWERED'
            assert [s['source_id'] for s in source_frames[-1]['sources']]==[expected]
            streamed.append(dict(id=case['id'],events=events))
        write(REPORT/'triage-streams.json',streamed)
        settings=profile(base);uc=QaUseCase(searcher=Searcher(settings=settings),llm=guard)
        merged=read(ROOT/'.runtime/test/reports/merged-protocol-release-20261010/expected-rows.json')
        protocol_cases=read(protocol.REVIEW/'review-cases.json')['cases']
        regressions=[]
        for i,case in enumerate(protocol_cases):
            result=uc.ask(case['query'],roles=['admin'],scope=scope,kb_version='local-merged-r2',use_llm=False,persist=False)
            expected='faq_protocol_%02d.csv'%(1 if i<4 else 2 if i<8 else 3)
            regressions.append(dict(id=case['id'],expected=expected,result=result));write(REPORT/'protocol-regression.json',regressions)
            assert result['status']=='ANSWERED' and [s['source'] for s in result['sources']]==[expected],case['id']
            print('protocol',case['id'],'single source',flush=True)
        for row in [r for r in merged.values() if r['doc_type']=='faq' and not r['source'].startswith('faq_protocol_')][:3]:
            result=uc.ask(row['question'],roles=['admin'],scope=scope,kb_version='local-merged-r2',use_llm=False,persist=False)
            assert result['route']=='faq_exact' and result['status']=='ANSWERED'
            regressions.append(dict(id='old-'+row['id'],result=result))
        write(REPORT/'protocol-regression.json',regressions)
    assert not guard.chat.called and not guard.chat_stream.called
    write(REPORT/'qa-summary.json',dict(passed=True,triage_sync=16,triage_streams=8,protocol_regressions=12,old_exact=3,
        integration='candidate process uses proposed import via scoped patch; released code unchanged',
        persist=False,paid_calls=0,semantic_labels='pending',independent_quality_passed=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=REPORT)
    args=parser.parse_args();REPORT=args.output.resolve();execute()
