"""Real STAGED data with default server retrieval hook; paid model forbidden."""
import csv
from unittest.mock import Mock
from scripts import content_quality_release_runtime as release
from scripts.content_quality_local_server import install_retrieval
from scripts.build_original_rollback_baseline import apply_settings
from scripts.prepare_legacy_projection import write


def main():
    output=release.PROJECT/'.runtime/test/reports/content-quality-release-validation-20261011'
    output.mkdir(parents=True,exist_ok=True)
    # Explicit engineering candidate profile. Never create a publication selection
    # or claim this is an approved live entry; all calls use admin STAGED access.
    settings=release.profile('citation');apply_settings(settings)
    install_retrieval()
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.retrieval.supported_searcher import SupportedFaqSearcher
    from qa_core.security.policy import AccessScope
    guard=Mock();guard.chat.side_effect=guard.chat_stream.side_effect=AssertionError('paid model forbidden')
    uc=QaUseCase(llm=guard)
    assert uc._searcher is None
    scope=AccessScope.build(roles=['admin'])
    with (release.merge.CANDIDATE/'raw/faq_revision_01.csv').open(encoding='utf-8',newline='') as f:
        row=next(csv.DictReader(f))
    result=uc.ask(row['question'],roles=['admin'],scope=scope,kb_version=release.VERSION,persist=False,use_llm=False)
    write(output/'default-adapter-observations.json',dict(result=result,raw_answer=row['answer']))
    assert isinstance(uc.searcher,SupportedFaqSearcher)
    assert result['status']=='ANSWERED' and result['route']=='faq_exact'
    expected='【结论】依据标准排查 FAQ：\n'+row['answer']+'\n【引用】[1] faq_revision_01.csv'
    assert result['answer']==expected and len(result['sources'])==1
    events=list(uc.ask_stream(row['question'],roles=['admin'],scope=scope,kb_version=release.VERSION,persist=False))
    ends=[e['data'] for e in events if e['event']=='end']
    assert len(ends)==1 and ends[0]['status']=='ANSWERED'
    assert not guard.chat.called and not guard.chat_stream.called
    write(output/'default-adapter.json',dict(passed=True,default_constructor=True,
        actual_supported_searcher=True,real_candidate_data=True,faq_exact_full_original=True,
        stream_single_end=True,paid_calls=0,publication_selection_created=False,
        transport='in-process usecase, not live 8010 or socket/browser',independent_quality_approved=False))
    print('Default server retrieval adapter real STAGED sync/SSE passed; paid calls 0')


if __name__=='__main__':
    import torch
    torch.set_num_threads(4)
    main()
