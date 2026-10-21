"""Own disposable MySQL fixture. Real domain SELECT + explicit fake evidence."""
import copy
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from qa_core.api.auth import AuthContext
from qa_core.application.domain_context_service import DomainContextService
from qa_core.application.agent_diagnose import diagnose, RunControl
from qa_core.application.agent_tools import AgentBackend, Binding
from qa_core.config.settings import get_settings
from qa_core.domain.models import EnterpriseSnapshot, Tenant, Department, Device, Alarm, WorkOrder, ChangeRecord, KnowledgeAsset, KnowledgeRelation, MonitoringMetric
from qa_core.schemas.agent import AgentRequest
from qa_core.schemas.evidence import evidence_of
from qa_core.schemas.retrieval import RetrievedDoc
from qa_core.storage.domain_dao import DomainDao
from qa_core.ingest.binding import version_binding
from qa_core.application.agent_tools import AgentStop, ReadTools
from scripts.migrate_domain_model import split_sql_statements


def test_domain_admission_fk_failure_rolls_back_all_prior_rows(isolated_domain):
    from scripts.execute_agent_domain_admission import insert_rows
    settings,dao=isolated_domain
    with pytest.raises(sa.exc.IntegrityError):
        with dao.engine.begin() as conn:
            insert_rows(conn,[('departments',[{'dept_id':'admission-new','tenant_id':'synthetic','name':'new'}]),
                ('devices',[{'device_id':'admission-invalid','tenant_id':'missing-tenant','name':'invalid','device_type':'AMF'}])])
    with dao.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM departments WHERE dept_id='admission-new'").scalar_one()==0


def test_domain_admission_conflict_does_not_replace_existing_row(isolated_domain):
    from scripts.execute_agent_domain_admission import insert_rows
    settings,dao=isolated_domain
    with pytest.raises(ValueError,match='ID conflict'):
        with dao.engine.begin() as conn:
            insert_rows(conn,[('departments',[{'dept_id':'admission-new','tenant_id':'synthetic','name':'new'},
                {'dept_id':'net','tenant_id':'synthetic','name':'must-not-overwrite'}])])
    with dao.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM departments WHERE dept_id='admission-new'").scalar_one()==0
        assert conn.exec_driver_sql("SELECT name FROM departments WHERE dept_id='net'").scalar_one()=='net'

