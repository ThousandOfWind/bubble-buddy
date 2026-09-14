import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from bubble_buddy import account_auth, cli, config, copilot_client as copilot
from bubble_buddy.polish import polish_text


def credential(**updates):
    return {"github_token": "github-secret", "access": "tid=old;proxy-ep=proxy.individual.githubcopilot.com",
            "expires": time.time() + 3600, **updates}


def response(status=200, **data):
    return httpx.Response(status, content=json.dumps(data))


def token_response(**updates):
    return response(token="tid=new;proxy-ep=proxy.individual.githubcopilot.com", expires_at=time.time() + 3600, **updates)


def device_response(**updates):
    return response(**{
        "device_code": "private-device-secret", "user_code": "ABCD-1234",
        "verification_uri": copilot.VERIFICATION_URL, "interval": 5, "expires_in": 900, **updates,
    })


def completed_response(text="rewritten"):
    return {"id": "resp_test", "object": "response", "created_at": 1, "status": "completed",
            "model": copilot.DEFAULT_MODEL,
            "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}]}


def sse_response(*events):
    body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body)


@contextmanager
def mock_responses_transport(handler):
    # Exercise the real SDK's SSE framing and response models without any network.
    from openai import OpenAI
    def make_client(**kwargs):
        assert kwargs["http_client"].follow_redirects is False
        kwargs["http_client"].close()
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        return OpenAI(**kwargs)
    with patch.object(copilot, "_RESPONSE_CLIENTS", {}), patch("openai.OpenAI", side_effect=make_client) as factory:
        try:
            yield factory
        finally:
            copilot._close_response_clients()


class MemoryStore:
    def __init__(self, value):
        self.value = json.dumps(value)
    def load(self):
        return self.value
    def save(self, value):
        self.value = value


class CopilotAuthTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(credential())
        lock = threading.Lock()
        @contextmanager
        def store():
            with lock:
                yield self.store
        self.enterContext(patch.object(copilot, "_store", store))
        self.enterContext(patch.object(copilot, "_MODEL_CACHE", None))
        self.http = self.enterContext(patch("httpx.request", side_effect=AssertionError("Unexpected provider request")))
        self.browser = self.enterContext(patch.object(copilot.webbrowser, "open", return_value=True))
        self.clock = 0
        self.waits = []
        self.enterContext(patch.object(copilot.time, "monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch.object(copilot, "_wait", side_effect=self.wait))

    def wait(self, seconds, deadline, cancelled):
        copilot._check_cancelled(cancelled)
        self.waits.append(seconds)
        self.clock = min(self.clock + seconds, deadline)

    def test_device_login_matches_pi_and_exchanges_two_distinct_tokens(self):
        notices = []
        self.http.side_effect = [device_response(), response(error="authorization_pending"),
                                 response(access_token="github-new"), token_response()]
        status = copilot.sign_in(on_code=notices.append)
        self.assertTrue(status["signed_in"])
        self.assertEqual(self.waits, [5, 5])
        self.assertEqual(notices, [{"user_code": "ABCD-1234", "verification_uri": copilot.VERIFICATION_URL, "expires_in": 900}])
        self.assertNotIn("private-device-secret", str(notices))
        self.browser.assert_called_once_with("https://github.com/login/device")
        device, pending, poll, token = self.http.call_args_list
        self.assertEqual(device.args, ("POST", copilot.DEVICE_URL))
        self.assertEqual(device.kwargs["data"], {"client_id": "Iv1.b507a08c87ecfe98", "scope": "read:user"})
        self.assertEqual(poll.kwargs["data"]["grant_type"], "urn:ietf:params:oauth:grant-type:device_code")
        self.assertEqual(poll.kwargs["data"]["device_code"], "private-device-secret")
        self.assertEqual(token.args, ("GET", copilot.TOKEN_URL))
        self.assertEqual(token.kwargs["headers"]["Authorization"], "Bearer github-new")
        self.assertEqual(json.loads(self.store.value)["github_token"], "github-new")
        self.assertNotIn("token", str(status))

    def test_default_poll_interval_and_slow_down_are_honored(self):
        device = json.loads(device_response().content)
        del device["interval"]
        self.http.side_effect = [response(**device), response(error="slow_down"),
                                 response(error="slow_down", interval=20),
                                 response(access_token="github-new"), token_response()]
        copilot.sign_in(on_code=lambda _: None)
        self.assertEqual(self.waits, [5, 10, 20])

    def test_decline_expiry_and_timeout_preserve_previous_login(self):
        for error in ("access_denied", "expired_token", "unexpected"):
            with self.subTest(error=error):
                self.http.side_effect = [device_response(), response(error=error, error_description="secret-body")]
                with self.assertRaises(RuntimeError) as caught:
                    copilot.sign_in(on_code=lambda _: None)
                self.assertNotIn("secret-body", str(caught.exception))
                self.assertEqual(json.loads(self.store.value)["github_token"], "github-secret")
        self.http.side_effect = [device_response()]
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            copilot.sign_in(on_code=lambda _: None, timeout=1)

    def test_cancel_before_or_during_login_never_saves_a_token(self):
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            copilot.sign_in(cancelled=lambda: True)
        self.http.assert_not_called()
        cancelled = [False]
        def notify(_):
            cancelled[0] = True
        self.http.side_effect = [device_response()]
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            copilot.sign_in(on_code=notify, cancelled=lambda: cancelled[0])
        self.browser.assert_not_called()
        self.assertEqual(json.loads(self.store.value)["github_token"], "github-secret")

    def test_browser_failure_still_allows_manual_device_login(self):
        self.browser.side_effect = OSError("no browser")
        self.http.side_effect = [device_response(), response(access_token="github-new"), token_response()]
        notices = []
        self.assertTrue(copilot.sign_in(on_code=notices.append)["signed_in"])
        self.assertEqual(notices[0]["verification_uri"], copilot.VERIFICATION_URL)

    def test_untrusted_verification_urls_are_not_opened(self):
        for uri in ("file:///tmp/app", "https://evil.test/login/device", "https://github.com.evil.test", "http://github.com/login/device"):
            self.http.side_effect = [device_response(verification_uri=uri)]
            with self.assertRaisesRegex(RuntimeError, "Untrusted"):
                copilot.sign_in(on_code=lambda _: None)
        self.browser.assert_not_called()

    def test_invalid_device_fields_and_timeout_are_rejected(self):
        for update in ({"expires_in": True}, {"interval": float("nan")}, {"user_code": "<script>"}, {"device_code": None}):
            self.http.side_effect = [device_response(**update)]
            with self.assertRaisesRegex(RuntimeError, "Invalid GitHub device"):
                copilot.sign_in(on_code=lambda _: None)
        for timeout in (0, -1, float("inf")):
            with self.assertRaises(ValueError):
                copilot.sign_in(timeout=timeout)
        self.browser.assert_not_called()

    def test_cached_status_exposes_no_tokens_and_never_opens_browser(self):
        status = copilot.auth_status()
        self.assertTrue(status["signed_in"])
        self.assertNotIn("github-secret", str(status))
        self.assertNotIn("access", status)
        self.http.assert_not_called()
        self.browser.assert_not_called()

    def test_expiry_exchanges_github_token_and_persists_copilot_token(self):
        self.store.save(json.dumps(credential(expires=0)))
        self.http.side_effect = [token_response()]
        self.assertIn("tid=new", copilot._credentials()["access"])
        self.assertIn("tid=new", copilot._credentials()["access"])
        self.http.assert_called_once()
        self.assertEqual(self.http.call_args.kwargs["headers"]["Authorization"], "Bearer github-secret")
        self.assertEqual(json.loads(self.store.value)["github_token"], "github-secret")

    def test_concurrent_refresh_and_stale_401_do_not_reexchange(self):
        self.store.save(json.dumps(credential(expires=0)))
        self.http.side_effect = [token_response()]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: copilot._credentials(), range(12)))
        copilot._credentials(rejected_access="tid=outdated")
        self.assertEqual(len(results), 12)
        self.http.assert_called_once()

    def test_revoked_github_token_requires_reauth_but_403_does_not_erase_it(self):
        self.store.save(json.dumps(credential(expires=0)))
        self.http.side_effect = [response(403)]
        with self.assertRaisesRegex(RuntimeError, "organization policy"):
            copilot._credentials()
        self.assertEqual(json.loads(self.store.value)["github_token"], "github-secret")
        self.http.side_effect = [response(401)]
        with self.assertRaises(copilot.AuthRequiredError):
            copilot._credentials()
        self.assertEqual(json.loads(self.store.value), {})
        self.assertFalse(copilot.auth_status()["signed_in"])

    def test_transient_refresh_errors_preserve_credentials_and_redact_details(self):
        self.store.save(json.dumps(credential(expires=0)))
        for result in (response(429, secret="hidden"), response(500, secret="hidden"), httpx.ConnectError("hidden")):
            self.http.side_effect = [result]
            with self.assertRaises(RuntimeError) as caught:
                copilot._credentials()
            self.assertNotIn("hidden", str(caught.exception))
            self.assertEqual(json.loads(self.store.value)["github_token"], "github-secret")

    def test_corrupt_or_missing_credentials_fail_without_using_other_apps(self):
        for value in ({}, credential(github_token=0), credential(reauth_required=True)):
            self.store.save(json.dumps(value))
            with self.assertRaises(copilot.AuthRequiredError):
                copilot._credentials()
        self.store.value = "corrupt"
        with self.assertRaisesRegex(RuntimeError, "Cannot read protected"):
            copilot._credentials()
        self.http.assert_not_called()

    def test_token_endpoint_validation_and_expiry(self):
        for data in ({"token": "secret", "expires_at": -1}, {"token": None, "expires_at": time.time() + 900},
                     {"token": "proxy-ep=evil.test", "expires_at": time.time() + 900}):
            self.http.side_effect = [response(**data)]
            with self.assertRaises(RuntimeError) as caught:
                copilot._exchange("github-secret")
            self.assertNotIn("github-secret", str(caught.exception))
        for access in ("proxy-ep=proxy.individual.githubcopilot.com.evil.test", "proxy-ep=proxy.business.githubcopilot.com/path",
                       "proxy-ep=", "proxy-ep=proxy.githubcopilot.com;proxy-ep=proxy.githubcopilot.com"):
            with self.assertRaisesRegex(RuntimeError, "Untrusted"):
                copilot._api_base(access)
        self.assertEqual(copilot._api_base("tid=x"), "https://api.individual.githubcopilot.com")
        self.assertEqual(copilot._api_base("tid=x;proxy-ep=proxy.business.githubcopilot.com"), "https://api.business.githubcopilot.com")

    def test_401_retries_once_with_fresh_auth_and_no_redirects(self):
        self.http.side_effect = [response(401), token_response(), response(data=[])]
        self.assertEqual(copilot._authorized_request("GET", "/models"), {"data": []})
        first, exchange, retry = self.http.call_args_list
        self.assertNotEqual(first.kwargs["headers"]["Authorization"], retry.kwargs["headers"]["Authorization"])
        self.assertEqual(exchange.args[1], copilot.TOKEN_URL)
        for call in self.http.call_args_list:
            self.assertFalse(call.kwargs["follow_redirects"])
        self.assertEqual(retry.kwargs["headers"]["X-Initiator"], "user")
        self.assertEqual(retry.kwargs["headers"]["Openai-Intent"], "conversation-edits")

    def test_repeated_401_surfaces_login_without_an_infinite_status_refresh(self):
        self.http.side_effect = [response(401), token_response(), response(401)]
        with self.assertRaises(copilot.AuthRequiredError):
            copilot._authorized_request("GET", "/models")
        self.assertFalse(copilot.auth_status()["signed_in"])
        self.assertEqual(self.http.call_count, 3)

    def test_non_auth_errors_never_refresh_or_retry(self):
        for status in (403, 404, 429, 500, 302):
            self.http.reset_mock()
            self.http.side_effect = [response(status, token="secret-body")]
            with self.assertRaises(RuntimeError) as caught:
                copilot._authorized_request("GET", "/models")
            self.assertNotIn("secret-body", str(caught.exception))
            self.http.assert_called_once()

    def test_model_catalog_filters_disabled_and_non_chat_models(self):
        self.http.side_effect = [response(data=[
            {"id": "gpt-4.1", "model_picker_enabled": True},
            {"id": "disabled", "model_picker_enabled": True, "policy": {"state": "disabled"}, "supported_endpoints": ["/chat/completions"]},
            {"id": "responses-only", "model_picker_enabled": True, "supported_endpoints": ["/responses"]},
            {"id": "unknown-contract", "model_picker_enabled": True},
            {"id": "chat-model", "model_picker_enabled": True, "supported_endpoints": ["/chat/completions"]},
            {"id": "policy-only", "policy": {"state": "enabled"}, "supported_endpoints": ["/chat/completions"]},
        ])]
        self.assertEqual(copilot.list_models(), ["chat-model", "gpt-4.1", "responses-only"])
        self.assertEqual(self.http.call_args.args[0], "GET")

    def test_policy_fallback_is_individual_only_and_never_enables_models(self):
        catalog = response(data=[{"id": "gpt-4.1", "policy": {"state": "enabled"}}])
        self.http.side_effect = [catalog]
        self.assertEqual(copilot.list_models(), ["gpt-4.1"])
        self.store.save(json.dumps(credential(access="proxy-ep=proxy.business.githubcopilot.com")))
        self.http.side_effect = [catalog]
        self.assertEqual(copilot.list_models(), [])
        self.assertTrue(all(call.args[0] == "GET" for call in self.http.call_args_list))

    def test_text_only_polish_uses_selected_model_prompts_and_no_tools(self):
        self.http.side_effect = [response(data=[{"id": "custom", "model_picker_enabled": True, "supported_endpoints": ["/chat/completions"]}]),
                                 response(choices=[{"message": {"content": " corrected "}, "finish_reason": "stop"}])]
        with patch.object(config, "load_config", return_value={"copilot_model": "custom"}):
            result = copilot.polish("original", context="editor context", language_preference="zh-en", mode_prompt="custom prompt")
        self.assertEqual(result, "corrected")
        request = self.http.call_args
        self.assertTrue(request.args[1].endswith("/chat/completions"))
        body = request.kwargs["json"]
        self.assertEqual(body["model"], "custom")
        self.assertIn("custom prompt", body["messages"][0]["content"])
        self.assertIn("original", body["messages"][1]["content"])
        self.assertIn("editor context", body["messages"][1]["content"])
        self.assertNotIn("files", request.kwargs)
        self.assertNotIn("tools", body)
        self.assertFalse(body["stream"])

    def test_unavailable_model_does_not_upload_transcript(self):
        self.http.side_effect = [response(data=[])]
        with self.assertRaisesRegex(RuntimeError, "Selected Copilot model"):
            copilot.polish("private transcript")
        self.http.assert_called_once()
        self.assertNotIn("private transcript", str(self.http.call_args))

    def test_empty_refused_truncated_and_malformed_output_are_not_used(self):
        for choice in ({"message": {"content": "cut off"}, "finish_reason": "length"},
                       {"message": {"content": "", "refusal": "refused"}}, {"message": {}}, None):
            with patch.object(copilot, "_model_catalog", return_value={"gpt-4.1": "/chat/completions"}):
                self.http.side_effect = [response(choices=[choice])]
                with patch.object(config, "load_config", return_value={"copilot_model": "gpt-4.1"}), self.assertRaisesRegex(RuntimeError, "incomplete rewrite"):
                    copilot.polish("source")
        self.http.reset_mock()
        self.assertEqual(copilot.polish(""), "")
        self.http.assert_not_called()

    def test_recommended_default_uses_responses_low_effort_and_concise_text(self):
        self.http.side_effect = [response(data=[{"id": "gpt-5.6-luna", "model_picker_enabled": True,
                                                "supported_endpoints": ["/responses"]}])]
        requests = []
        def handler(request):
            requests.append(request)
            return sse_response(
                {"type": "response.output_text.delta", "delta": "untrusted partial"},
                {"type": "response.completed", "response": completed_response("保留原意的文字。")},
            )
        with mock_responses_transport(handler) as factory, patch.object(config, "load_config", return_value={}):
            text = copilot.polish("original", "editor context", "zh-en", "custom cleanup")
        self.assertEqual(text, "保留原意的文字。")
        self.assertEqual(copilot.DEFAULT_MODEL, "gpt-5.6-luna")
        self.assertEqual(config.DEFAULTS["copilot_model"], copilot.DEFAULT_MODEL)
        self.assertEqual(config.DEFAULTS["copilot_reasoning_effort"], copilot.DEFAULT_REASONING_EFFORT)
        self.assertEqual(config.DEFAULTS["copilot_max_output_tokens"], copilot.DEFAULT_MAX_OUTPUT_TOKENS)
        body = json.loads(requests[0].content)
        self.assertEqual(requests[0].url.path, "/responses")
        self.assertEqual(body["model"], "gpt-5.6-luna")
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertEqual(body["text"], {"verbosity": "low"})
        self.assertEqual(body["max_output_tokens"], 2048)
        self.assertTrue(body["stream"])
        self.assertFalse(body["store"])
        self.assertEqual(body["input"][0]["role"], "developer")
        self.assertIn("original", body["input"][1]["content"])
        self.assertIn("editor context", body["input"][1]["content"])
        for field in ("temperature", "tools", "max_tokens", "service_tier", "previous_response_id"):
            self.assertNotIn(field, body)
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
        self.assertEqual(requests[0].headers["X-Initiator"], "user")

    def test_responses_connections_reused_without_storing_authorization(self):
        requests = []
        def handler(request):
            requests.append(request)
            return sse_response({"type": "response.completed", "response": completed_response()})
        body = {"model": copilot.DEFAULT_MODEL, "input": "hello", "stream": True, "store": False}
        with mock_responses_transport(handler) as factory:
            copilot._responses_request("tid=first", body)
            copilot._responses_request("tid=second", body)
            factory.assert_called_once()
            self.assertEqual(factory.call_args.kwargs["api_key"], "unused")
            self.assertNotIn("default_headers", factory.call_args.kwargs)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer tid=first")
        self.assertEqual(requests[1].headers["Authorization"], "Bearer tid=second")

    def test_responses_401_refreshes_once_using_same_payload(self):
        self.http.side_effect = [token_response()]
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return response(401, error={"message": "secret-provider-body"})
            return sse_response({"type": "response.completed", "response": completed_response()})
        with mock_responses_transport(handler):
            result = copilot._authorized_action(lambda access: copilot._responses_request(access, {
                "model": copilot.DEFAULT_MODEL, "input": "hello", "stream": True, "store": False,
            }))
        self.assertEqual(copilot._response_text(result), "rewritten")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].content, requests[1].content)
        self.assertNotEqual(requests[0].headers["Authorization"], requests[1].headers["Authorization"])
        self.http.assert_called_once()

    def test_responses_errors_are_redacted_without_paid_retries_or_partial_output(self):
        import traceback
        for status in (401, 403, 429, 500, 302):
            requests = []
            def handler(request):
                requests.append(request)
                return response(status, error={"message": "secret-provider-body"})
            with mock_responses_transport(handler), self.assertRaises(RuntimeError) as caught:
                copilot._responses_request(credential()["access"], {"model": copilot.DEFAULT_MODEL, "input": "hello", "stream": True})
            self.assertNotIn("secret-provider-body", "".join(traceback.format_exception(caught.exception)))
            self.assertEqual(len(requests), 1)
        for event in ({"type": "response.incomplete", "response": {"status": "incomplete"}},
                      {"type": "response.failed", "response": {"status": "failed"}},
                      {"type": "error", "message": "secret-provider-body"}):
            with mock_responses_transport(lambda _: sse_response(event)), self.assertRaisesRegex(RuntimeError, "incomplete rewrite"):
                copilot._responses_request(credential()["access"], {"model": copilot.DEFAULT_MODEL, "input": "hello", "stream": True})
        with mock_responses_transport(lambda _: sse_response({"type": "response.output_text.delta", "delta": "partial"})), \
                self.assertRaisesRegex(RuntimeError, "incomplete rewrite"):
            copilot._responses_request(credential()["access"], {"model": copilot.DEFAULT_MODEL, "input": "hello", "stream": True})

    def test_responses_refusal_incomplete_and_reasoning_are_not_pasted(self):
        value = completed_response()
        value["output"].insert(0, {"type": "reasoning", "summary": [{"text": "private reasoning"}]})
        self.assertEqual(copilot._response_text(value), "rewritten")
        for bad in ({**value, "status": "incomplete"}, {**value, "incomplete_details": {"reason": "max_output_tokens"}},
                    {**value, "output": []}, {**value, "error": {"message": "hidden"}}):
            with self.assertRaisesRegex(RuntimeError, "incomplete rewrite"):
                copilot._response_text(bad)
        refused = completed_response()
        refused["output"][0]["content"] = [{"type": "refusal", "refusal": "hidden"}]
        with self.assertRaises(RuntimeError):
            copilot._response_text(refused)

    def test_model_cache_avoids_extra_gets_but_expires_and_tracks_credentials(self):
        catalog = response(data=[{"id": copilot.DEFAULT_MODEL, "model_picker_enabled": True}])
        self.http.side_effect = [catalog, catalog, catalog, catalog]
        self.assertEqual(copilot._model_catalog(), {copilot.DEFAULT_MODEL: "/responses"})
        self.assertEqual(copilot._model_catalog(), {copilot.DEFAULT_MODEL: "/responses"})
        self.assertEqual(self.http.call_count, 1)
        copilot.list_models()  # an explicit CLI listing is always fresh
        self.assertEqual(self.http.call_count, 2)
        self.clock += copilot._MODEL_CACHE_TTL + 1
        copilot._model_catalog()
        self.assertEqual(self.http.call_count, 3)
        self.store.save(json.dumps(credential(access="tid=different;proxy-ep=proxy.individual.githubcopilot.com")))
        copilot._model_catalog()
        self.assertEqual(self.http.call_count, 4)
        copilot.sign_out()
        self.assertIsNone(copilot._MODEL_CACHE)
        with self.assertRaises(copilot.AuthRequiredError):
            copilot._model_catalog()

    def test_two_polishes_use_one_catalog_and_two_inference_requests(self):
        catalog = response(data=[{"id": "custom", "model_picker_enabled": True, "supported_endpoints": ["/chat/completions"]}])
        answer = response(choices=[{"message": {"content": "rewritten"}, "finish_reason": "stop"}])
        self.http.side_effect = [catalog, answer, answer]
        with patch.object(config, "load_config", return_value={"copilot_model": "custom"}):
            self.assertEqual(copilot.polish("source"), "rewritten")
            self.assertEqual(copilot.polish("source"), "rewritten")
        self.assertEqual([call.args[0] for call in self.http.call_args_list], ["GET", "POST", "POST"])

    def test_tuning_is_validated_before_network_and_can_be_overridden(self):
        for settings in ({"copilot_reasoning_effort": "xhigh"}, {"copilot_max_output_tokens": True},
                         {"copilot_max_output_tokens": 15}, {"copilot_max_output_tokens": 20000},
                         {"copilot_max_output_tokens": "2048"}):
            with patch.object(config, "load_config", return_value=settings), self.assertRaises(ValueError):
                copilot.polish("source")
        self.http.assert_not_called()
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return sse_response({"type": "response.completed", "response": completed_response()})
        with patch.object(copilot, "_model_catalog", return_value={copilot.DEFAULT_MODEL: "/responses"}), \
                patch.object(config, "load_config", return_value={"copilot_reasoning_effort": "medium", "copilot_max_output_tokens": 4096}), \
                mock_responses_transport(handler):
            copilot.polish("source")
        self.assertEqual(requests[0]["reasoning"], {"effort": "medium"})
        self.assertEqual(requests[0]["max_output_tokens"], 4096)

    def test_recommended_profile_requires_its_verified_responses_route(self):
        self.http.side_effect = [response(data=[{
            "id": copilot.DEFAULT_MODEL, "model_picker_enabled": True,
            "supported_endpoints": ["/chat/completions"],
        }])]
        self.assertNotIn(copilot.DEFAULT_MODEL, copilot.list_models())
        self.http.assert_called_once()

    def test_unknown_response_model_does_not_get_unverified_tuning(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return sse_response({"type": "response.completed", "response": completed_response()})
        with patch.object(copilot, "_model_catalog", return_value={"other-model": "/responses"}), \
                patch.object(config, "load_config", return_value={"copilot_model": "other-model"}), \
                mock_responses_transport(handler):
            copilot.polish("source")
        self.assertNotIn("reasoning", requests[0])
        self.assertNotIn("text", requests[0])

    def test_logout_forgets_only_own_store(self):
        copilot.sign_out()
        self.assertEqual(json.loads(self.store.value), {})
        self.assertFalse(copilot.auth_status()["signed_in"])
        self.http.assert_not_called()


class CopilotIntegrationTest(unittest.TestCase):
    def test_required_accounts_include_polish_but_not_disabled_polish(self):
        self.assertEqual(account_auth.required_providers("faster-whisper", "copilot", "auto"), ("copilot",))
        self.assertEqual(account_auth.required_providers("mlx", "copilot", "off"), ())
        self.assertEqual(account_auth.required_providers("codex", "copilot", "auto"), ("codex", "copilot"))
        self.assertEqual(account_auth.required_providers("azure", "copilot", "auto"), ("azure", "copilot"))
        self.assertEqual(account_auth.provider_name("copilot"), "GitHub Copilot")
        self.assertIs(account_auth.client("copilot"), copilot)

    def test_cli_commands_dispatch_without_exposing_saved_tokens(self):
        args = cli.build_parser().parse_args(["desktop", "--backend", "faster-whisper", "--polish-engine", "copilot"])
        self.assertEqual(args.polish_engine, "copilot")
        with patch.object(config, "load_config", return_value={}), patch("bubble_buddy.diagnostics.setup_logging"):
            for action, method, output in (("status", "auth_status", {"signed_in": True}),
                                           ("models", "list_models", ["gpt-4.1"]),
                                           ("logout", "sign_out", None),
                                           ("login", "sign_in", {"signed_in": True})):
                with patch.object(copilot, method, return_value=output) as call, redirect_stdout(io.StringIO()) as stdout:
                    cli.main(["auth", action, "copilot"])
                    call.assert_called_once()
                    self.assertNotIn("github-secret", stdout.getvalue())

    def test_config_accepts_grouped_model_and_top_level_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            with patch.object(config, "_candidate_paths", return_value=[path]), patch.object(config, "_CACHE", None):
                path.write_text(json.dumps({"polish": {"engine": "copilot", "mode": "auto", "copilot_model": "custom",
                                                      "copilot_reasoning_effort": "medium", "copilot_max_output_tokens": 4096}}))
                cfg = config.load_config(reload=True)
                self.assertEqual((cfg["polish_engine"], cfg["copilot_model"]), ("copilot", "custom"))
                self.assertEqual((cfg["copilot_reasoning_effort"], cfg["copilot_max_output_tokens"]), ("medium", 4096))
                config.save_config({"copilot_model": "override", "copilot_reasoning_effort": "low", "copilot_max_output_tokens": 2048})
                self.assertEqual(config.load_config()["copilot_model"], "override")
                self.assertEqual(config.load_config()["copilot_reasoning_effort"], "low")
                self.assertEqual(config.load_config()["copilot_max_output_tokens"], 2048)

    def test_polish_pipeline_preserves_intent_and_skips_network_when_off(self):
        with patch.object(copilot, "polish", return_value="已经完成检查。") as rewrite:
            result = polish_text("能帮我检查这个配置吗？", "copilot", engine="copilot", live_context="context")
            self.assertIn("？", result)
            self.assertNotIn("已经完成", result)
            self.assertIn("context", rewrite.call_args.kwargs["context"])
        with patch.object(copilot, "polish", side_effect=AssertionError("No cloud call")):
            polish_text("hello", "off", engine="copilot")
            polish_text("", "copilot", engine="copilot")

    def test_local_whisper_qt_route_keeps_raw_text_when_copilot_fails(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from bubble_buddy.qt_overlay import TranscribeWorker, _field_applies
        model = Mock()
        model.transcribe.return_value = ([SimpleNamespace(text="original transcript")], None)
        fake_whisper = SimpleNamespace(WhisperModel=Mock(return_value=model))
        with patch.dict(sys.modules, {"faster_whisper": fake_whisper}), \
                patch.object(copilot, "polish", side_effect=copilot.AuthRequiredError()):
            worker = TranscribeWorker(Path("local.wav"), "small", "faster-whisper", "unused", "zh", "unused",
                                      [], None, "copilot", None, False, "zh-en", "copilot", "unused")
            raw, errors, finished = [], [], []
            worker.raw_text_ready.connect(raw.append)
            worker.failed.connect(errors.append)
            worker.finished_text.connect(lambda a, b: finished.append((a, b)))
            worker.run()
        self.assertEqual(raw, ["original transcript"])
        self.assertEqual(finished, [])
        self.assertIn("sign-in required", errors[0])
        self.assertTrue(_field_applies("copilot_model", "mlx", "copilot"))
        self.assertFalse(_field_applies("copilot_model", "mlx", "rules"))
        self.assertTrue(_field_applies("polish_prompts.dev", "mlx", "copilot"))
        self.assertFalse(_field_applies("azure.endpoint", "mlx", "copilot"))

    def test_switching_to_local_copilot_stops_old_azure_refresh(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from bubble_buddy import azure_client
        from bubble_buddy.qt_overlay import VoiceDesktop
        with patch.object(azure_client, "refresh_token") as refresh:
            VoiceDesktop._refresh_azure_token(SimpleNamespace(backend="faster-whisper", polish_engine="copilot"))
        refresh.assert_not_called()

    def test_qt_worker_delivers_device_code_and_status_via_signals(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from bubble_buddy.qt_overlay import SignInWorker
        def login(**kwargs):
            kwargs["on_code"]({"user_code": "ABCD-1234", "verification_uri": copilot.VERIFICATION_URL})
            return {"signed_in": True}
        with patch.object(copilot, "sign_in", side_effect=login):
            worker = SignInWorker("copilot")
            notices, statuses = [], []
            worker.device_code.connect(notices.append)
            worker.signed_in.connect(statuses.append)
            worker.run()
        self.assertEqual(notices[0]["user_code"], "ABCD-1234")
        self.assertEqual(statuses, [{"signed_in": True, "provider": "copilot"}])

    def test_shared_secure_storage_has_no_plaintext_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(copilot, "_AUTH_PATH", Path(tmp) / "auth.bin"), \
                patch("msal_extensions.build_encrypted_persistence", side_effect=RuntimeError("unavailable")), \
                patch("httpx.request") as http, patch.object(copilot.webbrowser, "open") as browser:
            with self.assertRaisesRegex(RuntimeError, "no plaintext fallback"):
                copilot.sign_in()
            http.assert_not_called()
            browser.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI smoke test")
    def test_copilot_and_codex_credentials_are_isolated_and_encrypted(self):
        from bubble_buddy import codex_client
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(copilot, "_AUTH_PATH", Path(tmp) / "copilot.bin"), \
                patch.object(codex_client, "_AUTH_PATH", Path(tmp) / "codex.bin"):
            with copilot._store() as store:
                copilot._save(store, credential())
            with codex_client._store() as store:
                codex_client._save(store, {"test": "codex-secret"})
            self.assertNotIn(b"github-secret", copilot._AUTH_PATH.read_bytes())
            codex_bytes = codex_client._AUTH_PATH.read_bytes()
            copilot.sign_out()
            self.assertEqual(codex_client._AUTH_PATH.read_bytes(), codex_bytes)
            self.assertFalse(copilot.auth_status()["signed_in"])
            self.assertEqual(list(Path(tmp).glob(".account-auth-*")), [])


if __name__ == "__main__":
    unittest.main()
