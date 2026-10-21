"""Verify owned stop/restart and bounded observations; seal honest preview evidence."""
from datetime import datetime,timezone
import html
import json
from pathlib import Path
import socket
import time
import xml.etree.ElementTree as ET
import httpx
import sqlalchemy as sa
from scripts import sop_guide_preview_runtime as rt
from scripts.verify_sop_guide_preview import REPORT,protected
from scripts.sop_evidence_stage_profile import profile
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from qa_core.quality.guide_preview_checks import verify_live


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('already sealed')
    attempts=[p for p in REPORT.glob('attempt-*') if read(p/'state.json').get('state')=='completed'
              and (p/'code-bindings.json').exists() and read(p/'code-bindings.json')==rt.bindings()]
    if len(attempts)!=1:raise ValueError('one completed final-code acceptance required')
    attempt=attempts[0]
    assert read(attempt/'before.json')==read(attempt/'after.json')
    assert len(read(attempt/'http-checks.json'))==24 and len(read(attempt/'browser-checks.json'))==9
    assert read(attempt/'key-cleanup.json')['temporary_removed']==4
    tests=ET.parse(REPORT/'unit-tests.xml').getroot().find('testsuite')
    assert int(tests.attrib['tests'])==45 and int(tests.attrib['failures'])==int(tests.attrib['errors'])==0
    # Browser session data can contain the private technical key; remove owned profiles before sealing.
    assert not list(REPORT.glob('attempt-*/edge-profile'))
    before=protected();write(REPORT/'lifecycle-before.json',before)
    old=rt.record();key=(rt.ROOT/'api-key.txt').read_text(encoding='utf-8')
    old_pids=[p['pid'] for p in old['processes']]
    stopped=rt.stop();assert stopped['owned_preview_key_removed']
    for item in old['processes']:assert rt.owned_process({**old,**item}) is None
    with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',rt.PORT))!=0
    engine=sa.create_engine(profile().mysql_dsn)
    try:
        with engine.connect() as conn:assert conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys').scalar_one()==0
    finally:engine.dispose()
    write(REPORT/'lifecycle-stopped.json',dict(owned_pids=old_pids,all_exited=True,port_closed=True,new_namespace_keys=0,
          original_and_existing_readonly_not_stopped=True))
    started=rt.start();assert started['status']=='running'
    current=rt.record();assert current['instance']!=old['instance']
    new_key=(rt.ROOT/'api-key.txt').read_text(encoding='utf-8');assert new_key!=key
    observations=[]
    with httpx.Client(base_url=started['url'],trust_env=False,timeout=45) as client:
        assert client.get('/preview/checks',headers={'X-API-Key':key}).status_code==401
        began=time.monotonic()
        for i in range(3):
            response=client.get('/preview/checks',headers={'X-API-Key':new_key})
            assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
            data=response.json();assert data['read_preview_ready'] and len(data['checks'])==8
            assert data['ordinary_publication_gate']['passed'] is False
            assert rt.inspect()['status']=='running'
            observations.append(dict(at_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=round(time.monotonic()-began,2),
                      live_guide_checks=8,read_preview_ready=True,ordinary_gate_passed=False))
            if i<2:time.sleep(7)
    write(REPORT/'bounded-observations.json',dict(observations=observations,long_term_availability_passed=False))
    write(REPORT/'guide-integrity.json',verify_live(profile()))
    after=protected();write(REPORT/'lifecycle-after.json',after);assert before==after
    engine=sa.create_engine(profile().mysql_dsn)
    try:
        with engine.connect() as conn:assert conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys').scalar_one()==1
    finally:engine.dispose()
    codes=rt.CODE+('scripts/verify_sop_guide_preview.py','scripts/verify_sop_guide_preview_browser.js',
                   'scripts/finalize_sop_guide_preview.py','tests/unit/test_guide_preview_checks.py')
    write(REPORT/'code-bindings.json',{name:digest(rt.PROJECT/name) for name in codes})
    summary=dict(state='completed',version='test-sop-evidence-r1',status='STAGED',guide_sources=20,real_chunks=160,
        guide_read_integrity_checks=8,unit_tests=45,real_http_checks=24,real_edge_checks=9,final_attempt=attempt.name,
        owned_stop_restart_passed=True,owned_processes=len(current['processes']),temporary_keys_created=4,
        temporary_keys_removed=4,persistent_preview_keys=1,original_keys_roles_pids_unchanged=True,
        original_readonly_and_stage_knowledge_snapshots_unchanged=True,ordinary_gate_report_unchanged=True,
        ordinary_publication_gate_passed=False,activated=False,published=False,generation_enabled=False,agent_enabled=False,
        qa_requests=0,paid_calls=0,independent_denominator=0,independent_quality=None,full_sop_approved=False,
        long_term_availability_passed=False,loopback_url=started['url'],private_login_file=started['login_file'],
        note='Technical preview credential exists only in new namespace; not a human reviewer identity.',
        lifecycle_note='Initial stop exposed Windows child-exit/launcher race; cleanup completed, exception handling and owned-tree recording fixed and retested.',
        limitations=['Two captures detect ordinary concurrent changes; no atomic cross-store lock.',
                    'Only reused exposed development queries; no independent truth or generation-quality assessment.',
                    'Dedicated guide preview is separate from platform startup controller; no original configuration switch.'])
    write(REPORT/'summary.json',summary)
    esc=lambda x:html.escape(str(x),quote=True)
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>指南只读预览实际验收</title><style>body{{font:16px/1.7 system-ui;background:#f3f6fb;color:#20314c;margin:0}}main{{max-width:1000px;margin:auto;padding:24px}}section{{background:white;border-radius:12px;padding:22px;margin:20px 0}}a{{color:#2454b9}}p,a,li{{overflow-wrap:anywhere}}img{{max-width:100%}}@media(max-width:500px){{main{{padding:12px}}section{{padding:14px}}}}</style><main>
<h1>20指南 STAGED · 管理员只读预览</h1><section><p>候选、20原件、160实际向量保持。8项指南读取完整性检查通过；普通发布门禁仍失败，没有改写报告或发布资格，生成与Agent关闭。</p><p><a href="{esc(Path(started['login_file']).as_uri())}">本机私有预览入口</a> · <a href="summary.json">验收范围</a> · <a href="guide-integrity.json">8项实时完整性与普通门禁</a></p><p>目录按具体指南标题显示；检索结果可重读原文片段，当前不提供生成答案。依赖、来源/权限、配置、快照变化时停止本次预览。认证后的读取前后均核验，仅default未限域admin访问；ops/guest、其他租户/限域admin、角色头覆盖受限，原平台角色未扩大。</p></section>
<section><h2>真实服务与页面验收</h2><p>45单元、24真实网络HTTP、9无窗口Edge通过。浏览器实际读取新SQL/Milvus目录20与已公开AMF检索，引用重读字节相符；完整流程无证据、凭据从地址移除、退出清理、390像素无横向溢出。没有模拟API回复或付费调用。</p><p><a href="{attempt.name}/http-checks.json">24 HTTP</a> · <a href="{attempt.name}/browser-checks.json">9浏览器</a> · <a href="unit-tests.xml">45单元</a></p><img alt="实际管理员检查与缺口页" src="{attempt.name}/checks-desktop.png"><img alt="实际手机检索与原文页" src="{attempt.name}/search-mobile.png"></section>
<section><h2>启停与保护</h2><p>新入口只绑定127.0.0.1:18024，隐藏启动。Windows启动器与实际服务均核对PID/创建时间/命令/cwd/instance后停止；停止后端口关闭、仅新库Key0，重新启动后旧Key401，新库保留1个技术预览Key。4临时验证Key已删除，浏览器私有profile已清理。原8010及既有只读入口Key/角色/PID、知识与权限、候选/普通门禁、环境与费用预算前后保持。</p><p>初次完善停止处理时碰到子进程结束后Windows启动器已自动退出的竞争条件，清理随后完成；已修正并实际停启重验。前两轮是中间实现的开发验证，最终代码对应{esc(attempt.name)}，不把反复使用的问题算新独立样本。</p><p><a href="lifecycle-stopped.json">真实停止核对</a> · <a href="bounded-observations.json">重启后短时连续观察</a> · <a href="lifecycle-before.json">保护前</a> · <a href="lifecycle-after.json">保护后</a></p></section>
<section><h2>仍未完成</h2><p>无工单召回分母，SOP和工单去重分母0；指南仅补取证与交接，设备厂家/型号/软件版本/门限、授权现场动作与完整回退/恢复仍缺。独立问题/真值冻结0、指标null，未测生成质量，也没有长期稳定性结论。</p><p>新预览不是原8010切换，原入口未加入这20正文。只读检查只用于检查新候选的完整性，不替代具体发布/外发批准。普通失败报告和kb_versions质量指针保留，候选仍STAGED。</p></section></main></html>'''
    (REPORT/'review.html').write_text(page,encoding='utf-8')
    seal(REPORT);verify_seal(REPORT)
    print(json.dumps(dict(state='sealed',unit_tests=45,http_checks=24,browser_checks=9,live_checks=8,paid_calls=0,
                          status='STAGED',report=str(REPORT/'review.html'))))


if __name__=='__main__':main()
