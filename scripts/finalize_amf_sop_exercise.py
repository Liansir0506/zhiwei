"""Seal the offline pilot and explicitly record unavailable online protection checks."""
import json
import subprocess
from xml.etree import ElementTree as ET
from scripts.prepare_amf_sop_exercise import ROOT,OUTPUT,REPORT,validate,source_binding,CODE
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('never reseal a report')
    validate();checks=read(REPORT/'browser-checks.json')
    assert len(checks)==18 and all(c['passed'] for c in checks)
    assert not (REPORT/'exercise-edge-profile').exists()
    junit=ET.parse(REPORT/'unit-tests.xml').getroot()
    assert len(junit.findall('.//testcase'))==31
    assert not junit.findall('.//failure') and not junit.findall('.//error') and not junit.findall('.//skipped')
    payload=dict(engine_path=str(OUTPUT/'engine.js'),binding=source_binding())
    result=subprocess.run(['node',str(ROOT/'scripts/run_amf_exercise_cases.cjs')],input=json.dumps(payload),
        text=True,encoding='utf-8',capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError('model scenarios failed; safe report retained')
    scenarios=json.loads(result.stdout);assert len(scenarios['cases'])==8 and all(c['passed'] for c in scenarios['cases'])
    write(REPORT/'scenarios-execution.json',scenarios)
    write(REPORT/'finalization-parser-correction.json',dict(first_check='Expected nested testsuite counters',
        actual_node_format='31 testcase nodes directly below testsuites',correction='Count actual testcase elements and reject failure/error/skipped nodes',
        tests_rerun=False,model_or_platform_changes=0))
    # The attempted live baseline failed before any DB snapshot was obtained.
    write(REPORT/'online-verification-limit.json',dict(attempted=True,status='unavailable',
        endpoint='127.0.0.1:19530',error_kind='MilvusException',live_snapshot_captured=False,
        live_database_equality_proven=False,storage_started_or_reconfigured=False,
        scope='This delivery validates an offline in-memory exercise only; no platform DB, vector, role or PID mutation code is invoked'))
    protected_files=[ROOT/'.env.runtime',ROOT/'.runtime/original-local/generation-validation-budget.json']
    write(REPORT/'artifact-protection.json',dict(exercise_package_seal_verified=True,
        existing_runtime_mutations_performed=False,old_admitted_packages_unchanged=True,
        old_key_values_not_read_or_exported=True,files={str(p.relative_to(ROOT)):digest(p) for p in protected_files if p.is_file()},
        actual_online_state_not_revalidated=True))
    names=(*CODE,'scripts/verify_amf_sop_exercise_browser.js','scripts/run_amf_exercise_cases.cjs','scripts/finalize_amf_sop_exercise.py')
    write(REPORT/'code-bindings.json',{name:digest(ROOT/name) for name in names})
    summary=dict(state='completed_offline_pilot',pilot='AMF ingress branch',unit_tests=31,browser_checks=18,
        executed_scenarios=8,pending_new_sop_drafts=1,source_reference='SOP-EVIDENCE-001 / draft-r2',
        execution_environment='in_memory_training',network_requests_allowed_by_browser=False,
        platform_actions_available=False,real_action_authorized=False,site_truth_verified=False,
        human_review_verified=False,independent_denominator=0,new_admissions=0,new_ingestions=0,new_staged=0,
        new_embeddings=0,paid_calls=0,existing_runtime_restarted=False,live_protection_snapshot_available=False,
        original_milvus_endpoint_unavailable=True,browser_profile_removed=True,
        exercise_package_sha256=digest(OUTPUT/'artifact-hashes.json'),page=str(OUTPUT/'exercise.html'),
        next='Review this single training SOP and extend the exercise workflow only within the training scope; no automatic admission or publication')
    write(REPORT/'summary.json',summary)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AMF流程演练样板验收</title><style>body{font:16px/1.7 system-ui;background:#f3f6fb;color:#20314c;margin:0}main{max-width:1000px;margin:auto;padding:24px}section{background:white;padding:20px;border-radius:12px;margin:16px 0}p,a{overflow-wrap:anywhere}img{max-width:100%;height:auto}</style><main><h1>AMF流程演练样板已完成</h1><section><p>一个AMF入口排队演练贯通取证、同窗分母、支持/相反分支、具体计划、本页动作确认、内存变更、停止、配置回退、失败升级和恢复观察。操作只发生在本页内存，导出记录保留generated_training_model来源，不认定现场事实或真实审批。</p><p>模型基线预算80、故障20及请求计数均是该训练模型定义。当前窗完成与历史排队完成分开，正常例首个观察窗仍排队，随后两个合格窗才完成。回退恢复故障前保存的配置后仍升级，不能把配置回退当作业务恢复。</p><a href="summary.json">验收汇总与范围</a> · <a href="scenarios-execution.json">8个模型场景及完整事件账本</a> · <a href="browser-checks.json">18真实无窗口浏览器检查</a> · <a href="unit-tests.xml">31行为检查</a></section><section><p>8场景：正常、下游相反分支、证据矛盾、记录缺失、对象不匹配、应用中断回退、回退失败、观察复发。真实页面逐个点击，确认前执行不可用、过早完成被阻止、停止与回退状态正确，390手机无横向溢出。</p><p>一份新训练SOP正文、来源/内部范围提案与代码包封存，正文仍pending；没有注册来源或写知识库/向量、新STAGED/付费。不是完整现场SOP、厂家门限或独立质量验收。</p></section><section><p>在线保护快照尝试失败：原Milvus 127.0.0.1:19530当前不可连接，未获得在线库相等证明。本步没有启动/重配存储或修改平台角色、入口与业务数据；离线演练检查不被描述为在线服务验收。</p><a href="online-verification-limit.json">实际验证限制</a></section><section><h2>实际页面</h2><img src="exercise-desktop.png" alt="AMF流程演练正常闭环"><p><a href="exercise-mobile.png">手机停止与回退页面</a></p></section></main></html>'''
    (REPORT/'review.html').write_bytes(page.encode('utf-8'))
    validate();seal(REPORT);verify_seal(REPORT);verify_seal(OUTPUT)
    print(json.dumps(dict(state='sealed_offline',unit_tests=31,browser_checks=18,scenarios=8,paid_calls=0,online_snapshot_available=False)))


if __name__=='__main__':main()
