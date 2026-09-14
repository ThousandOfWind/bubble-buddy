import base64
import io
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx

from bubble_buddy import account_auth, cli, codex_client as codex


def jwt(account="account-1", **extra):
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": account}, **extra}
    return "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".sig"


def credentials(**updates):
    return {"access": jwt(), "refresh": "refresh-old", "expires": time.time() + 3600,
            "account_id": "account-1", "account": "test@example.invalid", **updates}


def token_response(**updates):
    return httpx.Response(200, content=json.dumps({"access_token": jwt(), "refresh_token": "refresh-new", "expires_in": 3600, **updates}))


class MemoryStore:
    def __init__(self, value):
        self.value = json.dumps(value)

    def load(self):
        return self.value

    def save(self, value):
        self.value = value


class CodexAuthTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(credentials())
        lock = threading.Lock()

        @contextmanager
        def store():
            with lock:
                yield self.store

        self.enterContext(patch.object(codex, "_store", store))
        # No test in this class can contact a real OAuth or transcription server.
        self.post = self.enterContext(patch("httpx.post", side_effect=AssertionError("Unexpected network request")))
        self.browser = self.enterContext(patch.object(codex.webbrowser, "open", side_effect=AssertionError("Unexpected browser")))

    def test_pkce_matches_pi_protocol_and_state_is_unique(self):
        import hashlib
        url, verifier, state = codex._authorization_url()
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["client_id"], [codex.CLIENT_ID])
        self.assertEqual(params["redirect_uri"], ["http://localhost:1455/auth/callback"])
        self.assertEqual(params["scope"], ["openid profile email offline_access"])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(params["code_challenge"], [challenge])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["state"], [state])
        self.assertNotEqual(state, codex._authorization_url()[2])

    def test_callback_rejects_wrong_missing_and_duplicate_state(self):
        for query in ("code=c", "state=wrong&code=c", "state=s&state=s&code=c"):
            with self.subTest(query=query):
                status, _, code = codex._callback_result("/auth/callback?" + query, "s")
                self.assertEqual(status, 400)
                self.assertEqual(code, "")

    def test_callback_requires_exact_route_and_single_code(self):
        for path in ("/?state=s&code=c", "/auth/callback?state=s", "/auth/callback?state=s&code=a&code=b"):
            self.assertEqual(codex._callback_result(path, "s")[2], "")
        self.assertEqual(codex._callback_result("/auth/callback?state=s&code=good", "s")[2], "good")
        self.assertEqual(codex._callback_result("/auth/callback?state=s&error=denied", "s")[2], "denied")

    def test_cached_token_and_status_never_open_browser(self):
        self.assertEqual(codex._credentials()["refresh"], "refresh-old")
        status = codex.auth_status()
        self.assertTrue(status["signed_in"])
        self.assertNotIn("access", status)
        self.assertNotIn("refresh", status)
        self.post.assert_not_called()
        self.browser.assert_not_called()

    def test_refresh_rotation_is_persisted_and_reused(self):
        self.store.save(json.dumps(credentials(expires=0)))
        self.post.side_effect = None
        self.post.return_value = token_response()
        codex._credentials()
        codex._credentials()
        self.assertEqual(json.loads(self.store.value)["refresh"], "refresh-new")
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["data"]["grant_type"], "refresh_token")

    def test_concurrent_recordings_rotate_once(self):
        self.store.save(json.dumps(credentials(expires=0)))
        self.post.side_effect = None
        self.post.return_value = token_response()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: codex._credentials(), range(12)))
        self.assertEqual(len(results), 12)
        self.post.assert_called_once()

    def test_stale_401_does_not_rotate_newer_token(self):
        codex._credentials(rejected_access="already-replaced-token")
        self.post.assert_not_called()

    def test_permanent_refresh_failure_forgets_credentials(self):
        self.store.save(json.dumps(credentials(expires=0)))
        self.post.side_effect = None
        self.post.return_value = httpx.Response(400, json={"error": "invalid_grant", "secret": "dont-log"})
        with self.assertRaises(codex.AuthRequiredError) as error:
            codex._credentials()
        self.assertNotIn("dont-log", str(error.exception))
        self.assertEqual(json.loads(self.store.value), {})
        self.assertFalse(codex.auth_status()["signed_in"])

    def test_transient_failure_keeps_refresh_token(self):
        self.store.save(json.dumps(credentials(expires=0)))
        for response in (httpx.Response(500, text="secret-server-body"), httpx.Response(429)):
            self.post.side_effect = None
            self.post.return_value = response
            with self.assertRaises(RuntimeError) as error:
                codex._credentials()
            self.assertNotIn("secret-server-body", str(error.exception))
            self.assertEqual(json.loads(self.store.value)["refresh"], "refresh-old")
        self.post.side_effect = httpx.ConnectError("secret-network-detail")
        with self.assertRaises(RuntimeError) as error:
            codex._credentials()
        self.assertNotIn("secret-network-detail", str(error.exception))
        self.assertEqual(json.loads(self.store.value)["refresh"], "refresh-old")

    def test_malformed_token_response_never_leaks_body(self):
        for updates in ({"access_token": "secret"}, {"expires_in": float("inf")}, {"expires_in": True}, {"expires_in": 0}):
            self.post.side_effect = None
            self.post.return_value = token_response(**updates)
            with self.assertRaisesRegex(RuntimeError, "Invalid Codex OAuth") as error:
                codex._token_request({"grant_type": "refresh_token"})
            self.assertNotIn("secret", str(error.exception))

    def test_refresh_can_keep_nonrotated_refresh_token_but_not_switch_account(self):
        self.post.side_effect = None
        self.post.return_value = token_response(refresh_token=None)
        value = codex._token_request({}, credentials())
        self.assertEqual(value["refresh"], "refresh-old")
        self.post.return_value = token_response(access_token=jwt("other-account"))
        with self.assertRaises(RuntimeError):
            codex._token_request({}, credentials())

    def test_corrupt_or_missing_credentials_do_not_send_requests(self):
        for value in ({}, credentials(expires=float("nan")), credentials(account_id=None), credentials(access=123)):
            self.store.save(json.dumps(value))
            with self.assertRaises(codex.AuthRequiredError):
                codex._credentials()
        self.store.value = "bad-json"
        with self.assertRaisesRegex(RuntimeError, "Cannot read protected"):
            codex._credentials()
        self.post.assert_not_called()

    def test_logout_clears_only_own_store(self):
        codex.sign_out()
        self.assertEqual(json.loads(self.store.value), {})
        self.assertFalse(codex.auth_status()["signed_in"])
        self.post.assert_not_called()

    def test_batch_contract_401_refresh_and_replay(self):
        with patch.object(codex, "_wav_bytes", return_value=b"wav"):
            self.post.side_effect = [httpx.Response(401), token_response(access_token=jwt(nonce=2)), httpx.Response(200, json={"text": " hello "})]
            self.assertEqual(codex.transcribe("unused.wav"), "hello")
        first, refresh, replay = self.post.call_args_list
        self.assertEqual(first.args[0], "https://chatgpt.com/backend-api/transcribe")
        self.assertEqual(refresh.args[0], codex.TOKEN_URL)
        self.assertEqual(first.kwargs["files"], {"file": ("audio.wav", b"wav", "audio/wav")})
        self.assertEqual(first.kwargs["headers"]["ChatGPT-Account-Id"], "account-1")
        self.assertNotIn("data", first.kwargs)
        self.assertNotEqual(first.kwargs["headers"]["Authorization"], replay.kwargs["headers"]["Authorization"])

    def test_second_401_requires_login_without_retry_loop(self):
        with patch.object(codex, "_wav_bytes", return_value=b"wav"):
            self.post.side_effect = [httpx.Response(401), token_response(), httpx.Response(401)]
            with self.assertRaises(codex.AuthRequiredError):
                codex.transcribe("unused.wav")
        self.assertEqual(self.post.call_count, 3)
        self.assertFalse(codex.auth_status()["signed_in"])

    def test_permission_quota_and_server_errors_do_not_refresh_or_fallback(self):
        for status in (403, 404, 429, 500, 302):
            with self.subTest(status=status), patch.object(codex, "_wav_bytes", return_value=b"wav"):
                self.post.reset_mock()
                self.post.side_effect = None
                self.post.return_value = httpx.Response(status, text="secret-response")
                with self.assertRaises(RuntimeError) as error:
                    codex.transcribe("unused.wav")
                self.assertNotIn("secret-response", str(error.exception))
                self.post.assert_called_once()
                self.assertEqual(json.loads(self.store.value)["refresh"], "refresh-old")

    def test_empty_text_is_silence_and_malformed_text_is_error(self):
        with patch.object(codex, "_wav_bytes", return_value=b"wav"):
            self.post.side_effect = None
            self.post.return_value = httpx.Response(200, json={"text": ""})
            self.assertEqual(codex.transcribe("unused.wav"), "")
            for data in ({}, {"text": None}, ["text"]):
                self.post.return_value = httpx.Response(200, json=data)
                with self.assertRaisesRegex(RuntimeError, "Invalid Codex transcription"):
                    codex.transcribe("unused.wav")

    def test_browser_callback_exchanges_code_and_persists_new_login(self):
        from http.client import HTTPConnection
        from http.server import HTTPServer
        port = []
        callbacks = []
        results = []

        class EphemeralServer(HTTPServer):
            def __init__(self, address, handler):
                super().__init__(("127.0.0.1", 0), handler)
                port.append(self.server_port)

        def open_browser(url):
            state = parse_qs(urlsplit(url).query)["state"][0]
            def callback():
                conn = HTTPConnection("127.0.0.1", port[0], timeout=5)
                conn.request("GET", f"/auth/callback?state={state}&code=test-code")
                response = conn.getresponse()
                results.append(response.status)
                response.read()
                conn.close()
            thread = threading.Thread(target=callback, daemon=True)
            callbacks.append(thread)
            thread.start()
            return True

        self.browser.side_effect = open_browser
        self.post.side_effect = None
        self.post.return_value = token_response(id_token=jwt(email="new@example.invalid"))
        with patch.object(codex, "HTTPServer", EphemeralServer):
            status = codex.sign_in(timeout=5)
        for thread in callbacks:
            thread.join(timeout=5)
        self.assertEqual(results, [200])
        self.assertEqual(status["account"], "new@example.invalid")
        self.assertEqual(json.loads(self.store.value)["refresh"], "refresh-new")
        data = self.post.call_args.kwargs["data"]
        self.assertEqual(data["grant_type"], "authorization_code")
        self.assertEqual(data["code"], "test-code")
        self.assertEqual(data["redirect_uri"], codex.REDIRECT_URI)
        self.assertIn("code_verifier", data)

    def test_invalid_login_timeout_is_rejected_before_auth_resources(self):
        with patch.object(codex, "_store") as store, patch.object(codex, "HTTPServer") as server:
            for value in (float("inf"), float("nan"), 0, -1, True, "180", None):
                with self.subTest(timeout=value), self.assertRaisesRegex(ValueError, "positive finite"):
                    codex.sign_in(timeout=value)
            store.assert_not_called()
            server.assert_not_called()
        self.post.assert_not_called()
        self.browser.assert_not_called()

    def test_signin_timeout_cancellation_and_port_conflict(self):
        # No listening sockets or browser are needed to exercise these exits.
        class FakeServer:
            def __init__(self, *args):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        self.browser.side_effect = None
        self.browser.return_value = True
        with patch.object(codex, "HTTPServer", FakeServer):
            with patch.object(codex.time, "monotonic", side_effect=[0, 2]), self.assertRaisesRegex(RuntimeError, "timed out"):
                codex.sign_in(timeout=1)
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                codex.sign_in(cancelled=lambda: True)
            self.browser.return_value = False
            with self.assertRaisesRegex(RuntimeError, "default browser"):
                codex.sign_in()
        class BusyServer:
            def __init__(self, *args):
                raise OSError("port busy")
        with patch.object(codex, "HTTPServer", BusyServer):
            with self.assertRaisesRegex(RuntimeError, "port 1455"):
                codex.sign_in()
        self.post.assert_not_called()


