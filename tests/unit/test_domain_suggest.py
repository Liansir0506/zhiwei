"""Domain suggest endpoint: tenant scoping, wildcard escaping and role boundary."""
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qa_core.api import route_domain
from qa_core.api.auth import AuthContext, get_auth_context
from qa_core.storage.domain_dao import DomainDao


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        return self

    def mappings(self):
        return self

    def first(self):
        return None

    def all(self):
        return self.rows


class FakeEngine:
    def __init__(self, rows):
        self.rows = rows
        self.conn = FakeConn(rows)

    @contextmanager
    def begin(self):
        yield self.conn

    def dispose(self):
        pass


def fake_dao(monkeypatch, rows):
    monkeypatch.setattr(DomainDao, '__init__', lambda self, dsn: setattr(self, 'engine', FakeEngine(rows)))
    return DomainDao('ignored')


def test_suggest_escapes_like_wildcards_and_scopes_tenant(monkeypatch):
    dao = fake_dao(monkeypatch, [{'alarm_id': 'SIM-ALM-000001', 'alarm_code': 'ALM-AMF-REG',
                                  'severity': 'critical', 'status': 'open'}])
    items = dao.suggest('alarm', 'ALM-AMF%_', 'default', 8)
    assert items == [{'alarm_id': 'SIM-ALM-000001', 'alarm_code': 'ALM-AMF-REG',
                      'severity': 'critical', 'status': 'open'}]
    sql, params = dao.engine.conn.calls[0]
    assert 'ESCAPE' in sql and 'tenant_id=:tenant' in sql
    assert params['tenant'] == 'default' and params['limit'] == 8
    assert params['pattern'].startswith('%') and '\\%' in params['pattern'] and '\\_' in params['pattern']


def test_suggest_rejects_short_query_and_unknown_kind(monkeypatch):
    dao = fake_dao(monkeypatch, [])
    assert dao.suggest('alarm', 'A', 'default') == []
    assert dao.suggest('alarm', '   ', 'default') == []
    with pytest.raises(ValueError):
        dao.suggest('ticket', 'ALM-AMF-REG', 'default')


def make_client(monkeypatch, rows, roles):
    app = FastAPI()
    app.include_router(route_domain.router)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext('ops', roles, 'api_key')
    monkeypatch.setattr(route_domain, '_dao', lambda: fake_dao(monkeypatch, rows))
    return TestClient(app)


def test_suggest_endpoint_returns_items(monkeypatch):
    client = make_client(monkeypatch, [{'alarm_id': 'SIM-ALM-000001', 'alarm_code': 'ALM-AMF-REG',
                                        'severity': 'critical', 'status': 'open'}], ['ops'])
    response = client.get('/domain/suggest', params={'kind': 'alarm', 'q': 'ALM-AMF'})
    assert response.status_code == 200
    body = response.json()
    assert body['kind'] == 'alarm' and body['items'][0]['alarm_id'] == 'SIM-ALM-000001'


def test_suggest_endpoint_requires_role(monkeypatch):
    client = make_client(monkeypatch, [], ['guest'])
    response = client.get('/domain/suggest', params={'kind': 'alarm', 'q': 'ALM-AMF'})
    assert response.status_code == 403


def test_suggest_endpoint_rejects_unknown_kind(monkeypatch):
    client = make_client(monkeypatch, [], ['ops'])
    response = client.get('/domain/suggest', params={'kind': 'ticket', 'q': 'ALM-AMF'})
    assert response.status_code == 422
