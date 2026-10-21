import json
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from qa_core.ingest.binding import version_binding


class VersionManifestDao:
    def __init__(self, dsn):
        self.engine = sa.create_engine(dsn, pool_pre_ping=True)

    def close(self):
        self.engine.dispose()

    def get(self, version):
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sa.text("SELECT binding_json,snapshot_json FROM kb_version_manifests WHERE version=:v"), {"v": version}).first()
        except OperationalError as exc:
            # Read-only legacy compatibility, not a write/migration fallback.
            code = getattr(exc.orig, "args", [None])[0]
            if code == 1146 or "no such table: kb_version_manifests" in str(exc.orig):
                return None
            raise
        if row is None:
            return None
        return {"binding": json.loads(row[0]) if isinstance(row[0], str) else row[0],
                "snapshot": json.loads(row[1]) if isinstance(row[1], str) else row[1]}

    def bind(self, version, settings):
        binding = version_binding(settings)
        with self.engine.begin() as conn:
            row = conn.execute(sa.text("SELECT binding_json FROM kb_version_manifests WHERE version=:v FOR UPDATE"), {"v": version}).first()
            if row:
                prior = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                if prior != binding:
                    raise ValueError("候选版本的集合或处理配置绑定不一致，请使用新版本")
            else:
                conn.execute(sa.text("INSERT INTO kb_version_manifests(version,binding_json) VALUES(:v,:b)"), {"v": version, "b": json.dumps(binding)})
        return binding

    def record_snapshot(self, version, snapshot):
        with self.engine.begin() as conn:
            conn.execute(sa.text("UPDATE kb_version_manifests SET snapshot_json=:s WHERE version=:v"), {"v": version, "s": json.dumps(snapshot)})


def bind_candidate(settings, version):
    dao = VersionManifestDao(settings.mysql_dsn)
    try:
        return dao.bind(version, settings)
    finally:
        dao.close()


def verify_read_binding(settings, version):
    dao = VersionManifestDao(settings.mysql_dsn)
    try:
        manifest = dao.get(version)
        if manifest and manifest["binding"] != version_binding(settings):
            raise ValueError("当前读取配置不匹配知识版本绑定")
        return manifest
    finally:
        dao.close()
