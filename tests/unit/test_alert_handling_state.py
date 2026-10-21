# -*- coding: utf-8 -*-
"""T1 处置状态机单元测试：转换规则 + 三个业务分支。"""
import pytest

from qa_core.api.route_ops_resources import _transition


def ok(action, state, verdict=None, last=None, note="", mn=None, mv=None, om=None):
    return _transition(action, state, verdict, last, note, mn, mv, om)


def test_happy_path_full_chain():
    # 待确认 → 确认 → 处理中
    assert ok('confirm', 'pending_confirm')[0] == 'processing'
    # 处理中 → 记录处理 → 处理中
    assert ok('handle', 'processing', note='已更换板卡')[0] == 'processing'
    # 处理中 → 观察通过 → 恢复观察
    assert ok('observe', 'processing', verdict='pass', mn='注册成功率', mv='99.2%', om=30)[0] == 'observing'
    # 恢复观察 + 最近通过 → 标记解决
    assert ok('resolve', 'observing', last='pass')[0] == 'resolved'


def test_cannot_skip_confirm():
    # 跳过确认直接处理 / 观察：拒绝
    with pytest.raises(ValueError, match='请先人工确认'):
        ok('handle', 'pending_confirm')
    with pytest.raises(ValueError, match='请先人工确认'):
        ok('observe', 'pending_confirm', verdict='pass', mn='m', mv='1', om=5)
    # 待确认直接 resolve：拒绝
    with pytest.raises(ValueError):
        ok('resolve', 'pending_confirm', last='pass')


def test_observe_requires_evidence():
    with pytest.raises(ValueError, match='验证依据'):
        ok('observe', 'processing', verdict='pass', mn=None, mv='99%', om=10)
    with pytest.raises(ValueError, match='验证依据'):
        ok('observe', 'processing', verdict=None, mn='m', mv='99%', om=10)
    with pytest.raises(ValueError, match='验证依据'):
        ok('observe', 'processing', verdict='pass', mn='m', mv='99%', om=None)


def test_observe_fail_returns_to_processing():
    new_state, note = ok('observe', 'processing', verdict='fail', mn='注册成功率', mv='85%', om=15, note='指标未恢复')
    assert new_state == 'processing'
    assert '指标未恢复' in note and '注册成功率' in note
    # 未写备注时默认说明也含"未通过"与指标
    new_state, note = ok('observe', 'processing', verdict='fail', mn='注册成功率', mv='85%', om=15)
    assert '未通过' in note and '注册成功率' in note


def test_resolve_requires_passing_observation():
    # 没有观察记录：不能解决
    with pytest.raises(ValueError, match='未恢复不能标记解决'):
        ok('resolve', 'observing', last=None)
    # 最近一次观察失败：不能解决
    with pytest.raises(ValueError, match='未恢复不能标记解决'):
        ok('resolve', 'observing', last='fail')


def test_repeat_confirm_rejected():
    with pytest.raises(ValueError, match='不能执行'):
        ok('confirm', 'processing')
    with pytest.raises(ValueError, match='不能执行'):
        ok('confirm', 'resolved')


def test_resolved_is_terminal():
    with pytest.raises(ValueError):
        ok('handle', 'resolved')
    with pytest.raises(ValueError):
        ok('observe', 'resolved', verdict='pass', mn='m', mv='1', om=5)
