"""Strict pending review contract, separate from the sealed draft preparer."""
from scripts.prepare_sop_record_revision import validate_package,revised_body


def validate_revision(package,raw,original_raw):
    counts=validate_package(package,raw)
    from pathlib import Path
    for e in package['entries']:
        if e.get('license_status')!='pending' or any(e.get(k) is not False for k in (
            'identity_registered','operational_ready','root_cause_confirmed','action_authorized','full_sop_approved')):
            raise ValueError('pending revision grants no admission, action or truth')
        if e['access_proposal']!=dict(roles=['ops','admin'],security_level='internal',tenant_id='default',visibility='internal'):
            raise ValueError('exact internal default ops/admin proposal required')
        text=(Path(raw)/e['filename']).read_bytes().decode('utf-8')
        original=(Path(original_raw)/e['filename']).read_bytes().decode('utf-8')
        if text!=revised_body(original,e['extensions']):raise ValueError('only known extensions may be appended')
        for x in e['extensions']:
            if x.get('action_authorized') is not False or x.get('site_evidence_verified') is not False or x.get('value') is not None:
                raise ValueError('no observed value or authorization supplied')
            if len(set(x['requested_fields']))!=len(x['requested_fields']) or not x['boundary'] or x['boundary'] not in x['description']:
                raise ValueError('specific fields and applicable boundary required')
    return counts
