"""Correct CRLF browser presentation without editing the sealed admission package."""
import shutil
from scripts.prepare_sop_record_admission import OUTPUT,load_verified,render
from scripts.verify_sop_record_admission import REPORT
from scripts.prepare_legacy_projection import write,digest


def main():
    plan=load_verified()
    page=REPORT/'full-content-review.html'
    if page.exists():raise ValueError('new verified view only')
    # Windows text-mode newline translation doubles CR in original CRLF bodies.
    # Raw UTF-8 output preserves bytes; the browser normalizes CRLF to LF once.
    page.write_bytes(render(plan).encode('utf-8'))
    for name in ('admission-plan.json','receipt-template.json','compatibility.json'):
        shutil.copyfile(OUTPUT/name,REPORT/name)
        assert digest(OUTPUT/name)==digest(REPORT/name)
    write(REPORT/'presentation-correction.json',dict(
        sealed_source_package_unchanged=True,plan_and_receipt_bytes_unchanged=True,
        error='Windows text-mode output expanded CRLF into CRCRLF inside pre elements',
        correction='supplementary UTF-8 byte-written full-content review; original sealed page retained',
        authorization_scope_unchanged=True,raw_source_bytes_unchanged=True))


if __name__=='__main__':main()
