"""Read-only screenshots after a cosmetic empty-panel correction; no new diagnosis."""
import json
import subprocess
import sys
from scripts import readonly_workspace_runtime as rt
from scripts.prepare_legacy_projection import read

def main():
    report=rt.PROJECT/'.runtime/test/reports/readonly-business-p2-20261012'
    folder=report/'visual-final';folder.mkdir(exist_ok=False)
    result=subprocess.run(['node',str(rt.PROJECT/'tests/browser/business_workspace_smoke.js')],
        input=json.dumps({'project':str(rt.PROJECT),'reportDir':str(folder),'loginUrl':(rt.ROOT/'open-workspace.html').as_uri(),'snapshotOnly':True}),
        capture_output=True,encoding='utf-8',timeout=120)
    (folder/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    assert result.returncode==0,'Read-only capture failed; preserved evidence'
    assert read(folder/'visual-verification.json')['no_diagnosis_submitted']
    print(result.stdout.strip())
if __name__=='__main__':main()
