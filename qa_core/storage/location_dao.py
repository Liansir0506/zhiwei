"""Small optional location side table; content/tenant/version bound, no raw files."""
import hashlib
import json
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


class LocationDao:
    def __init__(self, engine):
        self.engine = engine

    def ensure_schema(self):
        with self.engine.begin() as conn:
            conn.execute(text("""CREATE TABLE IF NOT EXISTS evidence_locations (
                identity_hash CHAR(64) PRIMARY KEY,
                location_json JSON NOT NULL
            )"""))

    @staticmethod
    def identity(doc):
        body = (json.dumps([doc.text, doc.answer], ensure_ascii=False)
                if doc.doc_type == "faq" else doc.text)
        value = [doc.tenant_id, doc.kb_version, doc.id, doc.asset_id, doc.source_version,
                 hashlib.sha256(body.encode()).hexdigest()]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()

    def save(self, doc, location):
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO evidence_locations (identity_hash, location_json) "
                "VALUES (:id, :location) ON DUPLICATE KEY UPDATE location_json=:location"),
                {"id": self.identity(doc), "location": json.dumps(location, ensure_ascii=False)})

    def get(self, doc):
        try:
            with self.engine.connect() as conn:
                value = conn.execute(text("SELECT location_json FROM evidence_locations WHERE identity_hash=:id"),
                                     {"id": self.identity(doc)}).scalar()
        except OperationalError as exc:
            if getattr(exc.orig, "args", (None,))[0] == 1146:
                return None  # Old installations have no location table yet.
            raise
        return json.loads(value) if isinstance(value, str) else value

    def record_chunk_count(self, checksum, record):
        """Conservative persisted part count; repeated-version locations deny ambiguity.

        The existing ledger has opaque identity hashes, so duplicate versions may
        cause refusal. It must never infer completeness from returned vector rows.
        """
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM evidence_locations "
                "WHERE JSON_UNQUOTE(JSON_EXTRACT(location_json,'$.file_sha256'))=:sha "
                "AND JSON_EXTRACT(location_json,'$.matches[0][0].record')=:record"),
                {'sha': checksum, 'record': record}).scalar_one()
