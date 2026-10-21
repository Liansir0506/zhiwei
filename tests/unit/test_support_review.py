from copy import deepcopy
import hashlib
import pytest
from qa_core.quality.support_review import ACTIONS, audit_sops, quote, verify_quote, source_path


def fixture(tmp_path, title='AMF注册成功率下降', equipment='AMF'):
    name='sop_operation_001.md'
    text=f'# {title}处置 SOP 001\n适用：{equipment}/5G；模拟告警：ALM-TEST-01。\n\n'+ '\n\n'.join(
        f'步骤 {i}：执行{action}。针对原因待证记录对象、版本、时间和前后值；证据不足时停止并转人工复核。'
        for i,action in enumerate(ACTIONS,1))
    (tmp_path/name).write_text(text,encoding='utf-8')
    meta={name:dict(doc_type='sop',equipment_type=equipment,network_tech='5G',alarm_code='ALM-TEST-01',asset_id='a',
                   source_version='s1',tenant_id='default',allowed_roles=['ops'])}
    return name,text,meta


def test_complete_template_remains_non_executable_with_exact_evidence(tmp_path):
    name,text,meta=fixture(tmp_path)
    result=audit_sops(tmp_path,meta,'v1');entry=result['entries'][0]
    assert result['summary']['known_template_files']==1
    assert not entry['executable'] and not entry['human_semantic_approved']
    for finding in entry['findings']:
        if 'quote' in finding['evidence']:assert verify_quote(tmp_path/name,finding['evidence'])
    assert {f['role']:f['status'] for f in entry['findings']}['approval']=='absent_in_exact_full_body'
    assert result['families'][0]['approved_runtime_aliases']==[]


@pytest.mark.parametrize('change',[
    lambda t:t+'\n人工审批 回滚 停止',
    lambda t:t.replace('证据不足时停止并转人工复核','无需审批、不能回滚'),
    lambda t:t.replace('步骤 2：','步骤 3：'),
    lambda t:t.replace('针对原因待证','针对已确认故障',1),
    lambda t:t.replace('观察三个周期','观察60秒'),
    lambda t:t.replace('AMF/5G','AMF/4G'),
])
def test_changed_or_unknown_content_never_inherits_literal_decisions(tmp_path,change):
    name,text,meta=fixture(tmp_path);(tmp_path/name).write_text(change(text),encoding='utf-8')
    entry=audit_sops(tmp_path,meta,'v1')['entries'][0]
    assert not entry['recognized_template'] and not entry['executable']
    assert entry['findings']==[dict(role='full_source',status='unreviewed_changed_or_unknown_template')]


def test_title_network_conflict_is_visible_without_silent_repair(tmp_path):
    name,text,meta=fixture(tmp_path,title='LTE附着失败率升高',equipment='MME')
    before=(tmp_path/name).read_bytes();entry=audit_sops(tmp_path,meta,'v1')['entries'][0]
    assert any(f['role']=='network_applicability' for f in entry['findings'])
    assert before==(tmp_path/name).read_bytes()


def test_equal_bodies_do_not_authorize_aliases_or_equal_acl(tmp_path):
    name,text,meta=fixture(tmp_path);name2='sop_operation_002.md'
    (tmp_path/name2).write_text(text.replace('SOP 001','SOP 002'),encoding='utf-8')
    meta[name2]=deepcopy(meta[name]);meta[name2].update(asset_id='b',allowed_roles=['admin'])
    result=audit_sops(tmp_path,meta,'v1');family=result['families'][0]
    assert family['file_count']==2 and not family['all_metadata_equal']
    assert family['approved_runtime_aliases']==[]
    assert all(not e['runtime_alias_approved'] for e in result['entries'])


@pytest.mark.parametrize('network,equipment,title',[('4G','MME','LTE附着失败率升高'),('传输','Clock','时钟失锁'),('无线','Power','电源欠压'),('核心网','DNS','DNS解析延迟')])
def test_network_declaration_comes_from_its_metadata_not_a_global_5g_default(tmp_path,network,equipment,title):
    name,text,meta=fixture(tmp_path,title=title,equipment=equipment)
    meta[name]['network_tech']=network
    (tmp_path/name).write_text(text.replace('/5G；','/'+network+'；'),encoding='utf-8')
    result=audit_sops(tmp_path,meta,'v1')
    assert result['summary']['known_template_files']==1
    assert result['summary']['network_applicability_conflicts']==0


@pytest.mark.parametrize('name',['../sop_operation_001.md','nested/sop_operation_001.md','x.md',None])
def test_unsafe_or_unrecognized_source_names_rejected(tmp_path,name):
    with pytest.raises(ValueError):source_path(tmp_path,name)


def test_quote_binding_rejects_changed_bytes_and_line(tmp_path):
    name,text,meta=fixture(tmp_path);sha=hashlib.sha256((tmp_path/name).read_bytes()).hexdigest()
    evidence=quote(text,'步骤 6：',sha);assert verify_quote(tmp_path/name,evidence)
    changed=deepcopy(evidence);changed['line']+=1
    with pytest.raises(ValueError):verify_quote(tmp_path/name,changed)
    (tmp_path/name).write_text(text+'\n',encoding='utf-8')
    with pytest.raises(ValueError):verify_quote(tmp_path/name,evidence)


def test_ambiguous_quote_not_accepted(tmp_path):
    with pytest.raises(ValueError):quote('相同\n相同','相同','checksum')
