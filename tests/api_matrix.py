"""全接口功能矩阵测试（答辩前终检）。"""
import atexit
import concurrent.futures
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:18010")
RUN_ID = uuid.uuid4().hex[:10]
SESSION_PREFIX = f"matrix-{RUN_ID}"
ANON = {}
OPS = {"X-User-Roles": "ops", "X-User-Id": f"matrix-ops-{RUN_ID}"}
AUD = {"X-User-Roles": "auditor", "X-User-Id": f"matrix-aud-{RUN_ID}"}
ADMIN = {"X-User-Roles": "admin", "X-User-Id": f"matrix-admin-{RUN_ID}"}
results = []
session_ids: set[str] = set()
active = ""
uploaded_filename = f"faq_matrix_test_{RUN_ID}.csv"
uploaded = False
cleaned = False

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), "|", name, ("| " + detail if detail and not ok else ""))

c = httpx.Client(base_url=BASE, timeout=90)
health = c.get("/health")
server_env = health.json().get("environment") if health.status_code == 200 else None
if server_env != "test":
    c.close()
    raise SystemExit(
        f"拒绝执行：接口矩阵只能写入 test 环境，{BASE} 当前为 {server_env!r}。"
    )


def ask(query: str, headers: dict, suffix: str = "") -> httpx.Response:
    sid = f"{SESSION_PREFIX}-{suffix or len(session_ids)}"
    session_ids.add(sid)
    return c.post("/qa", json={"query": query, "session_id": sid}, headers=headers)


def cleanup() -> None:
    """Best-effort cleanup; durable DB cleanup only runs behind local test guards."""
    global cleaned
    if cleaned:
        return
    cleaned = True
    if uploaded and active:
        try:
            c.delete("/admin/documents", params={"source": uploaded_filename,
                                                  "kb_version": active}, headers=ADMIN)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN | 测试文档索引清理失败: {exc}")
    try:
        from qa_core.config.environment import validate_isolated_environment
        from qa_core.config.settings import get_settings

        settings = get_settings()
        problems = validate_isolated_environment(settings, "test")
        if problems:
            print("WARN | 跳过本地测试记录清理: " + "; ".join(problems))
        else:
            import sqlalchemy as sa

            engine = sa.create_engine(settings.mysql_dsn)
            with engine.begin() as conn:
                for sid in session_ids:
                    conn.execute(sa.text(
                        "DELETE FROM feedback WHERE message_id IN "
                        "(SELECT message_id FROM messages WHERE session_id=:s)"), {"s": sid})
                    conn.execute(sa.text("DELETE FROM messages WHERE session_id=:s"), {"s": sid})
                    conn.execute(sa.text("DELETE FROM sessions WHERE session_id=:s"), {"s": sid})
                conn.execute(sa.text("DELETE FROM api_keys WHERE user_id LIKE :u"),
                             {"u": f"matrix-%-{RUN_ID}"})
            raw_file = Path(settings.data_dir) / "raw" / uploaded_filename
            if raw_file.is_file():
                raw_file.unlink()
            try:
                import redis

                rc = redis.Redis.from_url(settings.redis_url)
                for key in rc.scan_iter(match=f"*{RUN_ID}*"):
                    rc.delete(key)
            except Exception as exc:  # noqa: BLE001
                print(f"WARN | Redis 测试键清理失败: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN | 测试记录清理失败: {exc}")
    c.close()


atexit.register(cleanup)

# --- 公开接口 ---
check("GET /health", health.status_code == 200)
check("GET / 首页", c.get("/").status_code == 200)
check("GET /admin 状态页", c.get("/admin").status_code == 200)
check("GET /docs API文档", c.get("/docs").status_code == 200)

