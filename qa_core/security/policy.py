"""RBAC + ABAC 的纯策略模型，供 HTTP、WebSocket、检索与缓存共用。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

SECURITY_LEVELS = ("public", "internal", "confidential", "restricted")
ROLE_SECURITY_LEVELS = {
    "guest": {"public"},
    "ops": {"public", "internal"},
    "auditor": {"public", "internal", "confidential"},
    "admin": set(SECURITY_LEVELS),
}


def _clean(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in (values or []) if str(value).strip()}))


@dataclass(frozen=True)
class AccessScope:
    """一次请求的有效访问域；未声明的部门/设备域表示不额外收窄。"""

    tenant_id: str = "default"
    roles: tuple[str, ...] = field(default_factory=tuple)
    dept_ids: tuple[str, ...] = field(default_factory=tuple)
    device_types: tuple[str, ...] = field(default_factory=tuple)
    security_levels: tuple[str, ...] = field(default_factory=lambda: ("public",))

    @classmethod
    def build(cls, *, tenant_id: str = "default", roles=None, dept_ids=None,
              device_types=None, requested_security_levels=None) -> "AccessScope":
        normalized_roles = _clean(roles)
        role_levels: set[str] = set()
        for role in normalized_roles:
            role_levels.update(ROLE_SECURITY_LEVELS.get(role, set()))
        if not role_levels:
            role_levels = {"public"}
        requested = set(_clean(requested_security_levels))
        # Key/请求未声明时按角色上限；声明后只能收窄，不能借 Header 扩权。
        effective = role_levels & requested if requested else role_levels
        return cls(tenant_id=(tenant_id or "default").strip(), roles=normalized_roles,
                   dept_ids=_clean(dept_ids), device_types=_clean(device_types),
                   security_levels=tuple(sorted(effective)))

    @property
    def fingerprint(self) -> str:
        raw = json.dumps({"tenant": self.tenant_id, "roles": self.roles,
                          "depts": self.dept_ids, "devices": self.device_types,
                          "levels": self.security_levels}, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    def to_dict(self) -> dict:
        return {"tenant_id": self.tenant_id, "roles": list(self.roles),
                "dept_ids": list(self.dept_ids), "device_types": list(self.device_types),
                "security_levels": list(self.security_levels), "fingerprint": self.fingerprint}
