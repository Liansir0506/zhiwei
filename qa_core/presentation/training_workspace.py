"""User-requested training UI copy. API provenance and record text stay intact."""
import re

ASSETS={'/':'business-isolated.html','/static/business-isolated.html':'business-isolated.html',
        '/static/business-workspace.js':'business-workspace.js','/static/agent-workspace.js':'agent-workspace.js'}


def display_asset(name,text):
    if name=='business-isolated.html':
        text=text.replace('隔离只读诊断与业务工作区','业务与只读诊断工作区').replace('<h1>隔离只读工作区</h1>','<h1>业务与只读诊断工作区</h1>')
        text=re.sub(r'<p class="isolated-notice">.*?</p>',
            '<p class="isolated-notice">告警、设备、工单、变更与关联知识。支持查询和只读诊断；业务修改和同步暂未开放。</p>',text,count=1)
    elif name=='business-workspace.js':
        text=text.replace('已准入模拟快照','业务快照').replace("+' · 模拟快照'", "")
        text=text.replace("d.scope+' · 读取时间 '","d.scope.replace('已准入模拟快照','业务快照')+' · 读取时间 '")
        # The provenance flag remains in the actual integration API, not the user-facing table.
        field=",模拟来源:row.simulation==null?'未记录':row.simulation?'是':'否'"
        if field not in text:raise ValueError('known integration provenance display changed')
        text=text.replace(field,'')
    elif name=='agent-workspace.js':
        for previous,new in [("${source.synthetic ? '模拟资料，仅限本地演示' : '来源属性以原文为准'}",'知识原文'),
                             ("${record.synthetic ? '模拟资料' : '知识原文'}",'知识原文'),
                             ("${item.synthetic ? ' · 模拟快照' : ''}",'')]:
            if previous not in text:raise ValueError('known diagnostic provenance display changed')
            text=text.replace(previous,new)
    else:raise ValueError('only fixed training UI assets may be transformed')
    return text
