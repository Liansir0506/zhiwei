"""Seal revised source/proof alignment and live preview baseline separately from r1."""
import json
from pathlib import Path
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.prepare_sop_support_alignment import load_verified as previous_matrix
from scripts.prepare_sop_evidence_drafts import OUTPUT as ORIGINAL_DRAFTS
from scripts.prepare_sop_record_revision import OUTPUT as DRAFTS,ROOT
from scripts.build_sop_record_candidate import CANDIDATE,validate_inputs
from scripts.execute_sop_record_stage import REPORT as STAGE,RECEIPT,VERSION
from scripts.sop_record_stage_profile import profile
from qa_core.quality.sop_record_alignment import revise_alignment,validate_matrix,revision_summary
from qa_core.indexing.parsers.locations import canonical
from qa_core.schemas.evidence import evidence_of
from qa_core.retrieval.filters import _to_doc

OUTPUT=ROOT/'.runtime/test/sop-record-alignment-r2'
CODE=('qa_core/quality/sop_record_alignment.py','scripts/prepare_sop_record_alignment.py')


def source_bindings():
    return dict(candidate=digest(CANDIDATE/'artifact-hashes.json'),drafts=digest(DRAFTS/'artifact-hashes.json'),
        previous_alignment=digest(ROOT/'.runtime/test/sop-support-alignment-r1/artifact-hashes.json'),
        original_drafts=digest(ORIGINAL_DRAFTS/'artifact-hashes.json'),stage=digest(STAGE/'artifact-hashes.json'),receipt=digest(RECEIPT))


def make_matrix():
    for folder in (CANDIDATE,DRAFTS,ORIGINAL_DRAFTS,STAGE):verify_seal(folder)
    plan,_,_,_=validate_inputs(RECEIPT)
    previous={e['guide_source']:e for e in previous_matrix()['entries']}
    originals={e['filename']:e for e in read(ORIGINAL_DRAFTS/'review-package.json')['entries']}
    revisions=read(DRAFTS/'review-package.json')['entries'];chunks=read(STAGE/'stored-rows.json');entries=[]
    for e in revisions:
        filename=e['filename'];text=(CANDIDATE/'raw'/filename).read_bytes().decode('utf-8')
        entry=revise_alignment(originals[filename],e,text,previous[filename]['parent_references'])
        proofs=[r['proof'] for r in entry['records']]+entry['primary_conditions']+entry['counter_conditions']+[p for row in entry['steps'] for p in row['guide_proofs']]
        for proof in proofs:
            needle=canonical(proof['quote']);matches=[r for r in chunks if r['source']==filename and needle and needle in canonical(r['text'])]
            if len(matches)!=1:raise ValueError('one stored revised chunk for each complete literal proof required')
            ref=evidence_of(_to_doc(dict(id=matches[0]['id'],entity=matches[0])))
            proof.update(evidence_id=ref['evidence_id'],source_url=ref['source_url'],reference_match='canonical location normalization; exact source SHA and raw line retained')
        entries.append(entry)
    matrix=dict(protocol='sop-record-literal-alignment-r2',version=VERSION,entries=entries,summary=revision_summary(entries),
        publication_eligible=False,activated=False,human_semantic_approved=False,independent_denominator=0,
        ordinary_publication_gate_passed=False,new_knowledge_sources=0,new_ingestions=0,paid_calls=0)
    validate_matrix(matrix);return matrix


def build():
    if OUTPUT.exists():raise ValueError('new separate analysis only')
    matrix=make_matrix()
    from qa_core.quality.version_workspace import Workspace
    from qa_core.storage.kb_dao import KbDao
    settings=profile();version=KbDao(settings.mysql_dsn).get_version(VERSION)
    path=Path(version['quality_report_path']).resolve()
    if path!=(Path(settings.reports_dir)/('quality_'+VERSION+'.json')).resolve():raise ValueError('ordinary gate pointer changed')
    gate=read(path);snapshot=Workspace(settings).snapshot(VERSION)
    if gate['passed'] is not False or gate['snapshot']!=snapshot or gate['checks']!=read(STAGE/'gate-scope.json')['checks']:
        raise ValueError('live revised stage differs from executed ordinary gate snapshot')
    OUTPUT.mkdir(parents=True)
    write(OUTPUT/'alignment.json',matrix);write(OUTPUT/'source-bindings.json',source_bindings())
    write(OUTPUT/'preview-baseline.json',dict(target_snapshot=snapshot,ordinary_gate_sha256=digest(path)))
    write(OUTPUT/'code-bindings.json',{n:digest(ROOT/n) for n in CODE});seal(OUTPUT);load_verified()
    print(json.dumps(matrix['summary']))


def load_verified():
    verify_seal(OUTPUT)
    if read(OUTPUT/'source-bindings.json')!=source_bindings() or read(OUTPUT/'code-bindings.json')!={n:digest(ROOT/n) for n in CODE}:
        raise ValueError('revised alignment source or implementation drift')
    matrix=read(OUTPUT/'alignment.json');validate_matrix(matrix)
    if matrix!=make_matrix():raise ValueError('revised source proof or literal classification changed')
    return matrix


if __name__=='__main__':build()
