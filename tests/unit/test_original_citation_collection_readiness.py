"""Recovering Milvus channels must not bypass gate checks or retry permanent errors."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pymilvus.exceptions import MilvusException

from scripts.prepare_original_citation_release import wait_readable_collections


def clock():
    state = SimpleNamespace(now=0)
    return state, lambda: state.now, lambda seconds: setattr(state, 'now', state.now + seconds)


def test_retry_only_recovering_channel_then_strong_read_every_collection():
    state, now, pause = clock()
    query = Mock(side_effect=[MilvusException(106, 'collection on recovering'), [{'id': 'ready'}], []])
    result = wait_readable_collections(SimpleNamespace(query=query), ['doc', 'faq', 'doc'], clock=now, pause=pause)
    assert result['passed'] and result['attempts'] == {'doc': 2, 'faq': 1}
    assert query.call_count == 3 and state.now == 2
    assert all(call.kwargs['consistency_level'] == 'Strong' and call.kwargs['timeout'] <= 5
               and call.kwargs['output_fields'] == ['id'] for call in query.call_args_list)


@pytest.mark.parametrize('code,message', [(106, 'collection missing'), (1, 'permission denied'),
    (101, 'collection not loaded'), (106, 'invalid schema'), (1100, 'invalid filter')])
def test_permanent_failure_is_not_retried_or_treated_as_ready(code, message):
    _, now, pause = clock()
    query = Mock(side_effect=MilvusException(code, message))
    with pytest.raises(MilvusException):
        wait_readable_collections(SimpleNamespace(query=query), ['doc'], clock=now, pause=pause)
    assert query.call_count == 1


def test_recovery_has_a_total_deadline_and_never_falls_through():
    state, now, pause = clock()
    query = Mock(side_effect=MilvusException(106, 'do not found any channel in target'))
    with pytest.raises(TimeoutError, match='doc'):
        wait_readable_collections(SimpleNamespace(query=query), ['doc'], timeout=5, clock=now, pause=pause)
    assert state.now == 5 and query.call_count == 3


def test_rpc_latency_counts_toward_total_deadline():
    state, now, pause = clock()
    def query(**kwargs):
        state.now += kwargs['timeout']
        raise MilvusException(106, 'collection on recovering')
    with pytest.raises(TimeoutError):
        wait_readable_collections(SimpleNamespace(query=query), ['doc', 'faq'], timeout=3, clock=now, pause=pause)
    assert state.now == 3


def test_gate_closing_shared_sdk_alias_cannot_poison_legacy_vector_reads(monkeypatch):
    import pymilvus
    from scripts import prepare_original_citation_release as release
    from scripts import migrate_legacy_collections as migration
    clients = []
    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
        def query(self, **kwargs):
            assert not self.closed
            return []
        def close(self):
            self.closed = True
    def gate(*args):
        # Simulate the owned verifier closing all same-URI aliases.
        assert all(client.closed for client in clients)
        for client in clients:
            client.closed = True
        return {'passed': True}
    def rows(client, name):
        assert not client.closed, 'query attempted on SDK alias closed by gate'
        return [{'id': name}]
    monkeypatch.setattr(pymilvus, 'MilvusClient', Client)
    monkeypatch.setattr(release, 'verify_publication', gate)
    monkeypatch.setattr(migration, 'read_rows', rows)
    monkeypatch.setattr(migration, 'fingerprint', lambda rows: rows[0]['id'])
    settings = SimpleNamespace(milvus_uri='http://fixture:19530', doc_collection='new_doc', faq_collection='new_faq')
    readiness, report, vectors = release.inspect_live_collections(settings, ['old_doc', 'old_faq'], 'fixture-gate.json')
    assert readiness['passed'] and report['passed'] and vectors['old_doc']['sha256'] == 'old_doc'
    assert len(clients) == 2 and all(client.closed for client in clients)