class CodexAudioAndRoutingTest(unittest.TestCase):
    def test_no_plaintext_fallback_or_browser_when_secure_storage_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(codex, "_AUTH_PATH", Path(tmp) / "auth.bin"), \
                patch("msal_extensions.build_encrypted_persistence", side_effect=RuntimeError("unavailable")), \
                patch.object(codex.webbrowser, "open") as browser:
            with self.assertRaisesRegex(RuntimeError, "no plaintext fallback"):
                codex.sign_in()
            browser.assert_not_called()
            self.assertFalse(codex._AUTH_PATH.exists())

    def test_wav_is_resampled_to_24khz_mono_pcm16(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.wav"
            sf.write(path, np.ones((1600, 2)) * 0.1, 16000)
            result = codex._wav_bytes(path)
        with sf.SoundFile(io.BytesIO(result)) as audio:
            self.assertEqual((audio.samplerate, audio.channels, audio.subtype), (24000, 1, "PCM_16"))
            self.assertEqual(audio.frames, 2400)

    def test_audio_limits_checked_before_network(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as tmp, patch("httpx.post") as post:
            path = Path(tmp) / "clip.wav"
            for samples in (np.zeros(0), np.zeros(121 * 8000)):
                sf.write(path, samples, 8000)
                with self.assertRaises(ValueError):
                    codex.transcribe(path)
            post.assert_not_called()

    def test_cli_dispatch_and_replacements_do_not_load_whisper(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(codex, "transcribe", return_value="raw word"):
            path = Path(tmp) / "clip.wav"
            path.touch()
            result = cli.transcribe_audio(path, "zh", "small", "codex", "unused", "unused",
                                          replacement_pairs=["word=text"], replacements_file=None)
        self.assertEqual(result["raw_text"], "raw word")
        self.assertEqual(result["plain_text"], "raw text")
        args = cli.build_parser().parse_args(["auth", "login", "codex"])
        self.assertEqual((args.command, args.action, args.provider), ("auth", "login", "codex"))
        self.assertEqual(cli.build_parser().parse_args(["desktop", "--backend", "codex"]).backend, "codex")

    def test_mixed_provider_selection_and_unsupported_accounts(self):
        self.assertEqual(account_auth.required_providers("codex", "azure"), ("codex", "azure"))
        self.assertEqual(account_auth.required_providers("codex", "rules"), ("codex",))
        self.assertEqual(account_auth.required_providers("mlx", "rules"), ())
        clients = {"codex": Mock(), "azure": Mock()}
        clients["codex"].auth_status.return_value = {"signed_in": True}
        clients["azure"].auth_status.return_value = {"signed_in": False}
        with patch.object(account_auth, "client", side_effect=clients.__getitem__):
            self.assertEqual(account_auth.auth_status(("codex", "azure"))["provider"], "azure")
        with self.assertRaises(ValueError):
            account_auth.client("unsupported-provider")

    def test_hotkey_and_qt_transcription_route_without_local_model(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from bubble_buddy.qt_overlay import TranscribeWorker
        session = cli.HotkeySession(
            language="zh", model_name="small", backend="codex", mlx_model="unused",
            copy_to_clipboard=False, paste_to_active_app=False, submit_to_active_app=False,
            plain=True, save_text=None, hf_endpoint="unused", replacement_pairs=[], replacements_file=None,
        )
        with patch.object(session, "_ensure_model_loaded", side_effect=AssertionError("No Whisper")), \
                patch.object(cli, "transcribe_audio_codex", return_value={"plain_text": "recognized"}) as transcribe:
            session._prepare_backend()
            self.assertEqual(session._process_stream_preview(None, 12), (False, 12))
            self.assertEqual(session._transcribe_with_loaded_model(Path("clip.wav"))["plain_text"], "recognized")
            worker = TranscribeWorker(
                Path("clip.wav"), "small", "codex", "unused", "zh", "unused", [], None,
                "off", None, False, "zh-en", "rules", "unused",
            )
            results, errors = [], []
            worker.finished_text.connect(lambda raw, polished: results.append((raw, polished)))
            worker.failed.connect(errors.append)
            worker.run()
            self.assertEqual(results, [("recognized", "recognized")])
            self.assertEqual(errors, [])
            self.assertEqual(transcribe.call_count, 2)

    def test_auth_errors_are_unknown_not_a_false_signed_in_claim(self):
        with patch.object(codex, "auth_status", side_effect=RuntimeError("network temporarily unavailable")):
            status = account_auth.auth_status(("codex",))
        self.assertIsNone(status["signed_in"])
        self.assertIn("network", status["error"])

    def test_qt_banner_handles_coded_provider_and_ignores_stale_status(self):
        from types import SimpleNamespace
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from bubble_buddy.qt_overlay import QTimer, VoiceDesktop
        desktop = SimpleNamespace(
            _account_providers=lambda: ("codex",), _account_text=lambda key: key,
            signin_btn=Mock(), error=Mock(), _refit_for_signin=Mock(), _check_auth_async=Mock(),
        )
        VoiceDesktop._apply_auth_status(desktop, {"providers": ("codex",), "provider": "codex", "signed_in": False})
        self.assertEqual(desktop._signin_provider, "codex")
        desktop.signin_btn.setVisible.assert_called_with(True)
        desktop.signin_btn.reset_mock()
        VoiceDesktop._apply_auth_status(desktop, {"providers": ("codex",), "provider": "codex", "signed_in": None, "error": "offline"})
        desktop.error.setText.assert_called_with("offline")
        desktop.signin_btn.setVisible.assert_not_called()
        desktop.signin_btn.setText.assert_not_called()
        with patch.object(QTimer, "singleShot") as timer:
            VoiceDesktop._apply_auth_status(desktop, {"providers": ("azure",), "signed_in": False})
            timer.assert_called_once_with(100, desktop._check_auth_async)
        desktop.signin_btn.setVisible.assert_not_called()

    def test_qt_worker_routes_codex_and_settings_hide_azure(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from bubble_buddy.qt_overlay import AuthStatusWorker, SignInWorker, _field_applies
        self.assertFalse(_field_applies("azure.endpoint", "codex", "rules"))
        self.assertFalse(_field_applies("model", "codex", "rules"))
        self.assertTrue(_field_applies("_codex_note", "codex", "rules"))
        with patch.object(account_auth, "sign_in", return_value={"signed_in": True, "provider": "codex"}) as login:
            SignInWorker("codex").run()
            self.assertEqual(login.call_args.args, ("codex",))
        with patch.object(account_auth, "auth_status", return_value={"signed_in": True}) as status:
            AuthStatusWorker(("codex", "azure")).run()
            status.assert_called_once_with(("codex", "azure"))


@unittest.skipUnless(os.name == "nt", "Windows DPAPI persistence smoke test")
class ProtectedStorageTest(unittest.TestCase):
    def test_protected_atomic_roundtrip_and_logout_use_only_temp_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(codex, "_AUTH_PATH", Path(tmp) / "auth.bin"):
            with codex._store() as store:
                codex._save(store, credentials())
            self.assertNotIn(b"refresh-old", codex._AUTH_PATH.read_bytes())
            self.assertEqual(codex._credentials()["refresh"], "refresh-old")
            codex.sign_out()
            self.assertFalse(codex.auth_status()["signed_in"])
            self.assertEqual(list(Path(tmp).glob(".account-auth-*")), [])


if __name__ == "__main__":
    unittest.main()