# --- 问答四路由 ---
for q, expect in [("你好", "direct_answer"), ("驻波比多少需要处理", "faq_exact"),
                  ("ALM-1201", "structured_lookup"), ("SPN 光功率管理要求", "retrieval")]:
    r = ask(q, OPS)
    d = r.json()
    check(f"POST /qa [{q[:14]}] → {expect}", r.status_code == 200 and d["route"] == expect,
          f"got {r.status_code}/{d.get('route')}")

# --- 反馈 ---
r = c.get("/kb/versions", headers=OPS)
active = next(v["version"] for v in r.json()["versions"] if v["status"] == "ACTIVE")
# 拿一条消息 id
hist = ask("你好", OPS, "feedback").json()
mid = hist.get("message_id")
if mid:
    fb = c.post("/feedback", json={"message_id": mid, "rating": "up"}, headers=OPS)
    check("POST /feedback", fb.status_code == 200)

# --- 告警 webhook ---
r = c.post("/alerts/webhook", json={"alarm_code": "ALM-1201"}, headers=OPS)
check("POST /alerts/webhook", r.status_code == 200 and r.json()["advice"])

# --- metrics ---
r = c.get("/metrics", headers=OPS)
check("GET /metrics", r.status_code == 200 and "qa_requests_total" in r.text)

# --- kb 版本管理 ---
check("GET /kb/versions", c.get("/kb/versions", headers=OPS).status_code == 200)
r = c.post("/kb/activate", json={"version": "v999"}, headers=ADMIN)
check("激活假版本 → 404", r.status_code == 404)
r = c.post("/kb/rollback", json={"version": active}, headers=ADMIN)
check("回滚到自身 → 400", r.status_code == 400)

# --- admin 治理接口：角色矩阵 ---
OPS_OK = {"/admin/overview", "/admin/ops", "/admin/kb", "/admin/documents"}
for ep in ["/admin/overview", "/admin/ops", "/admin/eval", "/admin/kb", "/admin/permissions", "/admin/documents"]:
    r_anon = c.get(ep)
    r_ops = c.get(ep, headers=OPS)
    r_admin = c.get(ep, headers=ADMIN)
    expect_ops = 200 if ep in OPS_OK else 403
    check(f"GET {ep} [角色]", r_anon.status_code == 403 and r_ops.status_code == expect_ops
          and r_admin.status_code == 200, f"anon={r_anon.status_code} ops={r_ops.status_code} admin={r_admin.status_code}")

# eval/permissions 为 auditor/admin 专属：ops 访问应 403
r = c.get("/admin/eval", headers=OPS)
check("GET /admin/eval [ops→403]", r.status_code == 403)
r = c.get("/admin/permissions", headers=OPS)
check("GET /admin/permissions [ops→403]", r.status_code == 403)

# --- 文档管理 ---
r = c.get("/admin/documents/chunks", params={"source": "manual_otn_wdm.md", "kb_version": active}, headers=OPS)
check("GET chunks 溯源", r.status_code == 200 and r.json()["chunks"] > 0)
r = c.get("/admin/documents/chunks", params={"source": "fault_case_tickets_v3.csv", "kb_version": active}, headers=OPS)
check("GET chunks 工单溯源", r.status_code == 200 and r.json()["chunks"] > 0)
r = c.get("/admin/documents/chunks", params={"source": "faq_wireless_5gc_v3.csv", "kb_version": active}, headers=OPS)
check("GET chunks FAQ溯源", r.status_code == 200 and r.json()["chunks"] > 0)

# --- 上传：合法 + 非法格式 ---
content = "question,answer\n测试问题？,测试答案\n".encode("utf-8")
r = c.post("/admin/documents/upload", headers={**ADMIN, "X-Filename": uploaded_filename}, content=content)
uploaded = r.status_code == 202
upload_job_id = r.json().get("job_id", "") if r.status_code == 202 else ""
check("POST upload [faq csv→task]", r.status_code == 202 and bool(r.json().get("job_id")), r.text[:80])
r = c.post("/admin/documents/upload", headers={**ADMIN, "X-Filename": "evil.exe"}, content=b"MZ")
check("POST upload [非法格式→400]", r.status_code == 400)
c.post("/admin/documents/upload", headers={**ADMIN, "X-Filename": uploaded_filename}, content=content)  # 幂等重传
job_status = "pending"
for _ in range(60):
    r = c.get(f"/kb/rebuild/{upload_job_id}", headers=OPS)
    job_status = r.json().get("status") if r.status_code == 200 else "query_failed"
    if job_status in {"success", "failed", "cancelled"}:
        break
    time.sleep(1)
