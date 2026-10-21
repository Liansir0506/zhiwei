"""Bounded, permission-filtered business reads; no synchronization or commands."""
from datetime import datetime, timezone
import sqlalchemy as sa
from fastapi import HTTPException
from qa_core.application.domain_context_service import project, DomainContextService
from qa_core.api.auth import require_roles
from qa_core.storage.domain_dao import DomainDao
from qa_core.integrations.adapters import SYNC_ORDER

KINDS = {'devices': ('devices','device_id','device'), 'alarms': ('alarms','alarm_id','alarm'),
         'tickets': ('work_orders','ticket_id','work_orders'), 'changes': ('change_records','change_id','changes')}

def reader(ctx):
    require_roles(ctx, ('ops','auditor','admin'))
    if 'internal' not in ctx.scope.security_levels:
        raise HTTPException(403, '业务快照需要内部资料访问权限')

def selection(kind, ctx, *, identity=None, after='', query=''):
    """Filter BEFORE pagination. Only validated, accessible typed links survive."""
    if kind not in KINDS: raise HTTPException(404, '业务类型不存在')
    reader(ctx)
    table, key, _ = KINDS[kind]
    join = '' if kind == 'devices' else ' JOIN devices d ON d.device_id=r.device_id AND d.tenant_id=r.tenant_id'
    d = 'r' if kind == 'devices' else 'd'
    clauses, params = ['r.tenant_id=:tenant'], {'tenant':ctx.tenant_id}
    expanding = []
    if ctx.scope.device_types:
        clauses.append(f'{d}.device_type IN :types');params['types']=list(ctx.scope.device_types);expanding.append('types')
    if ctx.scope.dept_ids:
        clauses.append(f"({d}.dept_id='public' OR {d}.dept_id IN :depts)")
        params['depts']=list(ctx.scope.dept_ids);expanding.append('depts')
    def dept(alias):
        return f"({alias}.dept_id='public' OR {alias}.dept_id IN :depts)" if ctx.scope.dept_ids else f"({alias}.dept_id IS NOT NULL AND {alias}.dept_id<>'')"
    if kind in ('tickets','changes'): clauses.append(dept('r'))
    if kind == 'tickets':
        clauses.append('(r.alarm_id IS NULL OR EXISTS (SELECT 1 FROM alarms a WHERE a.alarm_id=r.alarm_id AND a.tenant_id=r.tenant_id AND a.device_id=r.device_id))')
    if kind == 'changes':
        clauses.append('(r.ticket_id IS NULL OR EXISTS (SELECT 1 FROM work_orders w WHERE w.ticket_id=r.ticket_id AND w.tenant_id=r.tenant_id AND w.device_id=r.device_id AND '+dept('w')+' AND (w.alarm_id IS NULL OR EXISTS (SELECT 1 FROM alarms a WHERE a.alarm_id=w.alarm_id AND a.tenant_id=w.tenant_id AND a.device_id=w.device_id))))')
    if identity is not None: clauses.append(f'r.{key}=:identity');params['identity']=identity
    if after: clauses.append(f'r.{key}>:after');params['after']=after
    if query:
        # Literal substring, not caller-supplied SQL wildcards.
        title = 'name' if kind == 'devices' else 'alarm_code' if kind == 'alarms' else 'title'
        clauses.append(f"(LOCATE(:query,r.{key})>0 OR LOCATE(:query,r.{title})>0)");params['query']=query
    sql = sa.text(f'SELECT r.* FROM {table} r{join} WHERE '+ ' AND '.join(clauses)+f' ORDER BY r.{key} LIMIT :limit')
    for field in expanding: sql=sql.bindparams(sa.bindparam(field,expanding=True))
    return sql, params

class BusinessWorkspace:
    def __init__(self, settings): self.dao=DomainDao(settings.mysql_dsn)
    def close(self): self.dao.engine.dispose()
    def listing(self, kind, ctx, *, after='', query='', limit=20):
        sql, params=selection(kind,ctx,after=after,query=query)
        with self.dao.engine.connect() as conn:
            rows=[dict(r) for r in conn.execute(sql,dict(params,limit=limit+1)).mappings()]
        key, model=KINDS[kind][1:]
        more=len(rows)>limit;rows=rows[:limit]
        return {'kind':kind,'items':[project(model,r) for r in rows],
                'next_cursor':rows[-1][key] if more else None,'page_size':limit,
                'scope':'当前凭据授权的已准入模拟快照；不代表实时状态',
                'synthetic':True,'retrieved_at':datetime.now(timezone.utc).isoformat()}
    def detail(self, kind, identity, ctx):
        sql,params=selection(kind,ctx,identity=identity)
        with self.dao.engine.connect() as conn: row=conn.execute(sql,dict(params,limit=1)).mappings().first()
        if row is None: raise HTTPException(404, '实体不存在或不可访问')
        row=dict(row)
        context=DomainContextService(self.dao).read('alarm' if kind=='alarms' else 'device',
                    identity if kind=='alarms' else row['device_id'],ctx)
        if context is None: raise HTTPException(404, '实体不存在或不可访问')
        return {'kind':kind,'entity':project(KINDS[kind][2],row),'context':context,
                'synthetic':True,'retrieved_at':datetime.now(timezone.utc).isoformat(),
                'record_time_basis':'业务时间以原始 UTC 快照为准；设备与变更观测时间未知',
                'diagnostic_target':{'alarm_id':identity} if kind=='alarms' else {'device_id':row['device_id']}}
    def integrations(self, ctx):
        reader(ctx)
        if ctx.scope.dept_ids or ctx.scope.device_types: raise HTTPException(403,'受限凭据不能读取租户汇总集成状态')
        with self.dao.engine.connect() as conn:
            rows={r['adapter_name']:dict(r) for r in conn.execute(sa.text(
                'SELECT adapter_name,sync_status,simulation,items_synced,last_synced_at,last_error_code FROM adapter_sync_state WHERE tenant_id=:tenant'),{'tenant':ctx.tenant_id}).mappings()}
        adapters=[]
        for name in SYNC_ORDER:
            record=rows.get(name)
            adapters.append(dict(record or {'adapter_name':name,'sync_status':'never_synced','simulation':None,
                'items_synced':None,'last_synced_at':None,'last_error_code':None},
                observed=bool(record),live_connection_checked=False))
        return {'tenant_id':ctx.tenant_id,'adapters':adapters,'read_only':True,
                'scope':'数据库中的租户同步账本；未探测或调用外部系统',
                'retrieved_at':datetime.now(timezone.utc).isoformat()}
