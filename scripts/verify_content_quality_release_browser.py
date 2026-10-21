"""Real offline Edge rendering of concrete publication/outbound scope."""
import json
from pathlib import Path
import subprocess
from scripts import content_quality_release_runtime as release


def main():
    release.validate_review()
    output=release.PROJECT/'.runtime/test/reports/content-quality-release-validation-20261011'
    template=(release.PROJECT/'.runtime/test/agent_domain_review_browser.js').read_text(encoding='utf-8')
    script=template.replace('19318','19333')
    script=script.replace("document.querySelector('h1')?.textContent.includes('待审核')",
        "document.querySelector('h1')?.textContent.includes('发布审核')")
    script=script.replace("document.querySelectorAll('details').length===9&&document.querySelectorAll('table tr').length===9",
        "document.querySelectorAll('details').length===28&&document.querySelectorAll('article').length===28")
    script=script.replace("document.body.textContent.includes('SIM-ALM-000001')&&document.body.textContent.includes('SIM-CHG-00001')&&document.body.textContent.includes('legacy-0916f3dedec24e1084f6907e6a65beb9')",
        "document.body.textContent.includes('faq_revision_01.csv')&&document.body.textContent.includes('faq_triage_08.csv')&&document.body.textContent.includes('回退')&&document.body.textContent.includes('api.deepseek.com/v1/chat/completions')")
    script=script.replace('all_9_groups_expanded','all_28_bodies_expanded')
    path=output/'release-review-browser.js';path.write_text(script,encoding='utf-8')
    edge=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    if not edge.is_file():edge=Path('C:/Program Files/Microsoft/Edge/Application/msedge.exe')
    params=dict(edge=str(edge),profile=str(output/'edge-profile'),reportDir=str(output),url=(release.REVIEW/'review.html').as_uri())
    result=subprocess.run(['node',str(path)],input=json.dumps(params),encoding='utf-8',capture_output=True,timeout=60)
    (output/'review-browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError('release review browser verification failed')
    print(result.stdout.strip())


if __name__=='__main__':main()
