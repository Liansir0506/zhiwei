"""阶段3 组件连接检查（component：需要 docker compose 服务在线）。"""
import pytest

from qa_core.config.settings import get_settings
from qa_core.storage.connections import check_all


@pytest.mark.component
def test_all_components_ok_when_compose_up():
    result = check_all(get_settings())
    for name, status in result.items():
        assert status.startswith("ok"), f"{name} not ok: {status}"
