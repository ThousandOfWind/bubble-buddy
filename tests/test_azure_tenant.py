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
        self.expires_on = 9_999_999_999


class _AuthError(RuntimeError):
    def __init__(self, message: str = "unauthorized", status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class AzureTenantTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        self._tmp.close()
        self._prev = os.environ.get("BUBBLE_BUDDY_CONFIG")
        os.environ["BUBBLE_BUDDY_CONFIG"] = self._tmp.name
        self._prev_env_tenant = os.environ.pop("AZURE_TENANT_ID", None)
        from bubble_buddy import azure_client as az

        az._discovered_tenant = None  # reset auto-discovery cache
        az._cached_token = None
        az._last_method = ""
        az._account_hint = ""
        az._reauth_required = False

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("BUBBLE_BUDDY_CONFIG", None)
        else:
            os.environ["BUBBLE_BUDDY_CONFIG"] = self._prev
        if self._prev_env_tenant is not None:
            os.environ["AZURE_TENANT_ID"] = self._prev_env_tenant
        os.unlink(self._tmp.name)
        from bubble_buddy import azure_client as az

        az._discovered_tenant = None
        az._cached_token = None
        az._last_method = ""
        az._account_hint = ""
        az._reauth_required = False
        from bubble_buddy import config

        config.load_config(reload=True)

    def _set_tenant(self, tenant):
        from bubble_buddy import config

        data = {"azure": {"tenant_id": tenant}} if tenant is not None else {}
        with open(self._tmp.name, "w", encoding="utf-8") as f:
            json.dump(data, f)
        config.load_config(reload=True)

    def _write(self, data):
        from bubble_buddy import config

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


    def test_tenant_alias_and_locations(self):
        from bubble_buddy import azure_client as az

        # azure.tenant (alias for tenant_id)
        self._write({"azure": {"tenant": "aliased"}})
        self.assertEqual(az._configured_tenant(), "aliased")
        # top-level tenant_id (misplaced but honored)
        self._write({"tenant_id": "toplevel"})
        self.assertEqual(az._configured_tenant(), "toplevel")
        # top-level tenant alias
        self._write({"tenant": "toplevel-alias"})
        self.assertEqual(az._configured_tenant(), "toplevel-alias")

    def test_tenant_from_env(self):
        from bubble_buddy import azure_client as az

        self._write({})
        os.environ["AZURE_TENANT_ID"] = "from-env"
        try:
            self.assertEqual(az._configured_tenant(), "from-env")
        finally:
            os.environ.pop("AZURE_TENANT_ID", None)

    def test_configured_tenant_wins_over_discovery(self):
        from bubble_buddy import azure_client as az

        self._write({"azure": {"tenant_id": "configured"}})
        called = {"n": 0}

        def _boom():
            called["n"] += 1
            return "discovered"

        orig = az._discover_tenant_from_endpoint
        try:
            az._discover_tenant_from_endpoint = _boom
            self.assertEqual(az._tenant_id(), "configured")
            self.assertEqual(called["n"], 0)  # discovery never invoked
        finally:
            az._discover_tenant_from_endpoint = orig

    def test_discovery_used_when_unconfigured(self):
        from bubble_buddy import azure_client as az

        self._write({})  # no tenant anywhere
        orig = az._discover_tenant_from_endpoint
        try:
            az._discover_tenant_from_endpoint = lambda: "discovered-tenant"
            self.assertEqual(az._tenant_id(), "discovered-tenant")
        finally:
            az._discover_tenant_from_endpoint = orig

    def test_discovery_parses_www_authenticate(self):
        from bubble_buddy import azure_client as az

        self._write({"azure": {"endpoint": "https://x.openai.azure.com"}})

        import urllib.error
        import urllib.request

        guid = "12345678-1234-1234-1234-1234567890ab"
        header = (
            f'Bearer authorization_uri="https://login.microsoftonline.com/{guid}", '
            'resource="https://cognitiveservices.azure.com"'
        )

        class _Hdrs:
            def get(self, _k, default=""):
                return header

        err = urllib.error.HTTPError("u", 401, "Unauthorized", _Hdrs(), None)

        def _fake_urlopen(*_a, **_k):
            raise err

        orig = urllib.request.urlopen
        try:
            urllib.request.urlopen = _fake_urlopen
            az._discovered_tenant = None
            self.assertEqual(az._discover_tenant_from_endpoint(), guid)
            # cached: a second call must not re-probe
            urllib.request.urlopen = lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("should not re-probe")
            )
            self.assertEqual(az._discover_tenant_from_endpoint(), guid)
        finally:
            urllib.request.urlopen = orig

    def test_auth_retry_forces_refresh_once(self):
        from bubble_buddy import azure_client as az

        refresh_calls = []
        orig_aad = az._aad_token
        try:
            az._aad_token = lambda scope, **kwargs: refresh_calls.append((scope, kwargs)) or "fresh"
            calls = {"n": 0}

            def _action():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise _AuthError()
                return "ok"

            result = az._call_with_auth_retry({"auth": "aad", "scope": "scope"}, _action)
            self.assertEqual(result, "ok")
            self.assertEqual(calls["n"], 2)
            self.assertEqual(refresh_calls, [("scope", {"force": True})])
            self.assertFalse(az._reauth_required)
        finally:
            az._aad_token = orig_aad

    def test_auth_retry_forces_refresh_for_expired_token_403(self):
        from bubble_buddy import azure_client as az

        refresh_calls = []
        orig_aad = az._aad_token
        try:
            az._aad_token = lambda scope, **kwargs: refresh_calls.append((scope, kwargs)) or "fresh"
            calls = {"n": 0}

            def _action():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise _AuthError("token expired", status_code=403)
                return "ok"

            self.assertEqual(
                az._call_with_auth_retry({"auth": "aad", "scope": "scope"}, _action),
                "ok",
            )
            self.assertEqual(calls["n"], 2)
            self.assertEqual(refresh_calls, [("scope", {"force": True})])
            self.assertFalse(az._reauth_required)
        finally:
            az._aad_token = orig_aad

    def test_generic_forbidden_does_not_trigger_reauth(self):
        from bubble_buddy import azure_client as az

        refresh_calls = []
        error = _AuthError(
            "Forbidden: authentication policy denies this operation", status_code=403
        )
        orig_aad = az._aad_token
        try:
            az._aad_token = lambda *args, **kwargs: refresh_calls.append((args, kwargs))
            with self.assertRaisesRegex(_AuthError, "policy denies") as raised:
                az._call_with_auth_retry(
                    {"auth": "aad", "scope": "scope"},
                    lambda: (_ for _ in ()).throw(error),
                )
            self.assertIs(raised.exception, error)
            self.assertEqual(refresh_calls, [])
            self.assertFalse(az._reauth_required)
        finally:
            az._aad_token = orig_aad

    def test_refresh_failure_marks_reauth_required(self):
        from bubble_buddy import azure_client as az

        orig_aad = az._aad_token
        try:
            az._aad_token = lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("refresh failed")
            )
            with self.assertRaises(az.AuthRequiredError):
                az._call_with_auth_retry(
                    {"auth": "aad", "scope": "scope"},
                    lambda: (_ for _ in ()).throw(_AuthError("token expired")),
                )
            self.assertTrue(az._reauth_required)
            self.assertFalse(az.auth_status()["signed_in"])
        finally:
            az._aad_token = orig_aad

    def test_sign_in_clears_reauth_required(self):
        from bubble_buddy import azure_client as az

        az._reauth_required = True
        orig_interactive = az._interactive_sign_in
        orig_auth_status = az.auth_status
        try:
            az._interactive_sign_in = lambda _scope: _Tok(_make_jwt("tid"))
            az.auth_status = lambda: {"signed_in": True, "method": "browser", "account": ""}
            status = az.sign_in()
            self.assertTrue(status["signed_in"])
            self.assertFalse(az._reauth_required)
            self.assertIsNotNone(az._cached_token)
        finally:
            az._interactive_sign_in = orig_interactive
            az.auth_status = orig_auth_status


if __name__ == "__main__":
    unittest.main()
