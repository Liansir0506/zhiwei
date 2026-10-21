"""入库记录与知识库版本 DAO（MySQL）。状态机见 docs/DATA_MODEL.md ingest_records。"""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


class KbDao:
    def __init__(self, dsn: str):
        self.engine = create_engine(dsn, pool_pre_ping=True)

    # ---- ingest_records ----

    def file_done(self, file_hash: str, kb_version: str, asset_id: str | None = None) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(text(
                "SELECT asset_id,status FROM ingest_records WHERE file_hash=:h AND kb_version=:v"
            ), {"h": file_hash, "v": kb_version}).first()
        if row is not None and asset_id is not None and row[0] != asset_id:
            raise ValueError("同版本相同正文已绑定其他或未知资产，拒绝跨资产幂等；请使用独立候选版本")
        return row is not None and row[1] == "done"

    def get_records_by_filename(self, filename: str, kb_version: str) -> list[dict]:
        """同名文件在本版本的全部入库记录（不同 hash 可有多条）。"""
        with self.engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT file_hash, doc_type, status, asset_id FROM ingest_records "
                "WHERE filename=:f AND kb_version=:v"),
                {"f": filename, "v": kb_version}).mappings().all()
        return [dict(r) for r in rows]

    def delete_record(self, file_hash: str, kb_version: str) -> int:
        with self.engine.begin() as conn:
            return conn.execute(text(
                "DELETE FROM ingest_records WHERE file_hash=:h AND kb_version=:v"),
                {"h": file_hash, "v": kb_version}).rowcount

    def upsert_record(self, file_hash: str, filename: str, doc_type: str, kb_version: str,
                      asset_id: str | None = None) -> None:
        params = {"h": file_hash, "f": filename, "t": doc_type,
                  "v": kb_version, "asset": asset_id}
        try:
            with self.engine.begin() as conn:
                conn.execute(text(
                    """INSERT INTO ingest_records
                           (file_hash, filename, asset_id, doc_type, kb_version, status)
                       VALUES (:h, :f, :asset, :t, :v, 'pending')
                       ON DUPLICATE KEY UPDATE asset_id=VALUES(asset_id), status='pending',
                         error_msg=NULL, chunk_count=0"""), params)
        except OperationalError:  # 滚动升级兼容尚无 asset_id 的旧库
            with self.engine.begin() as conn:
                conn.execute(text(
                    """INSERT INTO ingest_records (file_hash, filename, doc_type, kb_version, status)
                       VALUES (:h, :f, :t, :v, 'pending')
                       ON DUPLICATE KEY UPDATE status='pending', error_msg=NULL, chunk_count=0"""),
                    params)

    def set_status(self, file_hash: str, kb_version: str, status: str,
                   error_msg: str | None = None, chunk_count: int | None = None) -> None:
        assert status in ("pending", "parsed", "chunked", "embedded", "loaded", "done", "failed")
        with self.engine.begin() as conn:
            conn.execute(text(
                """UPDATE ingest_records SET status=:s,
                       error_msg=:e,
                       chunk_count=COALESCE(:c, chunk_count)
                   WHERE file_hash=:h AND kb_version=:v"""
            ), {"s": status, "e": error_msg, "c": chunk_count, "h": file_hash, "v": kb_version})

    def list_records(self, kb_version: str) -> list[dict]:
        with self.engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT file_hash, filename, doc_type, status, error_msg, chunk_count"
                " FROM ingest_records WHERE kb_version=:v ORDER BY id"
            ), {"v": kb_version}).mappings().all()
        return [dict(r) for r in rows]

    # ---- kb_versions ----

    def asset_chunk_count(self, asset_id: str, kb_version: str) -> int | None:
        """Authoritative completed-file size for source expansion; ambiguity denies."""
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT chunk_count FROM ingest_records WHERE asset_id=:asset "
                "AND kb_version=:version AND status='done'"),
                {"asset": asset_id, "version": kb_version}).all()
        return int(rows[0][0]) if len(rows) == 1 and rows[0][0] else None

    def ensure_version(self, kb_version: str, scenario: str = "telecom_ops") -> None:
        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT IGNORE INTO kb_versions (version, scenario, status) VALUES (:v, :s, 'STAGED')"
            ), {"v": kb_version, "s": scenario})

    def set_quality_report(self, kb_version: str, report_path: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE kb_versions SET quality_report_path=:p WHERE version=:v"
            ), {"p": report_path, "v": kb_version})

    def get_version(self, kb_version: str) -> dict | None:
        with self.engine.begin() as conn:
            row = conn.execute(text(
                "SELECT version, scenario, status, quality_report_path FROM kb_versions WHERE version=:v"
            ), {"v": kb_version}).mappings().first()
        return dict(row) if row else None