def root_sql(sql):
    result = subprocess.run(['docker', 'exec', '-i', 'telecom-ops-rag-test-mysql-1', 'sh', '-c',
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'], input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('isolated test database setup/cleanup failed')


@pytest.fixture
def isolated_domain():
    base = get_settings()
    name = 'test_readonly_agent_' + uuid4().hex[:12]
    url = sa.engine.make_url(base.mysql_dsn)
    assert base.is_test and url.database == 'rag_kb_test' and url.port == 13306 and url.username == 'rag'
    assert re.fullmatch(r'test_readonly_agent_[a-f0-9]{12}', name)
    admin = sa.create_engine(url.set(database=None), pool_pre_ping=True)
    created = False
    dao = None
    try:
        root_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4; GRANT ALL ON `{name}`.* TO 'rag'@'%';")
        created = True
        settings = base.model_copy(update={'mysql_dsn': url.set(database=name).render_as_string(hide_password=False),
            'doc_collection': name + '_doc', 'faq_collection': name + '_faq', 'generation_enabled': False})
        dao = DomainDao(settings.mysql_dsn)
        with dao.engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE schema_migrations (version VARCHAR(64) PRIMARY KEY, description VARCHAR(255))')
            for sql in split_sql_statements(Path('docker/mysql/init/02_enterprise_domain.sql').read_text(encoding='utf-8')):
                conn.exec_driver_sql(sql)
            conn.exec_driver_sql('CREATE TABLE kb_versions (version VARCHAR(64) PRIMARY KEY, status VARCHAR(32))')
            conn.exec_driver_sql('CREATE TABLE kb_version_manifests (version VARCHAR(64) PRIMARY KEY, binding_json JSON, snapshot_json JSON)')
            conn.exec_driver_sql('CREATE TABLE knowledge_source_withdrawals (identity_hash CHAR(64), tenant_id VARCHAR(64))')
            security = split_sql_statements(Path('docker/mysql/init/03_security_governance.sql').read_text(encoding='utf-8'))
            conn.exec_driver_sql(next(sql for sql in security if sql.startswith('CREATE TABLE IF NOT EXISTS permission_audit')))
            conn.execute(sa.text("INSERT INTO kb_versions VALUES ('synthetic-v','ACTIVE')"))
            conn.execute(sa.text("INSERT INTO kb_version_manifests VALUES ('synthetic-v',:b,:s)"),
                {'b': json.dumps(version_binding(settings)), 's': json.dumps({'synthetic_fixture': True})})
        now = datetime(2026, 1, 1)
        snapshot = EnterpriseSnapshot(
            tenants=[Tenant(tenant_id='synthetic', name='Synthetic Agent Fixture')],
            departments=[Department(dept_id='net', tenant_id='synthetic', name='net'), Department(dept_id='other', tenant_id='synthetic', name='other')],
            devices=[Device(device_id='d', tenant_id='synthetic', name='synthetic AMF', dept_id='net', device_type='AMF'),
                     Device(device_id='d-other', tenant_id='synthetic', name='hidden', dept_id='other', device_type='AMF')],
            alarms=[Alarm(alarm_id='a', tenant_id='synthetic', device_id='d', alarm_code='ALM-AMF-REG', source_system='synthetic-fixture', occurred_at=now),
                    Alarm(alarm_id='orphan', tenant_id='synthetic', alarm_code='ALM-AMF-REG', source_system='synthetic-fixture', occurred_at=now)],
            monitoring_metrics=[MonitoringMetric(observation_id='m', tenant_id='synthetic', device_id='d', metric_name='registration_failure_rate', metric_value=4.0, sampled_at=now, source_system='synthetic-fixture')],
            work_orders=[WorkOrder(ticket_id=f'w-{i:02}', tenant_id='synthetic', device_id='d', alarm_id='a', dept_id='net' if i else 'other', title='synthetic-ticket', source_system='synthetic-fixture', opened_at=now) for i in range(25)],
            changes=[ChangeRecord(change_id='allowed-change', tenant_id='synthetic', device_id='d', dept_id='net', ticket_id='w-01', title='synthetic-change', change_type='software', source_system='synthetic-fixture'),
                     ChangeRecord(change_id='hidden-change', tenant_id='synthetic', device_id='d', dept_id='net', ticket_id='w-00', title='must-not-leak', change_type='software', source_system='synthetic-fixture')],
            knowledge_assets=[KnowledgeAsset(asset_id='visible', tenant_id='synthetic', dept_id='net', filename='synthetic.md', doc_type='manual', source_system='synthetic-fixture', status='active', kb_version='synthetic-v', source_version='r', checksum='a'*64),
                              KnowledgeAsset(asset_id='hidden-via-ticket', tenant_id='synthetic', dept_id='net', filename='must-not-leak.md', doc_type='manual', source_system='synthetic-fixture', status='active')],
            knowledge_relations=[KnowledgeRelation(asset_id='visible', relation_type='device', relation_id='d'),
                                 KnowledgeRelation(asset_id='hidden-via-ticket', relation_type='work_order', relation_id='w-00')])
        dao.upsert_snapshot(snapshot)
        yield settings, dao
    finally:
        if dao:
            dao.engine.dispose()
        if created:
            assert name.startswith('test_readonly_agent_') and len(name) == len('test_readonly_agent_') + 12
            root_sql(f"DROP DATABASE `{name}`; REVOKE ALL ON `{name}`.* FROM 'rag'@'%';")
        with admin.connect() as conn:
            assert conn.execute(sa.text('SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:n'), {'n': name}).scalar_one() == 0
        admin.dispose()


@pytest.mark.integration
def test_real_domain_filters_limits_relations_and_mock_evidence_loop(isolated_domain):
    settings, dao = isolated_domain
    ctx = AuthContext('synthetic-user', ['ops'], 'api_key', 'synthetic', ['net'], ['AMF'])
    result = DomainContextService(dao).read('alarm', 'a', ctx)
    assert result['monitoring'][0]['observation_id'] == 'm'
    assert len(result['work_orders']) == 20
    assert all(row['dept_id'] == 'net' for row in result['work_orders'])
    assert [r['change_id'] for r in result['changes']] == ['allowed-change']
    assert [r['asset_id'] for r in result['knowledge']] == ['visible']
    assert 'must-not-leak' not in json.dumps(result, default=str)
    backend = AgentBackend(settings, ctx, lambda: ctx, RunControl())
    evidence = evidence_of(RetrievedDoc('synthetic-chunk', 'ALM-AMF-REG AMF 注册失败原因：核对认证数据与版本。', .9,
                                      asset_id='visible', kb_version='synthetic-v', source_version='r'))
    backend.search = lambda *a: {'status': 'EVIDENCE_FOUND', 'evidence': [evidence]}
    backend.source = lambda token: copy.deepcopy(evidence)
    answer = diagnose(AgentRequest(query='注册失败原因', alarm_id='a', device_id='d'), settings, ctx, lambda: ctx, backend=backend)
    assert answer['stop_reason']=='relationship_scope_incomplete' and not answer['sources']
    with dao.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM work_orders WHERE ticket_id NOT IN ('w-00','w-01')")
    answer = diagnose(AgentRequest(query='注册失败原因', alarm_id='a', device_id='d'), settings, ctx, lambda: ctx, backend=backend)
    assert answer['status'] == 'ANSWERED' and answer['observations'] and answer['sources']
    assert all(observation['synthetic'] for observation in answer['observations'])
    assert answer['budget']['calls_used'] == 4
    with dao.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM permission_audit WHERE actor='synthetic-user' AND action='agent_diagnose'")).scalar_one() >= 6
    for body in [AgentRequest(query='注册失败原因', alarm_id='orphan'), AgentRequest(query='注册失败原因', device_id='d-other')]:
        denied = diagnose(body, settings, ctx, lambda: ctx, backend=backend)
        assert denied['stop_reason'] == 'entity_unavailable' and not denied['observations'] and not denied['sources']
    foreign = AuthContext('synthetic-user', ['admin'], 'api_key', 'default')
    assert DomainContextService(dao).read('alarm', 'a', foreign) is None


@pytest.mark.integration
def test_real_authorized_edges_and_midrun_relation_changes(isolated_domain):
    from qa_core.application.agent_relations import select_targets
    from qa_core.application.agent_tools import digest
    settings, dao = isolated_domain
    ctx=AuthContext('synthetic-user',['ops'],'api_key','synthetic',['net'],['AMF'])
    with dao.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM work_orders WHERE ticket_id NOT IN ('w-00','w-01')")
        conn.exec_driver_sql("INSERT INTO knowledge_relations (asset_id,relation_type,relation_id) VALUES ('visible','work_order','w-01')")
    context=DomainContextService(dao).read('alarm','a',ctx,include_relations=True)
    assert {(e['asset_id'],e['relation_type'],e['relation_id']) for e in context['_relations']}=={
        ('visible','device','d'),('visible','work_order','w-01')}
    assert select_targets(context,'synthetic-v','核对关联工单 w-01 原文',digest(context)).triples==(('visible','r','w-01'),)
    assert '_relations' not in DomainContextService(dao).read('alarm','a',ctx)
    backend=AgentBackend(settings,ctx,lambda:ctx,RunControl())
    def changed(*args):
        with dao.engine.begin() as conn: conn.exec_driver_sql("DELETE FROM knowledge_relations WHERE asset_id='visible'")
        return {'status':'EVIDENCE_FOUND','evidence':[evidence_of(RetrievedDoc('c','注册失败原因记录：只读证据内容。',0,
            asset_id='visible',source_version='r',kb_version='synthetic-v'))]}
    backend.search=changed
    result=diagnose(AgentRequest(query='注册失败原因',alarm_id='a'),settings,ctx,lambda:ctx,backend=backend)
    assert result['stop_reason']=='relation_changed' and not result['observations'] and not result['sources']


@pytest.mark.integration
def test_real_record_location_part_ledger_preserves_completeness(isolated_domain):
    from qa_core.storage.location_dao import LocationDao
    settings,dao=isolated_domain
    locations=LocationDao(dao.engine);locations.ensure_schema()
    docs=[RetrievedDoc(str(i),'现象：受控临时记录片段'+str(i),0,tenant_id='synthetic',kb_version='synthetic-v',
        asset_id='visible',source_version='r') for i in range(3)]
    loc={'format':'csv','file_sha256':'a'*64,'matches':[[{'kind':'csv_record','record':1}]],'ambiguous':False}
    for doc in docs: locations.save(doc,loc)
    assert locations.record_chunk_count('a'*64,1)==3
    with dao.engine.begin() as conn:
        conn.execute(sa.text('DELETE FROM evidence_locations WHERE identity_hash=:id'),{'id':locations.identity(docs[0])})
    assert locations.record_chunk_count('a'*64,1)==2


@pytest.mark.integration
@pytest.mark.parametrize('change',['expired','retired','other_release'])
def test_real_unusable_related_asset_never_falls_back_to_global_search(isolated_domain,change):
    settings,dao=isolated_domain
    ctx=AuthContext('synthetic-user',['ops'],'api_key','synthetic',['net'],['AMF'])
    with dao.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM work_orders WHERE ticket_id NOT IN ('w-00','w-01')")
        if change=='expired': conn.exec_driver_sql("UPDATE knowledge_assets SET effective_to='2020-01-01' WHERE asset_id='visible'")
        if change=='retired': conn.exec_driver_sql("UPDATE knowledge_assets SET status='retired' WHERE asset_id='visible'")
        if change=='other_release': conn.exec_driver_sql("UPDATE knowledge_assets SET kb_version='other-v' WHERE asset_id='visible'")
    backend=AgentBackend(settings,ctx,lambda:ctx,RunControl())
    from unittest.mock import Mock
    backend.search=Mock(side_effect=AssertionError('global fallback forbidden'))
    result=diagnose(AgentRequest(query='注册失败原因',alarm_id='a'),settings,ctx,lambda:ctx,backend=backend)
    assert result['stop_reason']=='related_evidence_unavailable' and not result['sources'] and not result['observations']
    backend.search.assert_not_called()


@pytest.mark.integration
def test_real_binding_rejects_nonactive_manifest_and_ledger_changes(isolated_domain):
    settings, dao = isolated_domain
    ctx = AuthContext('synthetic-user', ['ops'], 'api_key', 'synthetic', ['net'], ['AMF'])
    backend = AgentBackend(settings, ctx, lambda: ctx, RunControl())
    binding = backend.binding()
    tools = ReadTools(backend, binding, RunControl(), 'synthetic-lifecycle')
    for status in ['STAGED', 'ARCHIVED']:
        with dao.engine.begin() as conn:
            conn.execute(sa.text('UPDATE kb_versions SET status=:s'), {'s': status})
        with pytest.raises(AgentStop, match='binding_changed'):
            tools.guard()
    with dao.engine.begin() as conn:
        conn.execute(sa.text("UPDATE kb_versions SET status='ACTIVE'"))
        conn.execute(sa.text('UPDATE kb_version_manifests SET snapshot_json=:s'), {'s': json.dumps({'changed_fixture': True})})
    with pytest.raises(AgentStop, match='binding_changed'):
        tools.guard()
    fresh = ReadTools(backend, backend.binding(), RunControl(), 'synthetic-ledger')
    with dao.engine.begin() as conn:
        conn.execute(sa.text('INSERT INTO knowledge_source_withdrawals VALUES (:h,:t)'), {'h': 'a'*64, 't': 'synthetic'})
    with pytest.raises(AgentStop, match='binding_changed'):
        fresh.guard()
    with dao.engine.begin() as conn:
        conn.execute(sa.text('UPDATE kb_version_manifests SET binding_json=:b'), {'b': json.dumps({'invalid': True})})
    with pytest.raises(AgentStop, match='binding_changed'):
        backend.binding()
