"""One executable local-training SOP draft; no knowledge admission or platform actions."""
import json
import shutil
import subprocess
from pathlib import Path
from scripts.prepare_sop_record_alignment_v2 import load_verified
from scripts.prepare_sop_record_admission import ROOT,load_verified as admission
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal

OUTPUT=ROOT/'data/candidates/amf-sop-exercise-r1'
REPORT=ROOT/'.runtime/test/reports/amf-sop-exercise-20261015'
ASSETS={'engine.js':'static/amf-exercise-engine.js','workspace.js':'static/amf-exercise-workspace.js',
    'exercise.css':'static/amf-exercise.css','exercise.html':'static/amf-exercise.html'}
CODE=(*ASSETS.values(),'scripts/prepare_amf_sop_exercise.py','tests/training/amf-exercise.test.cjs')


def source_binding():
    matrix=load_verified();plan=admission()
    entry=next(e for e in plan['entries'] if e['id']=='SOP-EVIDENCE-001')
    guide=next(e for e in matrix['entries'] if e['id']==entry['id'])
    if entry['body_sha256']!=guide['guide_sha256']:raise ValueError('AMF reference mismatch')
    return dict(guide_source=entry['filename'],source_version='draft-r2',body_sha256=entry['body_sha256'],
        title=entry['title'],reference_kind='admitted_project_authored_guide',
        guide_records=[r['id'] for r in guide['records']],source_authenticity_verified_by_exercise=False)


def sop_body():
    return '''# AMF注册失败：入口排队分支流程演练（待审）

适用环境：本地内存模型amf-model-v1，唯一对象train-amf-001。仅操作本页模型状态。
来源：项目自编训练流程，参考已准入AMF draft-r2指南的取证与分支结构；不是厂家手册或现场处置依据。

## 1. 适用对象和证据
核对environment=in_memory_training、target_id=train-amf-001、schema_version=amf-model-v1及当前配置修订。任一不匹配停止。
采集registration_window、ingress_queue、downstream_auth三个同窗模型记录，保留对象、修订、训练窗、单位、当前窗请求/完成、历史排队完成、队列和鉴权错误。
当前完成数不能大于同窗请求数；历史排队完成另列。缺失、错窗、单位不可比、对象或修订不一致停止，不能按零值补齐。

## 2. 分支判断
模型基线每训练窗收到40个请求，入口预算80；故障窗收到60个请求，入口预算20。
这些数是本练习的模型定义，不是AMF设备门限。请求相对基线增加且入口队列非零、同窗无鉴权错误时，进入入口排队练习分支。
鉴权错误非零而入口排队条件不成立，交接下游分支；两边同时成立时记录矛盾并停止。没有支持条件时保留未知。

## 3. 动作计划与本次确认
计划仅允许restore_ingress_budget，把同一对象、同一模型当前入口预算从20恢复到基线80。列出环境、对象、模型、变更前修订、前后值和观察条件。
操作者点击“确认本次演练动作”，只生成与这一完整计划绑定的本页练习确认；不是生产权限、真实人员审批或独立真值审核。
确认后若对象、修订、动作、前后值、来源引用或观察条件改变，确认失效，变更前停止。确认只使用一次。

## 4. 执行与停止条件
执行前保存同一对象和模型的原入口预算及配置修订，只修改内存模型的ingress_budget并递增修订。
应用中断、主动停止、观察超出约定训练窗或观察中复发，进入回退；不继续执行其他动作。
本页没有网络、设备、数据库或平台业务动作接口，不能填写真实设备地址来扩展执行范围。

## 5. 具体回退与失败升级
回退前重新核对目标、模型与保存配置的绑定。备份缺失、对象变化或备份值无效，不写入其他对象，保持未恢复并升级。
回退只把同一内存对象的入口预算恢复为保存的20，递增修订，记录原修订和当前修订。
回退后逐值核对配置。配置恢复不等于业务恢复：故障预算20仍可能产生排队，因此本例回退成功后仍需复核，不关闭为成功。
若故障注入使回退失败，明确记录config_restored=false、service_recovered=false，进入未恢复升级状态。

## 6. 恢复验证与观察
每次观察生成下一训练窗，先服务历史排队，再服务当前请求；分别记录两类完成数，保留当前请求分母。
合格窗要求队列为0、当前窗完成数等于当前窗请求数、鉴权错误为0；本例连续2个合格窗且总观察不超过4个训练窗才结束练习。
正常例首个恢复窗仍有20个排队，不能提前完成；随后两个窗合格。这里的窗和数值只描述本模型，不套用统一“三个周期”或现场合格值。
观察期复发则回退并升级，不沿用之前的合格窗声明恢复。

## 7. 事件账本与交接
逐项记录场景、对象/模型、配置修订、采集、分支、计划、本页确认、变更、观察、停止、回退和结果。可导出JSON，不记录凭据或拟造真人身份。
每份记录保留generated_training_model来源；real_action_authorized、site_truth_verified、human_review_verified始终为false。
本草稿、来源身份和访问范围仍pending；没有入库、替换既有指南、发布或新模型外发授权。完成本例只说明内存训练状态机满足自身条件，不证明完整现场SOP或独立质量。
'''


