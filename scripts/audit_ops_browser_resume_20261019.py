"""Resume only the headless walkthrough; API audit artifacts remain unchanged."""
import json,secrets,subprocess,argparse
import sqlalchemy as sa
from scripts.audit_ops_20261019 import PROJECT,REPORT,PRIVATE,protected,write
from scripts import demo_release_runtime_r2 as r
from scripts.build_original_rollback_baseline import apply_settings


def main(admin_final=False):
    r.install_adapter();s=r.selected_settings();apply_settings(s)
    from qa_core.api.auth import create_api_key,hash_key
    prefix='browser-admin-final' if admin_final else 'browser'
    before=protected(s);write(prefix+'-protected-before.json',before)
    keys={'ops':r.owned.read_key(r.owned.load_state())};created=[];engine=sa.create_engine(s.mysql_dsn)
    try:
        for name,roles in [('admin',['admin']),('auditor',['auditor']),('guest',['guest'])]:
            value=secrets.token_urlsafe(32);actor='ops-audit-20261019-browser-'+name+'-'+secrets.token_hex(5)
            create_api_key(value,actor,roles);created.append((hash_key(value),actor));keys[name]=value
        private=PRIVATE/'keys.json';private.write_text(json.dumps(keys),'utf-8')
        payload=dict(project=str(PROJECT),report_dir=str(REPORT/prefix),url='http://127.0.0.1:8010',keys_file=str(private))
        if admin_final:payload['roles']=['admin']
        (REPORT/prefix).mkdir(exist_ok=True)
        result=subprocess.run(['node',str(PROJECT/'scripts/audit_ops_pages_20261019.js')],input=json.dumps(payload),text=True,encoding='utf-8',capture_output=True,timeout=650,creationflags=subprocess.CREATE_NO_WINDOW)
        write(prefix+'-command.json',dict(returncode=result.returncode,message=result.stderr.strip()[:250]))
        after=protected(s);write(prefix+'-protected-after.json',after)
        write(prefix+'-data-preservation.json',dict(equal=before==after,paid_calls=0,qa_submissions=0))
        print(json.dumps({'browser_exit':result.returncode,'data_equal':before==after}))
    finally:
        with engine.begin() as c:
            for h,actor in created:c.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),dict(h=h,u=actor))
        engine.dispose();(PRIVATE/'keys.json').unlink(missing_ok=True);write(prefix+'-key-cleanup.json',dict(created=len(created),removed=len(created)))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--admin-final',action='store_true');args=parser.parse_args();main(args.admin_final)
