from qa_core.api.auth import AuthContext, authenticate_headers
from qa_core.security.policy import AccessScope


def test_role_bound_security_level_cannot_be_expanded_by_requested_scope():
    scope = AccessScope.build(tenant_id="tenant-a", roles=["ops"],
                              requested_security_levels=["public", "confidential"])
    assert scope.security_levels == ("public",)
    assert scope.tenant_id == "tenant-a"


def test_access_scope_fingerprint_changes_with_abac_dimensions():
    base = AccessScope.build(roles=["ops"], dept_ids=["netops"])
    device_limited = AccessScope.build(roles=["ops"], dept_ids=["netops"],
                                       device_types=["gNodeB"])
    assert base.fingerprint != device_limited.fingerprint


def test_dev_headers_build_same_scope_shape_as_api_key_context():
    ctx = authenticate_headers(x_user_id="u1", x_user_roles="ops", x_tenant_id="t1",
                               x_dept_ids="netops", x_device_types="gNodeB",
                               x_security_levels="public,internal")
    expected = AuthContext("u1", ["ops"], "header", "t1", ["netops"], ["gNodeB"],
                           ["public", "internal"])
    assert ctx.scope.to_dict() == expected.scope.to_dict()
