"""Internal immutable targets. Omitted and empty constraints have distinct meanings."""
from dataclasses import dataclass
from qa_core.retrieval.filters import _literal


class RecordUnavailable(ValueError):
    def __init__(self, reason='record_incomplete'):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class EvidenceConstraint:
    pairs: tuple[tuple[str, str], ...]
    triples: tuple[tuple[str, str, str], ...] | None = None
    revision: str = ''
    checksums: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self):
        for name, width in [('pairs', 2), ('triples', 3), ('checksums', 3)]:
            rows = getattr(self, name)
            if rows is None and name == 'triples':
                continue
            if len(rows) > 20 or any(len(row) != width or any(not isinstance(x, str) or not x or len(x) > 512 for x in row) for row in rows):
                raise ValueError('incomplete or excessive evidence targets')
            object.__setattr__(self, name, tuple(sorted(set(tuple(row) for row in rows))))
        if self.triples is not None and any(row[:2] not in self.pairs for row in self.triples):
            raise ValueError('record target outside asset targets')

    @property
    def exact(self):
        return self.triples is not None

    @property
    def empty(self):
        return not (self.triples if self.exact else self.pairs)

    def signature(self):
        return ['relation-constraint-v1', self.pairs, self.triples, self.revision, self.checksums]

    def expression(self):
        rows = self.triples if self.exact else self.pairs
        names = ('asset_id', 'source_version', 'ticket_id') if self.exact else ('asset_id', 'source_version')
        if not rows:
            return 'id == ""'
        return '(' + ' or '.join('(' + ' and '.join(f'{name} == "{_literal(value)}"' for name, value in zip(names, row)) + ')' for row in rows) + ')'

    def allows(self, doc):
        pair = (doc.asset_id, doc.source_version)
        return pair in self.pairs and (not self.exact or (*pair, doc.ticket_id) in self.triples)

    def checksum(self, pair):
        return next((sha for asset, revision, sha in self.checksums if (asset, revision) == pair), None)
