"""Actual hash-only CLI controls from public later exposure; never human gold."""
import json
import uuid

from scripts.prepare_question_holdout import ROOT,OUTPUT,PREVIOUS,VERSION
from scripts.prepare_legacy_projection import read,write,digest
from scripts.screen_question_holdout_v2 import run
from qa_core.quality.question_holdout import template
from qa_core.quality.independent_readiness import sha

REPORT=ROOT/'.runtime/test/reports/development-selfcheck-20261013'


def main():
    before=read(PREVIOUS/'exposure-registry.json')
    records=[]
    for p in (ROOT/'.runtime/quality/question-intake/development-exposure').glob('*/exposure.json'):
        records.extend(read(p)['records'])
    fresh=next(r for r in records if r['query_sha256'] not in before['raw_query_hashes']
        and r['canonical_query_sha256'] not in before['canonical_query_hashes']
        and r['group_links']['template_groups']
        and not set(r['group_links']['template_groups'])&set(before['blocking_link_index']['template_groups']))
    binding=read(OUTPUT/'baseline-binding.json')['binding_sha256'];source=read(OUTPUT/'source-roster.json')[0]
    folder=REPORT/'exposure-controls';folder.mkdir(exist_ok=False);results=[]
    for field,value,reason in [('query_sha256',fresh['query_sha256'],'known_exposed_question_hash'),
        ('canonical_query_sha256',fresh['canonical_query_sha256'],'known_exposed_question_hash'),
        ('template_groups',fresh['group_links']['template_groups'],'known_exposed_question_template')]:
        p=template(VERSION,binding)
        control=dict(id='development_metadata_control',query_sha256=sha(uuid.uuid4().hex),
            canonical_query_sha256=sha(uuid.uuid4().hex),truth_sha256=sha('development control only; no human truth'),
            evidence_manifest_sha256=sha('development control only; no human evidence'),
            source_ids=[source['asset_id']],body_families=[source['canonical_body_sha256']],
            topic_groups=['known-source-control'],template_groups=['new-control-'+uuid.uuid4().hex],
            development_exposed=False,expected_kind='answer',request_context=dict(roles=['ops'],tenant_id='default'))
        control[field]=value;p['cases']=[control]
        path=folder/(field+'.json');write(path,p)
        receipt=run(path);result=read(receipt['report'])
        assert reason in result['blocked_cases'][0]['reasons']
        assert result['quality_metrics'] is None and not result['run_authorized']
        results.append(dict(field=field,passed=True,reason=reason,absent_from_prior_registry=True,
            screen_report=receipt['report'],frozen=receipt['frozen']))
    empty=run();assert empty['state']=='blocked' and empty['case_count']==0
    write(folder/'verification.json',dict(passed=True,controls=results,empty_intake=empty,
        synthetic_metadata_controls=True,human_gold=False,independent_cases=0,
        preparation_seal_sha256=digest(OUTPUT/'artifact-hashes.json')))
    print(json.dumps(dict(passed=True,metadata_controls=len(results),empty_intake_blocked=True,independent_cases=0)))


if __name__=='__main__':main()
