"""企业系统 Adapter 边界。"""

from qa_core.integrations.adapters import build_adapter_registry
from qa_core.integrations.sync_service import EnterpriseSyncService

__all__ = ["EnterpriseSyncService", "build_adapter_registry"]
