"""Version review and fresh gates. Original serving-profile changes stay blocked.

Engineering review inherits exact source approvals; it never grants admission.
Pointer publication is enabled only for an owned disposable test namespace.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import csv
import json
from pathlib import Path
import re
import threading
import time
import uuid
import sqlalchemy as sa
from fastapi import HTTPException
from qa_core.ingest.binding import digest, version_binding
from qa_core.ingest.write_guard import candidate_write, LOCK_NAME
from qa_core.quality import publication
from qa_core.storage.version_manifest import verify_read_binding

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT / '.runtime/version-workspace'
GAPS = ['独立质量：未暴露样本与人工真值尚未冻结，分母0，未通过',
        'SOP支持性：200份既有SOP缺少安全控制，结构门禁不证明可执行',
        '设备资料：厂家、型号、软件版本和阈值依据未提供；仅通用取证']

def now(): return datetime.now(timezone.utc).isoformat()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class Workspace:
    def __init__(self, settings, root=ROOT, *, sandbox=False):
        self.settings, self.root, self.sandbox = settings, Path(root).absolute(), sandbox
        if self.root.resolve() != self.root or not self.root.is_relative_to(ROOT.absolute()):
            raise ValueError('版本工作区路径越界或包含链接')
        if sandbox:
            url = sa.engine.make_url(settings.mysql_dsn)
            if (not re.fullmatch(r'test_version_flow_[a-f0-9]{12}', url.database or '')
                or url.port != 13306 or settings.milvus_uri != 'http://127.0.0.1:29530'
                or (settings.doc_collection, settings.faq_collection) != (url.database+'_doc', url.database+'_faq')
                or settings.generation_enabled or settings.agent_enabled or not settings.auth_required):
                raise ValueError('发布演练只允许自有隔离测试库及集合，强鉴权、模型关闭')
        self.lock = threading.RLock()

    def path(self, *parts):
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root) or self.root.resolve() != self.root:
            raise ValueError('工作区文件越界或包含链接')
        return path

    def write(self, category, record):
        target = self.path(category, record['id']+'.json')
        with self.lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(target)
        return record

    def read(self, category, ident):
        if not re.fullmatch(r'[a-f0-9]{32}', ident): raise HTTPException(404, '记录不存在')
        with self.lock:
            try: result = json.loads(self.path(category, ident+'.json').read_text(encoding='utf-8'))
            except FileNotFoundError: raise HTTPException(404, '记录不存在') from None
        if result.get('id') != ident: raise ValueError('工作区记录身份变化')
        return result

    @contextmanager
    def engine(self):
        engine = sa.create_engine(self.settings.mysql_dsn, pool_pre_ping=True)
        try: yield engine
        finally: engine.dispose()

    def versions(self):
        with self.engine() as engine, engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(sa.text('SELECT version,status,published_at,activated_by '
                'FROM kb_versions ORDER BY published_at DESC,version DESC')).mappings()]
        return {'versions':rows, 'scope':'当前库版本状态；有记录不代表门禁或独立质量通过',
                'gaps':GAPS, 'execution_mode':'disposable_sandbox' if self.sandbox else 'review_only',
                'runtime_note':'原8010绑定具体批准的完整服务配置；禁止仅改ACTIVE指针。需完整候选、具体发布批准与服务配置交接。'}

    def status(self, version):
        publication.validate_version(version)
        with self.engine() as engine, engine.connect() as conn:
            row = conn.execute(sa.text('SELECT status,quality_report_path FROM kb_versions WHERE version=:v'), {'v':version}).first()
        if not row: raise HTTPException(404, '版本不存在')
        return row

    @contextmanager
    def client(self):
        # Installed PyMilvus reuses an endpoint alias by default; closing a probe
        # would close another request's channel. These clients own unique aliases.
        from pymilvus import MilvusClient
        client=MilvusClient(uri=self.settings.milvus_uri,timeout=30,alias='version-workspace-'+uuid.uuid4().hex)
        try:yield client
        finally:client.close()

    def snapshot(self,version):
        with self.client() as client:return publication.capture_snapshot(self.settings,version,client)

    def verify_publication(self,version,path):
        selected=self.settings.model_copy(update={'reports_dir':str(self.path('quality'))})
        manifest=verify_read_binding(selected,version)
        if not manifest:raise ValueError('无持久化版本绑定')
        report=publication.load_bound_report(selected,version,path)
        if report.get('profile'):raise ValueError('演练只接受完整五项门禁，不接受不适用范围的报告')
        if report['snapshot']!=manifest.get('snapshot') or self.snapshot(version)!=report['snapshot']:
            raise ValueError('实际快照与持久化门禁不匹配，禁止发布')
        from qa_core.security.withdrawals import assert_publishable_sources
        assert_publishable_sources(selected,report['snapshot']['sources'])
        return report

    def inspection(self, version):
        status, report_path = self.status(version)
        try: manifest = verify_read_binding(self.settings, version)
        except ValueError: raise HTTPException(409,'该版本绑定其他集合或处理配置；必须使用完整版本服务配置核验，不能仅改指针') from None
        blockers = []
        if not manifest: blockers.append('无持久化配置绑定，旧版本不能自动回填')
        with self.engine() as engine, engine.connect() as conn:
            ledger = [dict(r) for r in conn.execute(sa.text('SELECT filename,file_hash,asset_id,doc_type,status,chunk_count '
                'FROM ingest_records WHERE kb_version=:v ORDER BY filename'), {'v':version}).mappings()]
            jobs = [dict(r) for r in conn.execute(sa.text('SELECT job_id,status FROM ingest_jobs WHERE kb_version=:v'), {'v':version}).mappings()]
        if any(j['status'] not in ('success','failed','cancelled') for j in jobs): blockers.append('存在未完成入库任务')
        sources=[]
        # Source declarations come from the admitted immutable package, never from
        # untrusted upload metadata or an operator-supplied approval flag.
        data = Path(self.settings.data_dir)
        meta=json.loads((data/'raw/meta.json').read_text(encoding='utf-8'))
        mapping=json.loads((data/'source-map.json').read_text(encoding='utf-8'))
        from qa_core.ingest.source_identity import validate_mapping
        validate_mapping(data/'raw', mapping, meta)
        entries={r['filename']:r for r in mapping['entries']}
        seen=set()
        for row in ledger:
            name=row['filename']; declaration=meta.get(name); entry=entries.get(name)
            if Path(name).name != name or name in seen: raise ValueError('台账文件身份重复或越界')
            seen.add(name)
            admitted=bool(declaration and entry and row['status']=='done'
                and row['file_hash']==entry['sha256']==sha(data/'raw'/name)
                and row['asset_id']==declaration['asset_id'])
            if not admitted: blockers.append('台账未完成或具体来源准入不匹配：'+name)
            sources.append(dict(filename=name,sha256=row['file_hash'],asset_id=row['asset_id'],
                source_id=(declaration or {}).get('source_id'),chunks=row['chunk_count'],
                admission='inherited_approved' if admitted else 'blocked',
                scope={k:(declaration or {}).get(k) for k in ('tenant_id','role_scope','security_level')},
                approval=(entry or {}).get('approval',{})))
        quarantined=[]
        if version.startswith('local-work-') and not self.sandbox:
            from qa_core.ingest import workspace as ws
            ws.verify_candidate(self.settings, ws.get_candidate(version))
            for record in ws.uploads():
                if record['kb_version'] != version: continue
                pending=not record['admission']
                quarantined.append(dict(id=record['id'],filename=record['filename'],sha256=record['sha256'],
                    source_id=record['source_id'],admission='pending' if pending else 'inherited_approved',
                    note='仅暂存，未准入、未入库' if pending else '沿用既有具体准入'))
                if not pending and (record['filename'] not in seen or ws.admission(self.settings,record['filename'],record['source_id'],record['sha256']) != record['admission']):
                    blockers.append('获准上传与成功台账未对齐：'+record['filename'])
        snapshot=None
        try: snapshot=self.snapshot(version)
        except ValueError as error: blockers.append(str(error))
        if snapshot:
            # Matching file counts alone cannot authorize a vector with a wider
            # ACL or a different source identity. Inspect every stored declaration.
            from pymilvus import MilvusClient
            from qa_core.domain.models import normalize_knowledge_metadata
            expected={r['asset_id']:{**meta[r['filename']],**normalize_knowledge_metadata(meta[r['filename']])}
                      for r in ledger if r['filename'] in meta and r['asset_id']==meta[r['filename']]['asset_id']}
            equipment={}
            for record in ledger:
                filename=record['filename']
                if filename in meta and filename.lower().endswith('.csv'):
                    with (data/'raw'/filename).open(encoding='utf-8',newline='') as stream:
                        equipment[record['asset_id']]={r.get('equipment_type','') for r in csv.DictReader(stream)}
            keys=('tenant_id','dept_id','visibility','allowed_roles','role_scope','security_level',
                  'source_system','source_id','source_version','status','review_status','license_status','synthetic')
            counts={}; mismatch=False
            with self.client() as client:
                for collection in (self.settings.doc_collection,self.settings.faq_collection):
                    iterator=client.query_iterator(collection_name=collection,filter='kb_version == '+json.dumps(version),
                        output_fields=['asset_id','device_type','equipment_type',*keys],batch_size=500,consistency_level='Strong')
                    try:
                        while batch:=iterator.next():
                            for row in batch:
                                asset=row['asset_id'];decl=expected.get(asset);counts[asset]=counts.get(asset,0)+1
                                if not decl or any(row.get(k)!=decl.get(k) for k in keys):mismatch=True
                                elif decl.get('device_type'):
                                    if row['device_type']!=decl['device_type']:mismatch=True
                                elif asset in equipment and row['equipment_type'] in equipment[asset]:
                                    if row['device_type']!=(row['equipment_type'] or ''):mismatch=True
                                elif row['device_type']:mismatch=True
                    finally:iterator.close()
            if mismatch or counts!={r['asset_id']:r['chunk_count'] for r in ledger}:
                blockers.append('实际向量来源身份、访问范围或逐资产数量不匹配具体准入')
        if not ledger: blockers.append('无已完成来源台账')
        return dict(version=version,status=status,binding=manifest['binding'] if manifest else None,
            snapshot=snapshot,sources=sources,uploads=quarantined,jobs=jobs,blockers=sorted(set(blockers)),
            package_seal_sha256=sha(data/'artifact-hashes.json'),gaps=GAPS,
            candidate_kind='upload_delta' if version.startswith('local-work-') else 'registered_version',
            scope='工程审核仅沿用原文、身份及访问范围的既有批准；不批准pending，不证明独立质量',
            has_gate=bool(report_path),pointer_execution_enabled=self.sandbox)

    def review(self, version, actor):
        with self.publication_lock(): result=self.inspection(version)
        record=dict(id=uuid.uuid4().hex,version=version,actor=actor,created_at=now(),
            fingerprint=digest(result),inspection=result,kind='engineering_binding_review',new_admission=False)
        return self.write('reviews',record)

    def source(self, version, filename):
        inspection=self.inspection(version)
        if not any(row['filename']==filename and row['admission']=='inherited_approved' for row in inspection['sources']):
            raise HTTPException(404,'该版本没有匹配准入的来源')
        if Path(filename).name!=filename: raise HTTPException(404,'来源不存在')
        body=(Path(self.settings.data_dir)/'raw'/filename).read_bytes()
        try: text=body.decode('utf-8')
        except UnicodeDecodeError: return {'filename':filename,'sha256':hashlib.sha256(body).hexdigest(),'text':None,'note':'二进制原件请通过已鉴权知识原文查看；本页不假装已解析全文'}
        return {'filename':filename,'sha256':hashlib.sha256(body).hexdigest(),'text':text[:64000],
                'truncated':len(text)>64000,'note':'获准原件的字节文本；不是生成答案'}

    def prepare_package(self, review_id, actor):
        """Aggregate exact admitted source children with the complete baseline.

        The present upload workspace accepts only unchanged approved copies, so
        this merge is honestly a no-op. Pending originals remain quarantined; a
        duplicate full package is not fabricated as a new published release.
        """
        if self.sandbox: raise HTTPException(409,'演练子集不代表原完整发布包')
        with self.publication_lock():
            review=self.check_review(review_id,actor); version=review['version']
            from qa_core.ingest import workspace as uploads
            candidate=uploads.get_candidate(version);uploads.verify_candidate(self.settings,candidate,writable=True)
            source=Path(self.settings.data_dir)
            from scripts.prepare_legacy_projection import read,write,verify_seal,seal
            verify_seal(source)
            meta=read(source/'raw/meta.json');mapping=read(source/'source-map.json')
            with self.engine() as engine,engine.connect() as conn:
                active=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
            if len(active)!=1: raise HTTPException(409,'需要唯一完整基线ACTIVE')
            baseline=self.snapshot(active[0])
            if len(baseline['files'])!=len(meta) or {r['filename'] for r in baseline['files']}!=set(meta):
                raise HTTPException(409,'完整基线台账与原件清单不匹配')
            inherited=[];pending=[]
            for record in uploads.uploads():
                if record['kb_version']!=version: continue
                if not record['admission']:
                    pending.append({'upload_id':record['id'],'filename':record['filename'],'sha256':record['sha256'],'status':'pending_not_included'});continue
                name=record['filename']
                child=uploads.safe_path('candidates',version,'sources',record['id'],'raw')
                child_meta=read(child/'meta.json')
                if (sha(child/name)!=record['sha256'] or child_meta!={name:meta[name]}
                    or sha(source/'raw'/name)!=record['sha256']):
                    raise HTTPException(409,'来源子目录原文、元数据或基线不一致，不能汇总')
                inherited.append({'upload_id':record['id'],'filename':name,'sha256':record['sha256'],'outcome':'same_admitted_original'})
            ident=uuid.uuid4().hex;folder=self.path('packages',ident);(folder/'raw').mkdir(parents=True,exist_ok=False)
            for name in meta:
                (folder/'raw'/name).write_bytes((source/'raw'/name).read_bytes())
            write(folder/'raw/meta.json',meta);write(folder/'source-map.json',mapping)
            verify_seal(source)
            record=dict(id=ident,version=version,actor=actor,created_at=now(),baseline=active[0],
                review_id=review_id,baseline_snapshot=baseline,parent_seal_sha256=sha(source/'artifact-hashes.json'),
                file_count=len(meta),chunk_count=sum(r['count'] for r in baseline['collections'].values()),
                inherited_uploads=inherited,pending_excluded=pending,state='PREPARED',
                merge_outcome='no_content_change',new_admissions=0,paid_calls=0,sql_versions_created=0,
                note='完整原件已汇总并封存；上传是同一获准原文复本，没有内容增量，不伪造新STAGED、门禁或发布。pending仍不入库。')
            write(folder/'package.json',record);seal(folder)
            record['package_seal_sha256']=sha(folder/'artifact-hashes.json')
            self.write('package-records',record)
            return record

    def packages(self,version):
        self.status(version)
        records=[]
        for path in sorted(self.path('package-records').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            record=self.read('package-records',path.stem)
            if record['version']!=version:continue
            from scripts.prepare_legacy_projection import verify_seal
            folder=self.path('packages',record['id']);verify_seal(folder)
            if sha(folder/'artifact-hashes.json')!=record['package_seal_sha256']:raise ValueError('原件包封存摘要变化')
            records.append({k:record[k] for k in ('id','actor','created_at','baseline','state','file_count','chunk_count',
                'merge_outcome','package_seal_sha256','pending_excluded','note')})
            if len(records)==20:break
        return records

    @contextmanager
    def publication_lock(self):
        with self.engine() as engine, engine.connect() as conn:
            if conn.execute(sa.text('SELECT GET_LOCK(:n,0)'),{'n':LOCK_NAME}).scalar()!=1:
                raise HTTPException(409,'入库或发布正在进行，请稍后重试')
            conn.commit()
            try: yield conn
            finally:
                try: conn.execute(sa.text('SELECT RELEASE_LOCK(:n)'),{'n':LOCK_NAME}); conn.commit()
                except Exception: conn.invalidate(); raise

    def check_review(self, review_id, actor):
        review=self.read('reviews',review_id)
        if review['actor']!=actor: raise HTTPException(403,'只能执行本人准备的具体审核')
        if digest(self.inspection(review['version']))!=review['fingerprint']:
            raise HTTPException(409,'候选、来源、任务或配置已变化，请重新审核')
        if review['inspection']['blockers']: raise HTTPException(409,'审核存在阻断，不能运行门禁')
        return review

    def gate(self, review_id, actor):
        # This is run in a bounded background task. candidate_write uses the same
        # SQL advisory lock as ingestion/publication, including reentrant run_gate.
        review=self.read('reviews',review_id); version=review['version']
        with candidate_write(version,settings=self.settings,invalidate_report=False):
            self.check_review(review_id,actor)
            from qa_core.quality.gate import (GateResult,save_report,check_alarm_recall,check_sop_integrity,
                check_meta_completeness,check_duplicates,check_desensitize)
            from qa_core.indexing.embedder.embedder import get_embedder
            from qa_core.storage.version_manifest import bind_candidate,VersionManifestDao
            from qa_core.security.withdrawals import assert_publishable_sources
            job_id=uuid.uuid4().hex
            selected=self.settings.model_copy(update={'reports_dir':str(self.path('quality',job_id))})
            bind_candidate(selected,version)
            result=GateResult()
            with self.client() as client:
                before=publication.capture_snapshot(selected,version,client)
                assert_publishable_sources(selected,before['sources'])
                embedder=get_embedder(selected.embedding_model_path,selected.embedding_device)
                ok,detail=check_alarm_recall(client,embedder,version,selected.doc_collection)
                result.add('alarm_recall',ok,detail)
                for name,check in (('sop_integrity',check_sop_integrity),('meta_completeness',check_meta_completeness),
                    ('duplicates',check_duplicates),('desensitize',check_desensitize)):
                    ok,detail=check(client,version,selected.doc_collection);result.add(name,ok,detail)
                after=publication.capture_snapshot(selected,version,client)
            if before!=after:raise HTTPException(409,'门禁期间快照已变化')
            result.snapshot=after
            dao=VersionManifestDao(selected.mysql_dsn)
            try:dao.record_snapshot(version,after)
            finally:dao.close()
            path=save_report(result,version,selected.reports_dir)
            if result.snapshot is None: raise HTTPException(409,'门禁中候选发生变化，请重新审核')
            with self.engine() as engine, engine.begin() as conn:
                conn.execute(sa.text('UPDATE kb_versions SET quality_report_path=:p WHERE version=:v AND status=\'STAGED\''),{'p':path,'v':version})
            receipt=dict(id=job_id,version=version,actor=actor,review_id=review_id,created_at=now(),
                passed=result.passed,checks=result.checks,snapshot=result.snapshot,report_sha256=sha(path),
                report_path=path,scope='五项工程门禁；不证明独立语义质量、SOP安全支持或设备阈值适用',gaps=GAPS)
            self.write('gates',receipt)
            return {k:v for k,v in receipt.items() if k!='report_path'}

    def gate_report(self, version):
        status,path=self.status(version)
        if not path: return {'version':version,'recorded':False,'note':'未运行门禁'}
        target=Path(path).resolve()
        roots=(Path(self.settings.reports_dir).resolve(),self.path('quality').resolve())
        if not any(target.is_relative_to(r) for r in roots):
            return {'version':version,'recorded':False,'note':'历史报告属于其他集合/配置；需使用该版本完整服务配置核验'}
        if not target.is_file() or target.stat().st_size>1024*1024: raise ValueError('报告缺失或过大')
        report=json.loads(target.read_text(encoding='utf-8'))
        if report.get('kb_version')!=version: raise ValueError('报告版本不匹配')
        return {'version':version,'recorded':True,'sha256':sha(target),'report':report,'gaps':GAPS}

    def plan(self, version, action, actor):
        if action not in ('publish','rollback'): raise HTTPException(400,'未知版本操作')
        with self.publication_lock(), self.engine() as engine, engine.connect() as conn:
            active=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
            if len(active)!=1: raise HTTPException(409,'需要唯一ACTIVE版本')
            expected='STAGED' if action=='publish' else 'ARCHIVED'
            status,path=self.status(version)
            if status!=expected: raise HTTPException(409,'发布只接受STAGED，回滚只接受ARCHIVED')
            if not self.sandbox: raise HTTPException(409,'当前服务配置绑定原完整版本；请先准备完整候选、具体发布批准及配置交接，不能只改ACTIVE')
            if not path: raise HTTPException(409,'没有门禁报告')
            if not Path(path).resolve().is_relative_to(self.path('quality').resolve()): raise HTTPException(409,'报告不属于本版本工作区')
            try: report=self.verify_publication(version,path)
            except ValueError as error: raise HTTPException(409,str(error)) from None
            if self.inspection(version)['blockers']: raise HTTPException(409,'来源审核存在阻断')
            return self.write('plans',dict(id=uuid.uuid4().hex,version=version,action=action,actor=actor,
                from_version=active[0],snapshot=report['snapshot'],report_sha256=sha(path),binding=version_binding(self.settings),
                inspection_sha256=digest(self.inspection(version)),expires_at=time.time()+600,created_at=now(),
                scope='自有隔离测试库的指针发布/回滚；两版本同一配置，不涉及原8010或新来源准入',
                confirmation=f'{action}:{version}'))

    def execute(self, plan_id, confirmation, actor):
        plan=self.read('plans',plan_id)
        if plan['actor']!=actor: raise HTTPException(403,'只能执行本人准备的发布计划')
        if not self.sandbox: raise HTTPException(409,'原入口需要完整服务配置交接')
        if plan['expires_at']<time.time(): raise HTTPException(409,'计划过期，请重新准备')
        if confirmation!=plan['confirmation']: raise HTTPException(400,'请输入完整的操作及目标版本确认字符串')
        action,version=plan['action'],plan['version']
        with self.publication_lock() as conn:
            with conn.begin():
                active=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE' FOR UPDATE")).scalars().all()
                if active!=[plan['from_version']]: raise HTTPException(409,'ACTIVE已变化，禁止执行过期计划')
                status,path=self.status(version)
                if status!=('STAGED' if action=='publish' else 'ARCHIVED'): raise HTTPException(409,'目标状态已变化')
                if version_binding(self.settings)!=plan['binding'] or digest(self.inspection(version))!=plan['inspection_sha256']:
                    raise HTTPException(409,'候选、任务、来源或配置已变化')
                try: report=self.verify_publication(version,path)
                except ValueError as error: raise HTTPException(409,str(error)) from None
                if report['snapshot']!=plan['snapshot'] or sha(path)!=plan['report_sha256']: raise HTTPException(409,'门禁报告或快照已变化')
                conn.execute(sa.text("UPDATE kb_versions SET status='ARCHIVED' WHERE version=:v"),{'v':active[0]})
                conn.execute(sa.text("UPDATE kb_versions SET status='ACTIVE',published_at=NOW(),activated_by=:a WHERE version=:v"),{'a':actor,'v':version})
                result=dict(id=uuid.uuid4().hex,plan_id=plan_id,action=action,actor=actor,from_version=active[0],to_version=version,
                    committed=True,created_at=now(),paid_calls=0,new_admissions=0,scope=plan['scope'])
                conn.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:act,:a,:p)'),{'act':action,'a':actor,'p':json.dumps(result,ensure_ascii=False)})
        # A committed SQL switch is not reported as an uncommitted failure if a
        # receipt or cache operation fails. Audit log is the authoritative ledger.
        try:
            from qa_core.cache.redis_cache import CacheLayer
            cache=CacheLayer(self.settings.redis_url)
            for target in (version,plan['from_version']): cache.invalidate_version(target,strict=True)
            result['cache_invalidation_ok']=True
        except Exception: result['cache_invalidation_ok']=False
        try: self.write('executions',result); result['receipt_saved']=True
        except OSError: result['receipt_saved']=False
        return result
