"""Build a separate analysis artifact from admitted guide bytes and existing parent bindings."""
import json
from pathlib import Path
from qa_core.quality.sop_support_alignment import align_guide,validate_matrix,summarize
from qa_core.quality.support_review import verify_quote,source_path
from qa_core.schemas.evidence import evidence_of
from qa_core.retrieval.filters import _to_doc
from qa_core.indexing.parsers.locations import canonical
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.prepare_sop_evidence_drafts import OUTPUT as DRAFTS,ROOT
from scripts.prepare_sop_device_support import OUTPUT as PARENT
from scripts.build_sop_evidence_candidate import CANDIDATE,validate_inputs
from scripts.execute_sop_evidence_stage import REPORT as STAGE,RECEIPT
from scripts import stage_content_quality_merge as current

OUTPUT=ROOT/'.runtime/test/sop-support-alignment-r1'


def validate_sources():
    verify_seal(CANDIDATE);verify_seal(STAGE);verify_seal(DRAFTS);verify_seal(PARENT)
    plan,_,_,_=validate_inputs(RECEIPT)
    return plan


def build():
    plan=validate_sources()
    if OUTPUT.exists():raise ValueError('new separate alignment artifact only')
    package=read(DRAFTS/'review-package.json')
    audit=read(PARENT/'sop-support-audit.json');parents={e['filename']:e for e in audit['entries']}
    approved={e['filename']:e for e in plan['entries']}
    chunks=read(STAGE/'stored-rows.json');entries=[]
    for guide in package['entries']:
        entry=align_guide(guide,(CANDIDATE/'raw'/guide['filename']).read_bytes().decode('utf-8'))
        entry['parent_references']=[]
        for parent in approved[guide['filename']]['original_parent_bindings']:
            original=parents[parent['filename']];path=source_path(current.CANDIDATE/'raw',parent['filename'])
            if digest(path)!=parent['file_sha256'] or original['asset_id']!=parent['asset_id'] or not original['recognized_template']:
                raise ValueError('existing parent source binding drift')
            for finding in original['findings']:
                evidence=finding['evidence']
                if 'quote' in evidence:verify_quote(path,evidence)
            # Return source identities/locations only, never parent body into guide namespace.
            entry['parent_references'].append(dict(filename=parent['filename'],asset_id=parent['asset_id'],
                file_sha256=parent['file_sha256'],metadata_sha256=parent['metadata_sha256'],
                step_lines={str(i):n for n,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1)
                            for i in range(1,11) if line.startswith(f'步骤 {i}：')}))
        proofs=[r['proof'] for r in entry['records']]+entry['primary_conditions']+entry['counter_conditions']+[p for row in entry['steps'] for p in row['guide_proofs']]
        for proof in proofs:
            needle=canonical(proof['quote'])
            matches=[r for r in chunks if r['source']==proof['source'] and needle and needle in canonical(r['text'])]
            if len(matches)!=1:raise ValueError('one real guide chunk for exact proof required')
            reference=evidence_of(_to_doc({'id':matches[0]['id'],'entity':matches[0]}))
            proof['evidence_id']=reference['evidence_id'];proof['source_url']=reference['source_url']
            proof['reference_match']='existing_source_location_normalization; raw line and file SHA retained'
        entries.append(entry)
    matrix=dict(protocol='sop-guide-literal-alignment-r1',entries=entries,summary=summarize(entries),
        publication_eligible=False,activated=False,human_semantic_approved=False,independent_denominator=0,
        ordinary_publication_gate_passed=False,new_knowledge_sources=0,new_ingestions=0,paid_calls=0,
        note='Text alignment only; 20 repeated template families do not provide field evidence or human truth.')
    validate_matrix(matrix)
    OUTPUT.mkdir(parents=True)
    write(OUTPUT/'alignment.json',matrix)
    write(OUTPUT/'source-bindings.json',dict(candidate=digest(CANDIDATE/'artifact-hashes.json'),drafts=digest(DRAFTS/'artifact-hashes.json'),
        parent_review=digest(PARENT/'artifact-hashes.json'),stage=digest(STAGE/'artifact-hashes.json'),receipt=digest(RECEIPT)))
    write(OUTPUT/'code-bindings.json',{n:digest(ROOT/n) for n in ('qa_core/quality/sop_support_alignment.py','scripts/prepare_sop_support_alignment.py')})
    seal(OUTPUT);verify_seal(OUTPUT)
    print(json.dumps(matrix['summary']))


def load_verified():
    verify_seal(OUTPUT);validate_sources()
    for name,expected in read(OUTPUT/'code-bindings.json').items():
        if digest(ROOT/name)!=expected:raise ValueError('alignment implementation drift')
    matrix=read(OUTPUT/'alignment.json');validate_matrix(matrix)
    bindings=read(OUTPUT/'source-bindings.json')
    actual=dict(candidate=digest(CANDIDATE/'artifact-hashes.json'),drafts=digest(DRAFTS/'artifact-hashes.json'),
        parent_review=digest(PARENT/'artifact-hashes.json'),stage=digest(STAGE/'artifact-hashes.json'),receipt=digest(RECEIPT))
    if actual!=bindings:raise ValueError('analysis no longer matches admitted guides')
    for entry in matrix['entries']:
        path=CANDIDATE/'raw'/entry['guide_source']
        if digest(path)!=entry['guide_sha256']:raise ValueError('guide source bytes drift')
        for parent in entry['parent_references']:
            if digest(source_path(current.CANDIDATE/'raw',parent['filename']))!=parent['file_sha256']:
                raise ValueError('parent source bytes drift')
    return matrix


if __name__=='__main__':build()