check("上传任务完成", job_status == "success", job_status)
r = c.get("/admin/documents/chunks", params={"source": uploaded_filename,
                                                "kb_version": active}, headers=OPS)
check("上传后可溯源", r.status_code == 200 and r.json()["chunks"] >= 1)

# --- 权限管理：签发/吊销/越权 ---
ADMIN_ID = ADMIN
_test_key = f"matrix-test-key-{RUN_ID}"
_test_user = f"matrix-key-{RUN_ID}"
r = c.post("/admin/keys", json={"key": _test_key, "user_id": _test_user, "roles": ["ops"]}, headers=ADMIN_ID)
check("POST /admin/keys [admin]", r.status_code == 200)
r = c.post("/admin/keys", json={"key": f"matrix-denied-{RUN_ID}",
                                 "user_id": f"matrix-denied-{RUN_ID}",
                                 "roles": ["ops"]}, headers=OPS)
check("POST /admin/keys [ops→403]", r.status_code == 403)
r = c.get("/admin/permissions", headers=ADMIN_ID)
keys = r.json()["keys"]
target = next((k for k in keys if k["user_id"] == _test_user), None)
check("签发后可见", target is not None)
r = c.get("/admin/permissions", headers=ADMIN)
hash_prefix = target["key_hint"].replace("…", "")
r = c.delete(f"/admin/keys/{hash_prefix}", headers=ADMIN_ID)
check("DELETE 吊销", r.status_code == 200)
r = c.get("/admin/permissions", headers=ADMIN_ID)
keys = r.json()["keys"]
target = next((k for k in keys if k["user_id"] == _test_user), None)
check("吊销后 enabled=0", target is not None and target["enabled"] == 0)

# --- 限流 ---
rl_headers = {"X-User-Id": f"matrix-rl-{RUN_ID}"}
rate_sessions = [f"{SESSION_PREFIX}-rate-{i}" for i in range(12)]
session_ids.update(rate_sessions)


def _rate_hit(session_id: str) -> int:
    return c.post("/qa", json={"query": "你好", "session_id": session_id},
                  headers=rl_headers).status_code


with concurrent.futures.ThreadPoolExecutor(max_workers=len(rate_sessions)) as pool:
    codes = list(pool.map(_rate_hit, rate_sessions))
check("限流 429 触发", 429 in codes, str(codes))

# --- 语义缓存 ---
r1 = ask("运营商量子加密骨干网方案", OPS, "semantic-1").json()
# 拒答类不写缓存；改用检索类验证（需要真实 LLM，仅检查标记字段存在性）
r2 = ask("5G基站传输中断排查步骤", OPS, "semantic-2").json()
check("诊断字段完备", "diagnostics" in r2 and "kb_version" in r2["diagnostics"])

# --- 评测一致性 ---
ev = c.get("/admin/eval", headers=ADMIN).json()
m = ev["metrics"]
check("评测三项全满分", m.get("route_accuracy") == 1.0
      and m.get("hit_accuracy") == 1.0 and m.get("reject_accuracy") == 1.0, str(m))

print("\n===== 汇总 =====")
passed = sum(1 for _, ok, _ in results if ok)
print(f"通过 {passed}/{len(results)}")
fails = [(n, d) for n, ok, d in results if not ok]
for n, d in fails:
    print("FAIL:", n, d)
cleanup()
if fails:
    raise SystemExit(1)
