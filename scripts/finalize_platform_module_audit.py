"""Finalize evidence from the live module audit; no product writes or model calls."""
import html
import json
import sqlalchemy as sa
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import read, write, seal, verify_seal

ROOT = runtime.PROJECT / '.runtime/test/reports/platform-module-audit-20261012'


def main():
    observations = read(ROOT / 'browser-modules.json')['observations']
    assert len(observations) == 14 and all(row['loaded'] for row in observations)
    runtime.install_adapter()
    settings = runtime.selected_settings()
    runtime.owned.apply_profile(settings)
    state = runtime.owned.inspect()
    engine = sa.create_engine(settings.mysql_dsn)
    with engine.connect() as conn:
        remaining = conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id=:u'),
                                 {'u': 'platform-module-audit-20261012'}).scalar()
        active = conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
    engine.dispose()
    assert remaining == 0 and active == ['local-content-quality-r1']
    findings = [
        ('P0', '入口持续可用性', '本次开始时Docker引擎未启动、入口失效；已恢复五个既有存储和同版本服务。', '补统一依赖启动、故障提示与持续运行观察。'),
        ('P1', '角色导航', '默认ops访问评测和凭据清单为403，但页面保留空框；临时admin实测可进入。', '根据能力显示入口；受限页面给出完整权限说明。'),
        ('P1', '评测记录', '管理员页面也无报告：运行报告目录有0份eval报告，项目reports目录有11份历史报告。', '建立按知识版本归属的报告索引、详情和运行入口；历史结果不能冒充当前版本验收。'),
        ('P1', '文档入库', 'Worker停用；6个STAGED中3个绑定不兼容、3个缺manifest，界面未区分。CLI导入账本不等于后台任务列表。', '创建候选、显示兼容状态并打通匹配配置的Worker任务、进度、失败与重试。'),
        ('P1', '上传工作目录', '当前data_dir属于已封存候选，上传暂存却写在其下。', '使用独立可写暂存及候选目录，保护已发布资料。'),
        ('P2', '只读诊断', '页面和接口存在，但当前批准运行配置关闭Agent；提交禁用，接口503。', '准备匹配的隔离诊断配置和端到端验收；本次未开启。'),
        ('P2', '业务工作区', '设备/告警上下文GET可用，有模拟设备、告警、工单等数据；只有单对象上下文面板，外部适配器为0。', '补告警、设备、工单、变更列表及关联详情、集成状态。'),
        ('P2', '知识治理闭环', '已有部分管理接口和脚本，但创建候选、内容审核、门禁、发布和回滚未形成完整页面流程。', '提供可追踪的版本工作台，沿用现有权限和准入要求。'),
    ]
    write(ROOT / 'findings.json', {'findings': [dict(priority=p, module=m, evidence=e, next_step=n)
          for p, m, e, n in findings], 'temporary_admin_keys_remaining': remaining,
          'active': active, 'runtime_state': state.get('state', state.get('status')),
          'scope': '实际ops/admin七页导航、原文查看及接口检查；未提交QA、上传、同步或发布；付费0。'})
    rows = ''.join('<tr>' + ''.join('<td>' + html.escape(v) + '</td>' for v in row) + '</tr>' for row in findings)
    screenshots = ''.join(f'<details><summary>{html.escape(o["role"])} / {html.escape(o["view"])}</summary>'
                         f'<img src="{o["role"]}-{o["view"]}.png" alt="实际页面截图">'
                         f'<pre>{html.escape(o["text"])}</pre></details>' for o in observations)
    (ROOT / 'audit.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>平台功能自检</title>'
        '<style>body{font:16px/1.65 system-ui;margin:32px;color:#17233a}table{border-collapse:collapse;width:100%}'
        'td,th{border:1px solid #ccd5e1;padding:12px;text-align:left;vertical-align:top}'
        'img{width:100%;height:auto}pre{white-space:pre-wrap}details{margin:16px 0}summary{cursor:pointer}</style>'
        '<h1>平台功能自检 · 2026-10-12</h1><p>真实Edge浏览器，ops/admin各7页。当前版本local-content-quality-r1。'
        '知识中心1041个来源条目（31 FAQ、1010文档来源），对应306份物理文件；原文可查看。</p>'
        '<p>本次未提交问答或调用付费模型，未上传、同步或发布。临时管理员凭据已删除。'
        '页面可进入不等于生成答案、入库或独立质量验收通过。</p>'
        '<table><thead><tr><th>优先级</th><th>模块</th><th>实测结果</th><th>完善项</th></tr></thead>'
        '<tbody>' + rows + '</tbody></table><h2>实际页面截图与可见内容</h2>' + screenshots + '</html>', encoding='utf-8')
    seal(ROOT)
    verify_seal(ROOT)
    print(json.dumps({'browser_pages': len(observations), 'temporary_admin_keys_remaining': remaining,
                      'active': active, 'report': str(ROOT / 'audit.html'), 'paid_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
