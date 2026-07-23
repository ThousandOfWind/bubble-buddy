import base64
import json
import os
import tempfile
import unittest


def _make_jwt(tid: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"tid": tid}).encode()).decode().rstrip("=")
    return f"header.{payload}.sig"


class _Tok:
    def __init__(self, token: str) -> None:
        self.token = token


class AzureTenantTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        self._tmp.close()
        self._prev = os.environ.get("BUBBLE_BUDDY_CONFIG")
        os.environ["BUBBLE_BUDDY_CONFIG"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("BUBBLE_BUDDY_CONFIG", None)
        else:
            os.environ["BUBBLE_BUDDY_CONFIG"] = self._prev
        os.unlink(self._tmp.name)
        from bubble_buddy import config

        config.load_config(reload=True)

    def _set_tenant(self, tenant):
        from bubble_buddy import config

        data = {"azure": {"tenant_id": tenant}} if tenant is not None else {}
        with open(self._tmp.name, "w", encoding="utf-8") as f:
            json.dump(data, f)
        config.load_config(reload=True)

    def test_parses_tid_claim(self):
        from bubble_buddy import azure_client as az

        self.assertEqual(az._jwt_tenant(_make_jwt("AAA-BBB")), "AAA-BBB")
        self.assertEqual(az._jwt_tenant("garbage"), "")

    def test_matches_configured_tenant(self):
        from bubble_buddy import azure_client as az

        self._set_tenant("resource-tenant")
        self.assertTrue(az._token_matches_tenant(_Tok(_make_jwt("resource-tenant"))))
        # case-insensitive
        self.assertTrue(az._token_matches_tenant(_Tok(_make_jwt("Resource-Tenant"))))

    def test_rejects_wrong_tenant(self):
        from bubble_buddy import azure_client as az

        self._set_tenant("resource-tenant")
        wrong = _make_jwt("72f988bf-86f1-41af-91ab-2d7cd011db47")
        self.assertFalse(az._token_matches_tenant(_Tok(wrong)))

    def test_fails_open_when_no_tenant_configured(self):
        from bubble_buddy import azure_client as az

        self._set_tenant(None)
        # No configured tenant -> never reject.
        self.assertTrue(az._token_matches_tenant(_Tok(_make_jwt("anything"))))

    def test_fails_open_on_unparseable_token(self):
        from bubble_buddy import azure_client as az

        self._set_tenant("resource-tenant")
        self.assertTrue(az._token_matches_tenant(_Tok("not-a-jwt")))

    def test_acquire_skips_wrong_tenant_credential(self):
        """A credential that returns a wrong-tenant token must not block the next
        (tenant-steered) credential from being tried."""
        from bubble_buddy import azure_client as az

        self._set_tenant("resource-tenant")

        class _Cred:
            def __init__(self, tid):
                self._tid = tid

            def get_token(self, *_a, **_k):
                return _Tok(_make_jwt(self._tid))

        wrong = _Cred("72f988bf-86f1-41af-91ab-2d7cd011db47")
        right = _Cred("resource-tenant")

        orig_list = az._default_credential_list
        orig_interactive = az._get_interactive_credential
        try:
            az._default_credential_list = lambda: [wrong, right]

            class _NoInteractive:
                def get_token(self, *_a, **_k):
                    raise RuntimeError("no cached browser sign-in")

            az._get_interactive_credential = lambda: _NoInteractive()
            token = az._acquire_token("scope", allow_interactive=False)
            self.assertEqual(az._jwt_tenant(token.token), "resource-tenant")
            self.assertEqual(az._last_method, "cli")
        finally:
            az._default_credential_list = orig_list
            az._get_interactive_credential = orig_interactive


if __name__ == "__main__":
    unittest.main()
