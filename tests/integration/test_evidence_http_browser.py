"""Real localhost HTTP + isolated headless Edge; synthetic source fixture only."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
import httpx
import pytest
from sqlalchemy import text
from pymilvus import MilvusClient
from qa_core.config.settings import get_settings
from qa_core.api.auth import create_api_key, hash_key
from qa_core.storage.kb_dao import KbDao
from qa_core.retrieval.filters import _to_doc
from qa_core.schemas.evidence import evidence_of
from scripts.init_collections import build
from scripts.rebuild_kb import to_milvus_row
from qa_core.indexing.chunkers.base import make_chunk
from qa_core.storage.location_dao import LocationDao


def port():
    with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]


def test_real_http_and_browser_source_panel(tmp_path):
    edge=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    if not edge.is_file() or not shutil.which('node'): pytest.skip('Edge and Node required')
    settings=get_settings(); token=uuid.uuid4().hex[:12]
    collection,version=f'test_browser_{token}',f'browser-it-{token}'
    dao=KbDao(settings.mysql_dsn); client=MilvusClient(uri=settings.milvus_uri,timeout=15)
    key='browser-test-'+uuid.uuid4().hex; other_key='browser-other-'+uuid.uuid4().hex
    server=None; identities=[]
    root=Path(__file__).resolve().parents[2]
    screenshot=root/'.runtime/test/reports/evidence-browser-20260927.png'
    screenshot.parent.mkdir(parents=True,exist_ok=True)
    try:
        build(client,collection,False);dao.ensure_version(version)
        create_api_key(key,'browser-admin-'+token,['admin'])
        create_api_key(other_key,'browser-other-'+token,['admin'],tenant_id='other-'+token)
        locations=LocationDao(dao.engine);locations.ensure_schema()
        evidence=[];rows=[]
        for i,fmt in enumerate(['md','pdf','docx']):
            chunk=make_chunk('Unique '+fmt+' evidence.','manual',source='manual_demo.'+fmt,
                asset_id='browser-asset-'+token+str(i),source_version=version,tenant_id='default',
                security_level='internal',visibility='public',status='active',synthetic=True)
            row=to_milvus_row(chunk,'browser-'+token+str(i),[1.0]+[0.0]*1023,version)
            doc=_to_doc({'id':row['id'],'entity':row});rows.append(row)
            position=({'kind':'lines','line_start':2,'line_end':2} if fmt=='md' else
                      {'kind':'page','page':1} if fmt=='pdf' else {'kind':'paragraph','paragraph':2})
            # Fixture positions are explicit; actual parser anchors are checked by the three-format ingest test.
            locations.save(doc,{'kind':'source_spans','format':fmt,'matches':[[position]],'ambiguous':False})
            identities.append(locations.identity(doc));evidence.append(evidence_of(doc))
        client.upsert(collection_name=collection,data=rows);client.flush(collection)
        from qa_core.retrieval.workflow import assemble_sop
        from qa_core.application.qa_usecase import QaUseCase
        flow_docs=[]; flow_rows=[]
        for i,body in enumerate(['# AMF sandbox SOP','步骤 1：只读检查，停止并转人工。','步骤 2：人工审批，失败回滚。']):
            chunk=make_chunk(body,'sop',source='sop_browser.md',asset_id='flow-'+token,
                source_version=version,step_index=[i],tenant_id='default',security_level='internal',
                visibility='public',status='active',synthetic=True)
            row=to_milvus_row(chunk,'flow-'+token+str(i),[1.0]+[0.0]*1023,version)
            flow_rows.append(row);flow_docs.append(_to_doc({'id':row['id'],'entity':row}))
        packet=assemble_sop(flow_docs,flow_docs[0]);assert packet.ready
        flow=QaUseCase._sources_of([packet.packet])[0]
        client.upsert(collection_name=collection,data=flow_rows);client.flush(collection)
        api_port=port();env={**os.environ,'APP_ENV_FILE':str(root/'.env.test'),'APP_ENV':'test',
            'DOC_COLLECTION':collection,'AUTH_REQUIRED':'true','PYTHONIOENCODING':'utf-8'}
        server=subprocess.Popen([sys.executable,'-B','-m','uvicorn','app:app','--host','127.0.0.1','--port',str(api_port)],
            cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        url=f'http://127.0.0.1:{api_port}'
        with httpx.Client(timeout=3,trust_env=False) as http:
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                try:
                    if http.get(url+'/').status_code==200:break
                except httpx.TransportError:pass
                if server.poll() is not None:pytest.fail('isolated API server exited')
                time.sleep(.1)
            else: pytest.fail('isolated API readiness exceeded 25 seconds')
            for e in evidence:
                target=url+e['source_url']
                assert http.get(target).status_code==401
                assert http.get(target,headers={'X-API-Key':other_key}).status_code==404
                result=http.get(target,headers={'X-API-Key':key})
                assert result.status_code==200 and result.json()['text']==e['text']
                assert result.headers['cache-control']=='no-store'
            run=subprocess.run(['node',str(root/'tests/browser/evidence_smoke.js')],input=json.dumps({
                'edge':str(edge),'profile':str(tmp_path/'isolated-edge'),'debugPort':port(),
                'url':url,'key':key,'version':version,'evidence':evidence,'flow':flow,'screenshot':str(screenshot)}),
                text=True,encoding='utf-8',capture_output=True,timeout=80)
            assert run.returncode==0,run.stderr
            client.upsert(collection_name=collection,data=[{**rows[0],'status':'retired'}]);client.flush(collection)
            assert http.get(url+evidence[0]['source_url'],headers={'X-API-Key':key}).status_code==404
        assert dao.get_version(version)['status']=='STAGED'
    finally:
        if server:server.terminate();server.wait(timeout=15)
        if client.has_collection(collection):client.drop_collection(collection)
        client.close()
        with dao.engine.begin() as conn:
            for identity in identities:conn.execute(text('DELETE FROM evidence_locations WHERE identity_hash=:id'),{'id':identity})
            for value in [key,other_key]:conn.execute(text('DELETE FROM api_keys WHERE key_hash=:h'),{'h':hash_key(value)})
            conn.execute(text('DELETE FROM kb_versions WHERE version=:v'),{'v':version})
