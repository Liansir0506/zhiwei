"""Candidate manual chunking: preserve complete statements and chapter identity."""
import re

from qa_core.indexing.chunkers.base import make_chunk
from qa_core.indexing.chunkers.registry import _HEADING, _heading_path


def chunk_manual_supported(text, tokenizer, *, source='', equipment_type='', vendor='', max_tokens=450):
    def fits(value):
        return len(tokenizer.encode(value,add_special_tokens=True))<=max_tokens and len(value.encode('utf-8'))<=7900
    matches=list(_HEADING.finditer(text));sections=[]
    if not matches:sections=[('',text)]
    else:
        if text[:matches[0].start()].strip():sections.append(('',text[:matches[0].start()]))
        for i,heading in enumerate(matches):
            end=matches[i+1].start() if i+1<len(matches) else len(text)
            sections.append((_heading_path(matches,i),text[heading.end():end]))
    chunks=[]
    for path,body in sections:
        atoms=[]
        for paragraph in re.split(r'\n\s*\n',body):
            paragraph=paragraph.strip()
            if not paragraph:continue
            if fits(paragraph):atoms.append(paragraph);continue
            lines=[line.strip() for line in paragraph.splitlines() if line.strip()]
            if not all(fits(line) and re.search(r'[。！？.!?][”"\']?$',line) for line in lines):
                raise ValueError('complete paragraph exceeds budget; explicit editorial split required')
            atoms.extend(lines)
        buffered=''
        for atom in atoms:
            combined=buffered+'\n'+atom if buffered else atom
            if buffered and not fits(combined):
                chunks.append(make_chunk(buffered,'manual',doc_type='manual',source=source,
                    equipment_type=equipment_type,vendor=vendor,clause=path,chapter_path=path,chunking_policy='tokenizer-paragraph-v1'))
                buffered=atom
            else:buffered=combined
        if buffered:
            chunks.append(make_chunk(buffered,'manual',doc_type='manual',source=source,
                equipment_type=equipment_type,vendor=vendor,clause=path,chapter_path=path,chunking_policy='tokenizer-paragraph-v1'))
    return chunks