def prepare():
    if OUTPUT.exists():raise ValueError('new exercise package only')
    binding=source_binding();REPORT.mkdir(parents=True,exist_ok=True)
    # Test before packaging and sealing, retaining machine-readable results.
    result=subprocess.run(['node','--test','--test-reporter=junit',str(ROOT/'tests/training/amf-exercise.test.cjs')],
        capture_output=True,text=True,encoding='utf-8',timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    (REPORT/'unit-tests.xml').write_text(result.stdout,encoding='utf-8')
    if result.returncode:raise RuntimeError('exercise behavior checks failed; no package created')
    OUTPUT.mkdir(parents=True)
    for name,source in ASSETS.items():shutil.copyfile(ROOT/source,OUTPUT/name)
    (OUTPUT/'source-binding.js').write_bytes(('window.AMF_SOURCE_BINDING='+json.dumps(binding,ensure_ascii=False)+';\n').encode('utf-8'))
    (OUTPUT/'sop-draft.md').write_bytes(sop_body().encode('utf-8'))
    draft=dict(id='TRAINING-SOP-AMF-001',filename='sop-draft.md',body_sha256=digest(OUTPUT/'sop-draft.md'),
        review_status='pending',license_status='pending',identity_registered=False,
        source_identity_proposal=dict(source_system='project_authored_training_sop',source_id='training-sop-amf-001',source_version='draft-r1'),
        access_proposal=dict(tenant_id='default',roles=['ops','admin'],visibility='internal',security_level='internal'),
        doc_type_proposal='sop',intended_use='local_in_memory_training_only',operational_sop_approved=False,
        manufacturer_document=None,site_values_supplied=False,outbound_authorized=False,reference=binding)
    write(OUTPUT/'review-package.json',dict(protocol='amf-sop-exercise-r1',entries=[draft],admission_ready=False,
        new_admissions=0,new_ingestions=0,new_staged=0,paid_calls=0,independent_denominator=0,
        engine_environment='in_memory_training',authenticates_human_reviewers=False,platform_actions_available=False))
    write(OUTPUT/'code-bindings.json',{name:digest(ROOT/name) for name in CODE})
    seal(OUTPUT);validate()
    print(json.dumps(dict(prepared=True,pending_sop_drafts=1,scenarios=8,new_admissions=0,paid_calls=0)))


def validate():
    verify_seal(OUTPUT)
    if read(OUTPUT/'code-bindings.json')!={name:digest(ROOT/name) for name in CODE}:raise ValueError('exercise implementation drift')
    package=read(OUTPUT/'review-package.json');entry=package['entries'][0]
    if entry['reference']!=source_binding() or digest(OUTPUT/'sop-draft.md')!=entry['body_sha256']:
        raise ValueError('exact training draft/source reference changed')
    if entry['review_status']!='pending' or package['admission_ready'] or entry['identity_registered'] or entry['operational_sop_approved']:
        raise ValueError('draft or rehearsal cannot approve knowledge or operational use')
    return package


if __name__=='__main__':prepare()
