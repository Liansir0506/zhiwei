"""Read-only concrete publication and outbound review; no activation/model calls."""
import csv
import html
from datetime import datetime,timezone
import sqlalchemy as sa
from scripts import content_quality_release_runtime as release
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal


def outbound_entries():
    meta=read(release.merge.CANDIDATE/'raw/meta.json');entries=[]
    for name,m in meta.items():
        if not (name.startswith('faq_revision_') or name.startswith('faq_triage_')):continue
        with (release.merge.CANDIDATE/'raw'/name).open(encoding='utf-8',newline='') as stream:
            rows=list(csv.DictReader(stream))
        if len(rows)!=1:raise ValueError('one reviewed FAQ body per file required')
        entries.append(dict(filename=name,sha256=digest(release.merge.CANDIDATE/'raw'/name),
            asset_id=m['asset_id'],source_system=m['source_system'],source_id=m['source_id'],
            access_scope={k:m[k] for k in ('tenant_id','visibility','security_level','role_scope')},
            synthetic=True,question=rows[0]['question'],answer=rows[0]['answer']))
    if len(entries)!=28:raise ValueError('exact new twenty plus eight bodies required')
    return sorted(entries,key=lambda e:e['filename'])


def render(plan,outbound):
    esc=lambda value:html.escape(str(value))
    cards=''.join('<article><details><summary>'+esc(e['question'])+'</summary><p>'+esc(e['filename'])+
        ' · '+esc(e['source_id'])+'</p><pre>'+esc(e['answer'])+'</pre></details></article>' for e in outbound['entries'])
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>完整内容候选发布审核</title><style>body{font:16px/1.7 system-ui;margin:0;background:#f3f6fb;color:#17233a}main{max-width:1050px;margin:auto;padding:24px}section,article{background:white;border:1px solid #dce3ed;border-radius:12px;padding:18px;margin:14px 0}h1{font-size:28px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}summary{cursor:pointer;font-weight:600}p,li,code{overflow-wrap:anywhere}strong{color:#2355b4}</style><main>
<h1>完整内容候选发布审核</h1><p>切换与新增资料外发：<strong>pending</strong>。现有准入不重复审批；本页提交具体发布决定。</p>
<section><h2>切换结果</h2><p>旧 ACTIVE：local-merged-r2（279份 / 5903片段）。新候选：local-content-quality-r1（306份 / 4357片段 / 31条FAQ）。</p>
<p>使用独立集合 local_content_quality_r1_doc / local_content_quality_r1_faq，保留旧库与历史。新服务进程接入已验证的FAQ补充检索与证据适用性过滤；Agent关闭，强鉴权，入口仍为本地8010。</p>
<p>在新版本用20条有明确范围的模拟主题FAQ替代1000条模板FAQ，补入8条通用取证FAQ；原60份参考文档保留完整章节与真实位置。原SOP和工单按已准入范围继承。</p></section>
<section><h2>选择具体运行范围</h2><p><strong>批准切换并允许新增资料用于DeepSeek生成</strong>：开启文档生成，允许下列28份新增正文及本演示库内可读片段，连同主动提问和必要会话上下文发送至 https://api.deepseek.com/v1/chat/completions。手动提问可能产生API费用。本次发布验证付费调用为0。</p>
<p><strong>仅批准引用模式切换，新增资料不外发</strong>：FAQ直接回答、文档返回引用；关闭生成，模型Key不传给新服务，外发地址禁用。</p>
<p>外发不包含Key、数据库凭据或权限配置。审核页中的身份与访问范围用于核对来源；不作为模型提示内容。</p></section>
<section><h2>切换与回退</h2><ol><li>重新验证原入口归属、唯一ACTIVE、双方完整发布快照及具体批准回执。</li><li>停止自有旧入口，通过标准鉴权发布接口激活新候选，隐藏启动新服务。</li><li>只读验证真实入口身份、推荐与31条FAQ全文；不发送付费问答请求。</li><li>停止、激活、启动或检查失败时，停止自有新入口，恢复旧集合配置，经标准接口回滚旧版本并恢复旧服务。回退失败保留绑定与恢复日志，不声明成功。</li></ol><p>只有双方报告、版本绑定和封存代码仍匹配时才允许执行；不重放入库、不删除旧版本、不启动业务Worker。回退针对整个服务配置，不能只改ACTIVE指针。</p></section>
<section><h2>当前验收与保留缺口</h2><p>完整库工程门禁和候选开发回归已通过；本轮回退测试为故障注入，尚未实际切换。独立语义质量、200份SOP支持性专项、设备阈值与完整现场流程仍未通过。既有入口曾多次失效，短时恢复不代表长期稳定。缺失依据不编造答案。</p></section>
<section><h2>新增外发正文逐项核对（28份）</h2><p>仅供隔离本地演示的自编模拟内容，全部正文如下。</p>'''+cards+'</section></main></html>'


def main():
    if release.REVIEW.exists():raise ValueError('immutable review already exists; verify it, never overwrite')
    adapter=read(release.PROJECT/'.runtime/test/reports/content-quality-release-validation-20261011/default-adapter.json')
    if not adapter.get('passed') or adapter.get('paid_calls')!=0:
        raise ValueError('successful default-adapter validation required before sealing review')
    for parent in (release.merge.CANDIDATE,release.merge.REPORT):verify_seal(parent)
    release.owned.validate_review()
    for name,checksum in read(release.merge.REPORT/'code-hashes.json').items():
        if digest(release.PROJECT/name)!=checksum:raise ValueError('candidate code changed')
    from qa_core.quality.publication import verify_publication
    engine=sa.create_engine(release.BASE.mysql_dsn,pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            active=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
            records={r[0]:dict(status=r[1],report=r[2]) for r in conn.execute(sa.text(
                'SELECT version,status,quality_report_path FROM kb_versions WHERE version IN (:a,:b)'),dict(a=release.OLD,b=release.VERSION))}
        if active!=[release.OLD] or records[release.VERSION]['status']!='STAGED':raise ValueError('expected old ACTIVE/new STAGED')
        observations=[]
        for version,settings in ((release.OLD,release.BASE),(release.VERSION,release.profile('citation')),
                                 (release.VERSION,release.profile('generation'))):
            verify_publication(settings,version,records[version]['report'])
            observations.append(dict(version=version,collections=[settings.doc_collection,settings.faq_collection],passed=True))
        from qa_core.storage.version_manifest import verify_read_binding
        for version,settings in ((release.OLD,release.profile('citation')),(release.VERSION,release.BASE)):
            try:verify_read_binding(settings,version)
            except ValueError:observations.append(dict(version=version,cross_collection_binding_rejected=True))
            else:raise ValueError('cross collection binding unexpectedly accepted')
        runtime=release.owned.inspect()
        if runtime['status']!='running':raise ValueError('old owned entry must be healthy before review')
    finally:engine.dispose()
    entries=outbound_entries()
    prior=read(release.merge.PARENT/'raw/meta.json');current=read(release.merge.CANDIDATE/'raw/meta.json')
    inherited=[name for name in prior if name!='faq_foundation_simulated.csv']
    if len(inherited)!=278 or any(current[name]!=prior[name] or
        digest(release.merge.PARENT/'raw'/name)!=digest(release.merge.CANDIDATE/'raw'/name) for name in inherited):
        raise ValueError('inherited original or permissions changed')
    budget=read(release.owned.ROOT/'generation-validation-budget.json')
    release.REVIEW.mkdir(parents=True)
    outbound=dict(status='pending',endpoint='https://api.deepseek.com/v1/chat/completions',method='POST',
        scope='user_initiated_questions_necessary_session_context_and_readable_demo_fragments',
        excluded=['api_keys','database_credentials','permission_configuration'],entries=entries,
        inherited_original_files=278,inherited_bytes_and_permissions_unchanged=True,
        publication_validation_paid_calls=0,independent_quality_approved=False)
    write(release.REVIEW/'outbound-review.json',outbound)
    for mode in release.MODES:write(release.REVIEW/('profile-'+mode+'.json'),release.owned.public_profile(release.profile(mode)))
    bindings=dict(read(release.merge.REPORT/'code-hashes.json'))
    for name in ['scripts/content_quality_release_runtime.py','scripts/content_quality_local_server.py',
        'scripts/execute_content_quality_release.py','scripts/prepare_content_quality_release.py',
        'scripts/start_content_quality_background.ps1','scripts/stop_content_quality.ps1',
        'scripts/verify_content_quality_release_adapter.py','tests/unit/test_content_quality_release.py',
        'scripts/verify_content_quality_release_browser.py',
        'scripts/original_local_runtime.py','scripts/original_local_server.py',
        'qa_core/application/qa_usecase.py','qa_core/api/route_kb.py']:
        bindings[name]=digest(release.PROJECT/name)
    plan=dict(version=release.VERSION,previous_active=release.OLD,status='pending',
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),candidate_files=306,candidate_chunks=4357,
        collections=[release.merge.DOC,release.merge.FAQ],validation_paid_calls=0,agent_enabled=False,
        modes=release.MODES,independent_quality_approved=False,long_term_stability_proven=False,
        candidate_projection_sha256=digest(release.merge.CANDIDATE/'projection.json'),
        candidate_report_seal_sha256=digest(release.merge.REPORT/'artifact-hashes.json'),
        outbound_review_sha256=digest(release.REVIEW/'outbound-review.json'),code_bindings=bindings,
        profile_sha256={m:digest(release.REVIEW/('profile-'+m+'.json')) for m in release.MODES},
        rollback_requires_previous_collections_and_runtime=True,actual_activation_performed=False)
    write(release.REVIEW/'release-plan.json',plan)
    write(release.REVIEW/'readiness.json',dict(passed=True,read_only=True,bindings=observations,
        original_entry_status=runtime['status'],generation_budget=budget,paid_calls=0,
        original_env_sha256=digest(release.PROJECT/'.env.runtime'),old_active=release.OLD,
        candidate_status='STAGED',selection_exists=release.SELECTION.exists()))
    (release.REVIEW/'review.html').write_text(render(plan,outbound),encoding='utf-8')
    seal(release.REVIEW);release.validate_review()
    print('Concrete publication/outbound review sealed; old ACTIVE unchanged; paid calls 0')


if __name__=='__main__':main()
