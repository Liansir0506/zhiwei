"""Verify the original Docker project after update, without paid generation."""
import argparse
import hashlib
import json
import secrets
import subprocess
import time
from pathlib import Path

import httpx
import sqlalchemy as sa

from qa_core.config.settings import get_settings


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--report",required=True)
    args=p.parse_args(); path=Path(args.report)
    if path.exists(): raise ValueError("refuse report overwrite")
    s=get_settings(); url=sa.engine.make_url(s.mysql_dsn)
    if s.app_env!="dev" or url.database!="rag_kb" or url.port!=3306:
        raise ValueError("requires the original .env.runtime profile")
    root=Path(__file__).resolve().parents[1]
    files=["app.py","qa_core/retrieval/workflow.py","qa_core/application/evidence_service.py",
           "qa_core/quality/protocol_gate.py","qa_core/storage/connections.py","scripts/ingest_worker.py","static/workspace.js"]
    hashes={f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in files}
    result={"paid_llm_calls":0,"containers":[],"checks":{},"files_sha256":hashes}
    for name in ("api","ingest-worker","enterprise-mock"):
        c=json.loads(subprocess.check_output(["docker","inspect","telecom-ops-rag-"+name+"-1"],text=True))[0]
        assert c["State"]["Running"]
        result["containers"].append({"service":name,"image":c["Image"],"running":True,
            "mounts":[{k:m[k] for k in ("Source","Destination","Type")} for m in c["Mounts"]],
            "command":c["Config"]["Cmd"]})
    code="import hashlib,json; from pathlib import Path; fs="+repr(files)+"; print(json.dumps({f:hashlib.sha256(Path('/app',f).read_bytes()).hexdigest() for f in fs}))"
    actual=json.loads(subprocess.check_output(["docker","exec","telecom-ops-rag-api-1","python","-c",code],text=True))
    assert actual==hashes; result["checks"]["mounted_code_matches_worktree"]=True
    code="import importlib.metadata,json,torch; from qa_core.config.settings import get_settings; s=get_settings(); print(json.dumps({'torch':torch.__version__,'cuda':torch.version.cuda,'packages':{p:importlib.metadata.version(p) for p in ['fastapi','pymilvus','transformers','sentence-transformers']},'models':s.model_paths_exist(),'environment':s.app_env,'collections':[s.doc_collection,s.faq_collection],'auth_required':s.auth_required,'workflow_evidence_enabled':s.workflow_evidence_enabled}))"
    runtime=json.loads(subprocess.check_output(["docker","exec","telecom-ops-rag-api-1","python","-c",code],text=True))
    assert runtime["cuda"] is None and all(runtime["models"].values())
    result["runtime"]=runtime
    from qa_core.api.auth import create_api_key,hash_key
    key=secrets.token_urlsafe(24); actor="step5-runtime-"+secrets.token_hex(6)
    create_api_key(key,actor,["admin"])
    engine=sa.create_engine(s.mysql_dsn)
    try:
        with httpx.Client(base_url="http://127.0.0.1:8010",timeout=90,trust_env=False) as http:
            headers={"X-API-Key":key}
            health=http.get("/health?refresh=1"); assert health.status_code==200
            result["health"]=health.json(); assert result["health"]["status"]=="ok"
            deadline=time.monotonic()+120
            while result["health"]["intent_model"]["status"] not in ("ready","failed") and time.monotonic()<deadline:
                time.sleep(.5)
                result["health"]=http.get("/health").json()
            assert result["health"]["intent_model"]["ready"], result["health"]["intent_model"]
            result["checks"]["ordered_embedding_rerank_and_intent_warmup"]=True
            for target in ("/","/admin","/static/workspace.js"):
                r=http.get(target); assert r.status_code==200,(target,r.status_code)
            versions=http.get("/kb/versions",headers=headers); assert versions.status_code==200
            result["versions"]=versions.json(); assert [r["version"] for r in result["versions"]["versions"] if r["status"]=="ACTIVE"]==["v4-foundation-r3"]
            response=http.post("/qa/sse",json={"query":"你好","session_id":actor},headers=headers)
            assert response.status_code==200
            events=[]
            for frame in response.text.split("\n\n"):
                label=next((line[6:].strip() for line in frame.splitlines() if line.startswith("event:")),None)
                data="\n".join(line[5:].strip() for line in frame.splitlines() if line.startswith("data:"))
                if label and data: events.append({"event":label,"data":json.loads(data)})
            result["sse_greeting"]=events
            finals=[e["data"] for e in events if e.get("event")=="done"]
            assert len(finals)==1 and finals[0]["status"]=="ANSWERED"
            message=finals[0].get("message_id")
            assert message
            feedback=http.post("/feedback",json={"message_id":message,"rating":"up"},headers=headers)
            assert feedback.status_code==200; result["checks"]["real_sse_message_and_feedback"]=True
            # Legacy evidence remains fail-closed until source identities are governed.
            denied=http.post("/kb/search-evidence",json={"query":"AMF 告警处理"},headers=headers)
            assert denied.status_code==503,(denied.status_code,denied.text)
            assert denied.json().get("detail")=="证据验证暂不可用"
            citation=http.get("/kb/evidence/invalid-reference",headers=headers)
            assert citation.status_code==404
            result["legacy_evidence"]={"search_status":503,"citation_status":404,"detail":denied.json()}
            assert http.get("/metrics").status_code==200
        with engine.connect() as conn:
            result["jobs"]=[dict(r) for r in conn.execute(sa.text("SELECT status,COUNT(*) n FROM ingest_jobs GROUP BY status")).mappings()]
            assert not any(r["status"] not in ("success","failed","cancelled") for r in result["jobs"])
    finally:
        with engine.begin() as conn: conn.execute(sa.text("UPDATE api_keys SET enabled=0 WHERE key_hash=:h"),{"h":hash_key(key)})
        engine.dispose()
    result["passed"]=True;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":True,"active":"v4-foundation-r3","code_matches":True,"paid_llm_calls":0}))


if __name__=="__main__": main()
