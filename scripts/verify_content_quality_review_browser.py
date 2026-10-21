"""Inspect the sealed pending content review using real offline Edge."""
import json
from pathlib import Path
import subprocess

from scripts import original_local_runtime as runtime


def main():
    root=runtime.PROJECT
    report=root/'.runtime/test/reports/evidence-quality-phase2-20261010-r2'
    original=(root/'.runtime/test/agent_domain_review_browser.js').read_text(encoding='utf-8')
    script=original.replace('19318','19329')
    script=script.replace("document.querySelector('h1')?.textContent.includes('待审核')", "document.querySelector('h1')?.textContent.includes('修订审核')")
    script=script.replace("document.querySelectorAll('details').length===9&&document.querySelectorAll('table tr').length===9", "document.querySelectorAll('details').length===20&&document.querySelectorAll('article').length===20")
    script=script.replace("document.body.textContent.includes('SIM-ALM-000001')&&document.body.textContent.includes('SIM-CHG-00001')&&document.body.textContent.includes('legacy-0916f3dedec24e1084f6907e6a65beb9')", "document.body.textContent.includes('SIM-FAQ-')&&document.body.textContent.includes('指标引用编号')&&document.body.textContent.includes('未入库或外发')")
    script=script.replace("document.body.textContent.includes('SIM-FAQ-')", "document.body.textContent.includes('原模拟来源：')")
    script=script.replace('all_9_groups_expanded','all_20_families_expanded')
    script_path=report/'content-review-browser.js'
    script_path.write_text(script,encoding='utf-8')
    edge=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    if not edge.is_file():edge=Path('C:/Program Files/Microsoft/Edge/Application/msedge.exe')
    parameters=dict(edge=str(edge),profile=str(report/'edge-review-profile'),reportDir=str(report),
        url=(root/'data/candidates/content-quality-review-r1/review.html').as_uri())
    result=subprocess.run(['node',str(script_path)],input=json.dumps(parameters),encoding='utf-8',capture_output=True,timeout=60)
    (report/'review-browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError('Review browser verification failed; inspect report log')
    print(result.stdout.strip())


if __name__=='__main__':main()
