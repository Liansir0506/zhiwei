"""Authorization-before-pagination against real SQLite rows, no external services."""
import pytest
import sqlalchemy as sa
from types import SimpleNamespace
from fastapi import HTTPException
from qa_core.api.auth import AuthContext
from qa_core.application.business_workspace import BusinessWorkspace, selection

@pytest.fixture
def service(tmp_path):
    result=BusinessWorkspace(SimpleNamespace(mysql_dsn='sqlite:///'+str(tmp_path/'domain.db')))
    with result.dao.engine.begin() as conn:
        for ddl in [
            'CREATE TABLE devices(device_id TEXT,tenant_id TEXT,dept_id TEXT,device_type TEXT,name TEXT,attributes_json TEXT)',
            'CREATE TABLE alarms(alarm_id TEXT,tenant_id TEXT,device_id TEXT,alarm_code TEXT,payload_json TEXT)',
            'CREATE TABLE work_orders(ticket_id TEXT,tenant_id TEXT,device_id TEXT,alarm_id TEXT,dept_id TEXT,title TEXT,assignee_user_id TEXT)',
            'CREATE TABLE change_records(change_id TEXT,tenant_id TEXT,device_id TEXT,ticket_id TEXT,dept_id TEXT,title TEXT,operator_user_id TEXT)',
            'CREATE TABLE adapter_sync_state(adapter_name TEXT,tenant_id TEXT,sync_status TEXT,simulation INT,items_synced INT,last_synced_at TEXT,last_error_code TEXT,cursor_value TEXT,last_error_message TEXT)']:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql("INSERT INTO devices VALUES ('d0','other','net','AMF','foreign','secret'),('d1','t','hidden','AMF','hidden','secret'),('d2','t','net','AMF','allowed','secret'),('d3','t','net','BBU','wrong type','secret'),('d4','t','net','AMF','allowed next','secret')")
        conn.exec_driver_sql("INSERT INTO alarms VALUES ('a2','t','d2','ALM-AMF-REG','secret'),('foreign','other','d2','ALM','secret'),('orphan','t','missing','ALM','secret')")
        conn.exec_driver_sql("INSERT INTO work_orders VALUES ('w2','t','d2','a2','net','good','secret'),('cross','t','d2','foreign','net','cross tenant','secret'),('hidden','t','d2','a2','hidden','private department','secret')")
        conn.exec_driver_sql("INSERT INTO change_records VALUES ('c2','t','d2','w2','net','good','secret'),('cross','t','d2','cross','net','bad ticket','secret'),('hidden','t','d2','hidden','net','private ticket','secret')")
        conn.exec_driver_sql("INSERT INTO adapter_sync_state VALUES ('cmdb','t','degraded',1,7,NULL,'upstream_timeout','private cursor','private upstream URL')")
    yield result
    result.close()

def ctx(**kw):return AuthContext('user',kw.pop('roles',['ops']),'api_key',kw.pop('tenant_id','t'),**kw)

def test_scope_before_cursor(service):
    context=ctx(dept_ids=['net'],device_types=['AMF'])
    first=service.listing('devices',context,limit=1)
    assert [r['device_id'] for r in first['items']]==['d2'] and first['next_cursor']=='d2'
    second=service.listing('devices',context,after=first['next_cursor'],limit=1)
    assert [r['device_id'] for r in second['items']]==['d4'] and second['next_cursor'] is None
    assert 'attributes_json' not in first['items'][0]

@pytest.mark.parametrize('kind,id_field,expected',[('alarms','alarm_id','a2'),('tickets','ticket_id','w2'),('changes','change_id','c2')])
def test_links_and_private_projection(service,kind,id_field,expected):
    data=service.listing(kind,ctx(dept_ids=['net'],device_types=['AMF']))
    assert [r[id_field] for r in data['items']]==[expected]
    assert not any({'attributes_json','payload_json','assignee_user_id','operator_user_id'}&set(row) for row in data['items'])

@pytest.mark.parametrize('scope',[{'dept_ids':['nope']},{'device_types':['SGW']},{'tenant_id':'empty'}])
def test_empty_has_no_other_scope_counts(service,scope):
    data=service.listing('devices',ctx(**scope))
    assert not data['items'] and data['next_cursor'] is None and 'total' not in data

@pytest.mark.parametrize('scope',[{'roles':['guest']},{'security_levels':['public']}])
def test_deny_public(service,scope):
    with pytest.raises(HTTPException) as error:service.listing('devices',ctx(**scope))
    assert error.value.status_code==403

def test_integrations_no_raw_errors_or_cursor(service):
    data=service.integrations(ctx())
    assert len(data['adapters'])==6 and data['read_only']
    assert data['adapters'][0]['sync_status']=='degraded'
    assert data['adapters'][1]['sync_status']=='never_synced'
    assert 'private' not in str(data) and all(not r['live_connection_checked'] for r in data['adapters'])

@pytest.mark.parametrize('scope',[{'dept_ids':['net']},{'device_types':['AMF']}])
def test_integrations_deny_scope_totals(service,scope):
    with pytest.raises(HTTPException) as error:service.integrations(ctx(**scope))
    assert error.value.status_code==403

def test_identifier_injection_not_table_name(service):
    with pytest.raises(HTTPException):service.listing('devices;DROP TABLE devices',ctx())
    sql,params=selection('devices',ctx(),query="%' OR 1=1 --")
    assert params['query']=="%' OR 1=1 --" and params['query'] not in str(sql)
    assert len(service.listing('devices',ctx())['items'])==4
