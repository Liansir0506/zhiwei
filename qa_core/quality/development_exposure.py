"""Conservatively add sealed later development questions to a public hash index."""
from copy import deepcopy
import re
from pathlib import Path

from scripts.prepare_legacy_projection import read, verify_seal, digest

HEX=re.compile(r'[a-f0-9]{64}')


def include_overlays(registry,root,project,preparation_seal):
    root,project=Path(root).absolute(),Path(project).absolute()
    if root.resolve()!=root or project.resolve()!=project or not root.is_relative_to(project):
        raise ValueError('development overlay root must remain in workspace')
    merged=deepcopy(registry);raw=set(merged['raw_query_hashes']);canonical=set(merged['canonical_query_hashes'])
    templates=set(merged['blocking_link_index']['template_groups']);receipts=[]
    if not root.exists():return merged,receipts
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.resolve()!=folder or not folder.resolve().is_relative_to(root):
            raise ValueError('unexpected overlay file or link')
        verify_seal(folder)
        if (folder/'exposure.json').stat().st_size>16*1024*1024:raise ValueError('overlay too large')
        overlay=read(folder/'exposure.json')
        if (overlay.get('protocol')!='development-exposure-overlay-v1' or
            overlay.get('all_development_exposed') is not True or overlay.get('human_gold') is not False or
            overlay.get('independent_cases')!=0 or overlay.get('source_holdout_preparation_seal')!=preparation_seal):
            raise ValueError('overlay scope/preparation changed')
        dataset=Path(overlay['dataset_path'])
        if not dataset.is_absolute() or dataset.resolve()!=dataset or not dataset.is_relative_to(project) or digest(dataset)!=overlay['dataset_sha256']:
            raise ValueError('public development dataset drift')
        records=overlay['records']
        if not isinstance(records,list) or not records or len(records)>10000:raise ValueError('invalid overlay roster')
        for row in records:
            if row.get('developer_exposed') is not True or any(k in row for k in ('query','question','answer','truth')):
                raise ValueError('overlay must contain exposed hash commitments only')
            for field in ('query_sha256','canonical_query_sha256'):
                if not isinstance(row.get(field),str) or not HEX.fullmatch(row[field]):raise ValueError('invalid question hash')
            group=row.get('group_links',{}).get('template_groups')
            if not isinstance(group,list) or any(not isinstance(t,str) or not t or len(t)>300 for t in group):
                raise ValueError('invalid template exposure links')
            raw.add(row['query_sha256']);canonical.add(row['canonical_query_sha256']);templates.update(group)
        receipts.append(dict(path=str(folder),seal_sha256=digest(folder/'artifact-hashes.json'),records=len(records)))
    merged['raw_query_hashes']=sorted(raw);merged['canonical_query_hashes']=sorted(canonical)
    merged['blocking_link_index']['template_groups']=sorted(templates)
    return merged,receipts
