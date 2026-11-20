"""The AI backends: Claude through the Anthropic SDK, Ollama and any
OpenAI-compatible server over HTTP.

Claude is tested against the real SDK with a transport that answers like the
API and records what it was sent, so the request on the wire is what is checked,
not a call into a fake. Nothing here reaches the network or needs a key.

Needs the dependencies, so it runs in the dependency-installing CI job.
"""
import json
import os
import tempfile
import unittest
import warnings
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2
import requests
import yaml

from vibe_cnc import claude_client
from vibe_cnc.claude_client import (
    AIClient, BACKENDS, DEFAULT_CLAUDE_MODEL, DEFAULT_GENERATE_PROMPT, DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_BASE_URL, DEFAULT_REVIEW_PROMPT, MAX_OUTPUT_TOKENS_WITHOUT_STREAMING, SECTION_OF,
    backend_of,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "sk-ant-test-123"


def message(text="Line 5: G21 missing", stop="end_turn", content=None, stop_details=None):
    body = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": DEFAULT_CLAUDE_MODEL,
        "content": content if content is not None else [{"type": "text", "text": text}],
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    if stop_details is not None:
        body["stop_details"] = stop_details
    return httpx2.Response(200, json=body)


def failure(status, kind, text, headers=None):
    # The API sends the request id in a header as well as in the body, and the
    # SDK reads the header.
    return httpx2.Response(
        status, headers={"request-id": "req_TEST", **(headers or {})},
        json={"type": "error", "error": {"type": kind, "message": text}, "request_id": "req_TEST"})


class FakeApi:
    """Answers like the API with whatever it was given, and records the requests."""

    def __init__(self, response):
        self.response = response
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def body(self, index=0):
        return json.loads(self.requests[index].content)


def settings(**ai):
    return SimpleNamespace(data={"ai": ai})


class ClaudeCase(unittest.TestCase):
    """Runs a call with the SDK's client talking to a FakeApi."""

    def run_claude(self, api, call, ai=None, env=None):
        real = anthropic.Anthropic
        built = []

        def build(**kwargs):
            built.append(dict(kwargs))
            # No retries in a test that answers with an error, or it would wait.
            # The kwargs recorded above are what the client asked for.
            kwargs.setdefault("max_retries", 0)
            return real(http_client=httpx2.Client(transport=httpx2.MockTransport(api)), **kwargs)

        environment = {"ANTHROPIC_API_KEY": KEY}
        environment.update(env or {})
        client = AIClient(settings(mode="claude", anthropic=ai or {}))
        with mock.patch.object(anthropic, "Anthropic", side_effect=build), \
                mock.patch.dict(os.environ, environment):
            return call(client), built

    def ask(self, api, prompt="Review this", system=None, ai=None, env=None):
        return self.run_claude(api, lambda c: c.ask_claude(prompt, system), ai, env)[0]


class ClaudeRequestTests(ClaudeCase):
    def test_it_asks_for_the_current_sonnet_by_default(self):
        api = FakeApi(message())

        self.ask(api)

        self.assertEqual(api.body()["model"], "claude-sonnet-5-5")

    def test_the_retired_default_is_gone_from_everything_that_ships(self):
        retired = "claude-sonnet-4-20250514"
        for name in ("vibe_cnc/claude_client.py", "vibe_cnc/dialogs/settings.py",
                     "config.example.yaml"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
                text = handle.read()
            # The config template names it once, to say it is retired.
            allowed = 1 if name == "config.example.yaml" else 0
            self.assertLessEqual(text.count(retired), allowed, name)

    def test_the_request_carries_the_prompt_the_instructions_and_the_effort(self):
        api = FakeApi(message())

        self.ask(api, prompt="Check N10", system="You are a strict CNC reviewer.",
                 ai={"effort": "medium"})

        body = api.body()
        self.assertEqual(body["messages"], [{"role": "user", "content": "Check N10"}])
        self.assertEqual(body["system"], "You are a strict CNC reviewer.")
        self.assertEqual(body["output_config"], {"effort": "medium"})

    def test_a_request_without_instructions_sends_no_system_field(self):
        api = FakeApi(message())

        self.ask(api, system=None)

        self.assertNotIn("system", api.body())

    def test_effort_is_left_out_when_the_config_has_none(self):
        # A model without the setting (Haiku 4.5, Sonnet 4.5) rejects it.
        api = FakeApi(message())

        self.ask(api, ai={"effort": ""})

        self.assertNotIn("output_config", api.body())

    def test_there_is_room_for_the_thinking_as_well_as_the_answer(self):
        # 800 was the old limit. Thinking counts against it, so it left a model
        # that thinks by default nothing to answer with.
        api = FakeApi(message())

        self.ask(api)

        self.assertEqual(api.body()["max_tokens"], 16000)

    def test_a_limit_the_sdk_would_refuse_is_cut_back_to_what_it_accepts(self):
        # Above about 21 300 tokens a request that is not streamed raises.
        api = FakeApi(message())

        ok, _text = self.ask(api, ai={"max_output_tokens": 64000})

        self.assertTrue(ok)
        self.assertEqual(api.body()["max_tokens"], MAX_OUTPUT_TOKENS_WITHOUT_STREAMING)

    def test_no_sampling_parameters_and_no_thinking_setting_are_sent(self):
        # temperature, top_p and top_k return a 400 on the newest API version,
        # and Sonnet 5.5 rejects thinking: disabled.
        api = FakeApi(message())

        self.ask(api)

        for field in ("temperature", "top_p", "top_k", "thinking"):
            self.assertNotIn(field, api.body())

    def test_the_key_is_read_from_the_configured_variable(self):
        api = FakeApi(message())

        self.ask(api, ai={"api_key_env": "MY_SHOP_KEY"}, env={"MY_SHOP_KEY": "sk-from-shop"})

        sent = [v for k, v in api.requests[0].headers.items()
                if k.lower() in ("authorization", "x-api-key")]
        self.assertTrue(any("sk-from-shop" in value for value in sent), sent)
        self.assertFalse(any(KEY in value for value in sent), sent)

    def test_the_sdk_keeps_its_own_retries(self):
        # Rate limits, overload and 5xx are retried by the SDK; the client must
        # not switch that off.
        _result, built = self.run_claude(FakeApi(message()), lambda c: c.ask_claude("x"))

        self.assertNotIn("max_retries", built[0])

    def test_a_whole_endpoint_from_an_older_config_still_works(self):
        api = FakeApi(message())

        ok, _text = self.ask(api, ai={"base_url": "https://api.anthropic.com/v1/messages"})

        self.assertTrue(ok)
        self.assertEqual(api.requests[0].url.path, "/v1/messages")

    def test_base_urls_are_reduced_to_what_the_sdk_expects(self):
        for given, expected in (("https://api.anthropic.com/v1/messages", "https://api.anthropic.com"),
                                ("https://api.anthropic.com/v1/", "https://api.anthropic.com"),
                                ("https://proxy.example.com/", "https://proxy.example.com"),
                                ("", None), (None, None)):
            with self.subTest(given):
                self.assertEqual(claude_client._sdk_base_url(given), expected)


class ClaudeAnswerTests(ClaudeCase):
    def test_the_text_is_returned_and_the_thinking_is_not(self):
        api = FakeApi(message(content=[
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Line 5: G21 missing"},
        ]))

        self.assertEqual(self.ask(api), (True, "Line 5: G21 missing"))

    def test_several_text_blocks_are_joined(self):
        api = FakeApi(message(content=[{"type": "text", "text": "One. "},
                                       {"type": "text", "text": "Two."}]))

        self.assertEqual(self.ask(api), (True, "One. Two."))

    def test_an_answer_cut_off_at_the_limit_says_so(self):
        api = FakeApi(message(text="Line 5: G21 mis", stop="max_tokens"))

        ok, text = self.ask(api, ai={"max_output_tokens": 4000})

        self.assertTrue(ok)
        self.assertIn("Line 5: G21 mis", text)
        self.assertIn("cut off at 4000 tokens", text)
        self.assertIn("max_output_tokens", text)

    def test_thinking_that_used_the_whole_budget_is_not_shown_as_raw_json(self):
        # The old client printed str(response)[:1000] when there was no text.
        api = FakeApi(message(stop="max_tokens",
                              content=[{"type": "thinking", "thinking": "", "signature": "sig"}]))

        ok, text = self.ask(api, ai={"max_output_tokens": 800})

        self.assertFalse(ok)
        self.assertIn("800", text)
        self.assertIn("thinking counts too", text)
        self.assertNotIn("{", text)

    def test_a_refusal_is_reported_with_its_category(self):
        api = FakeApi(message(stop="refusal", content=[],
                              stop_details={"type": "refusal", "category": "cyber",
                                            "explanation": "Declined."}))

        ok, text = self.ask(api)

        self.assertFalse(ok)
        self.assertIn("declined", text)
        self.assertIn("cyber", text)

    def test_an_answer_with_no_text_for_another_reason_says_which(self):
        api = FakeApi(message(stop="end_turn", content=[]))

        ok, text = self.ask(api)

        self.assertFalse(ok)
        self.assertIn("no text", text)
        self.assertIn("end_turn", text)


class ClaudeErrorTests(ClaudeCase):
    def test_each_failure_says_what_to_do(self):
        cases = (
            (401, "authentication_error", "invalid x-api-key", {}, ["rejected", "ANTHROPIC_API_KEY"]),
            (403, "permission_error", "Not allowed here", {}, ["not allowed", "Not allowed here"]),
            (404, "not_found_error", "model: nope", {}, ["was not found", "retired", "claude-sonnet-5-5"]),
            (400, "invalid_request_error", "max_tokens: too large", {}, ["rejected the request", "max_tokens: too large"]),
            (429, "rate_limit_error", "slow down", {"retry-after": "17"}, ["rate limit", "17"]),
            (529, "overloaded_error", "busy", {}, ["overloaded", "529"]),
            (500, "api_error", "internal", {}, ["error 500", "internal"]),
            (402, "billing_error", "no credit", {}, ["credit balance"]),
            # What an account without credits actually got on 2026-11-17.
            (400, "invalid_request_error",
             "Your credit balance is too low to access the Anthropic API. "
             "Please go to Plans & Billing to upgrade or purchase credits.",
             {}, ["rejected the request", "credit balance is too low"]),
        )
        for status, kind, text, headers, expected in cases:
            with self.subTest(status=status):
                ok, shown = self.ask(FakeApi(failure(status, kind, text, headers)))

                self.assertFalse(ok)
                for fragment in expected:
                    self.assertIn(fragment, shown)

    def test_a_retired_model_names_itself(self):
        # The SDK warns about a model it knows to be retired before it sends;
        # that is not what is under test here, the 404 that follows is.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ok, shown = self.ask(FakeApi(failure(404, "not_found_error", "model: claude-sonnet-4-20250514")),
                                 ai={"model": "claude-sonnet-4-20250514"})

        self.assertFalse(ok)
        self.assertIn("was not found", shown)
        self.assertIn("claude-sonnet-4-20250514", shown)

    def test_what_the_api_said_is_shown_without_the_wrapper_around_it(self):
        _ok, shown = self.ask(FakeApi(failure(400, "invalid_request_error", "max_tokens: must be positive")))

        self.assertIn("max_tokens: must be positive", shown)
        self.assertNotIn("Error code:", shown)

    def test_the_request_id_is_shown_so_it_can_be_reported(self):
        _ok, shown = self.ask(FakeApi(failure(500, "api_error", "internal")))

        self.assertIn("req_TEST", shown)

    def test_markup_in_an_api_message_cannot_reach_the_chat(self):
        # The chat renders HTML, and the API's text is not ours.
        _ok, shown = self.ask(FakeApi(failure(400, "invalid_request_error", "<img src=x onerror=1>")))

        self.assertNotIn("<img", shown)
        self.assertIn("&lt;img", shown)

    def test_a_timeout_names_the_limit_and_the_setting(self):
        ok, shown = self.ask(FakeApi(httpx2.ReadTimeout("slow")), ai={"timeout_s": 90})

        self.assertFalse(ok)
        self.assertIn("90", shown)
        self.assertIn("timeout_s", shown)

    def test_a_missing_connection_says_so(self):
        ok, shown = self.ask(FakeApi(httpx2.ConnectError("down")))

        self.assertFalse(ok)
        self.assertIn("no connection", shown)

    def test_no_credentials_say_which_variable_to_set(self):
        # The SDK raises a bare TypeError for this.
        with tempfile.TemporaryDirectory() as home:
            client = AIClient(settings(mode="claude", anthropic={}))
            real = anthropic.Anthropic

            def build(**kwargs):
                return real(http_client=httpx2.Client(transport=httpx2.MockTransport(FakeApi(message()))), **kwargs)

            with mock.patch.object(anthropic, "Anthropic", side_effect=build), \
                    mock.patch.dict(os.environ, {"HOME": home}, clear=True):
                ok, shown = client.ask_claude("x")

        self.assertFalse(ok)
        self.assertIn("ANTHROPIC_API_KEY", shown)
        self.assertIn("export", shown)
        self.assertIn("setx", shown)

    def test_a_missing_sdk_package_is_a_message_and_not_a_crash(self):
        client = AIClient(settings(mode="claude", anthropic={}))

        with mock.patch.object(claude_client, "_anthropic", side_effect=ImportError("no module")):
            ok, shown = client.ask_claude("x")
            ok_check, shown_check = client.check_claude()

        for result in ((ok, shown), (ok_check, shown_check)):
            self.assertFalse(result[0])
            self.assertIn("pip install -r requirements.txt", result[1])


class ClaudeCheckTests(ClaudeCase):
    MODEL = {"id": "claude-sonnet-5-5", "type": "model", "display_name": "Claude Sonnet 5.5",
             "created_at": "2026-09-01T00:00:00Z", "max_input_tokens": 1000000, "max_tokens": 128000,
             "capabilities": {}}

    def test_a_working_key_and_model_are_confirmed_by_name(self):
        api = FakeApi(httpx2.Response(200, json=self.MODEL))

        ok, text = self.run_claude(api, lambda c: c.check_claude())[0]

        self.assertTrue(ok)
        self.assertIn("Claude Sonnet 5.5", text)
        # A green check with an empty account must not read as "all set".
        self.assertIn("does not look at the credit balance", text)
        self.assertEqual((api.requests[0].method, api.requests[0].url.path),
                         ("GET", "/v1/models/claude-sonnet-5-5"))

    def test_it_asks_about_the_model_it_is_given_not_the_saved_one(self):
        # The settings dialog tests what is typed in it, saved or not.
        api = FakeApi(httpx2.Response(200, json=dict(self.MODEL, id="claude-haiku-5-5")))

        self.run_claude(api, lambda c: c.check_claude("claude-haiku-5-5"))

        self.assertEqual(api.requests[0].url.path, "/v1/models/claude-haiku-5-5")

    def test_a_retired_model_is_found_out_without_running_anything(self):
        api = FakeApi(failure(404, "not_found_error", "model: claude-sonnet-4-20250514"))

        ok, text = self.run_claude(
            api, lambda c: c.check_claude("claude-sonnet-4-20250514"))[0]

        self.assertFalse(ok)
        self.assertIn("was not found", text)

    def test_the_check_is_quick_and_does_not_retry(self):
        _result, built = self.run_claude(
            FakeApi(httpx2.Response(200, json=self.MODEL)), lambda c: c.check_claude())

        self.assertEqual(built[0]["timeout"], 10.0)
        self.assertEqual(built[0]["max_retries"], 0)

    def test_a_wrong_key_is_reported_by_the_check(self):
        api = FakeApi(failure(401, "authentication_error", "invalid x-api-key"))

        ok, text = self.run_claude(api, lambda c: c.check_claude())[0]

        self.assertFalse(ok)
        self.assertIn("rejected", text)


class PromptTests(unittest.TestCase):
    def test_a_review_is_not_told_to_write_code_and_a_generation_is_not_told_to_review(self):
        client = AIClient(settings())

        self.assertEqual(client.review_prompt(), DEFAULT_REVIEW_PROMPT)
        self.assertEqual(client.generate_prompt(), DEFAULT_GENERATE_PROMPT)
        self.assertNotEqual(client.review_prompt(), client.generate_prompt())

    def test_the_review_prompt_comes_from_the_top_level_first(self):
        client = AIClient(settings(system_prompt="TOP", ollama={"system_prompt": "OLD"}))

        self.assertEqual(client.review_prompt(), "TOP")

    def test_the_review_prompt_still_comes_from_under_ollama_in_an_older_config(self):
        client = AIClient(settings(ollama={"system_prompt": "OLD"}))

        self.assertEqual(client.review_prompt(), "OLD")

    def test_a_generate_prompt_can_be_set(self):
        self.assertEqual(AIClient(settings(generate_prompt="G")).generate_prompt(), "G")

    def test_ask_hands_the_instructions_to_whichever_backend_is_chosen(self):
        for mode, backend in (("claude", "ask_claude"), ("ollama", "ask_ollama"),
                              ("openai", "ask_openai")):
            with self.subTest(mode=mode):
                client = AIClient(settings(mode=mode))
                with mock.patch.object(client, backend, return_value=(True, "x")) as called:
                    client.ask("prompt", "instructions")

                called.assert_called_once_with("prompt", "instructions")

    def test_anthropic_is_claude_too(self):
        # The template and AGENTS.md offered "anthropic"; it went to Ollama.
        client = AIClient(settings(mode="anthropic"))
        with mock.patch.object(client, "ask_claude", return_value=(True, "x")) as claude, \
                mock.patch.object(client, "ask_ollama") as ollama:
            client.ask("prompt", "instructions")

        claude.assert_called_once_with("prompt", "instructions")
        ollama.assert_not_called()

    def test_an_unknown_mode_is_said_not_guessed(self):
        client = AIClient(settings(mode="gpt"))
        with mock.patch.object(client, "ask_claude") as claude, \
                mock.patch.object(client, "ask_ollama") as ollama, \
                mock.patch.object(client, "ask_openai") as openai:
            ok, message = client.ask("prompt")

        self.assertFalse(ok)
        self.assertIn("ai.mode 'gpt' is not known", message)
        self.assertIn("openai", message)
        claude.assert_not_called()
        ollama.assert_not_called()
        openai.assert_not_called()

    def test_the_backend_a_mode_names(self):
        for ai, backend in (({}, "claude"), ({"mode": None}, "claude"),
                            ({"mode": "claude"}, "claude"), ({"mode": " Claude "}, "claude"),
                            ({"mode": "anthropic"}, "claude"), ({"mode": "ANTHROPIC"}, "claude"),
                            ({"mode": "ollama"}, "ollama"), ({"mode": "Ollama"}, "ollama"),
                            ({"mode": "openai"}, "openai"), ({"mode": " OpenAI"}, "openai"),
                            ({"mode": "gpt"}, "gpt")):
            with self.subTest(ai=ai):
                self.assertEqual(backend_of(ai), backend)
        self.assertEqual(backend_of(None), "claude")

    def test_the_shipped_template_agrees_with_the_built_in_defaults(self):
        with open(os.path.join(ROOT, "config.example.yaml"), encoding="utf-8") as handle:
            ai = yaml.safe_load(handle)["ai"]

        self.assertEqual(ai["system_prompt"].strip(), DEFAULT_REVIEW_PROMPT)
        self.assertEqual(ai["anthropic"]["model"], DEFAULT_CLAUDE_MODEL)
        self.assertEqual(ai["ollama"]["model"], DEFAULT_OLLAMA_MODEL)
        self.assertLessEqual(ai["anthropic"]["max_output_tokens"], MAX_OUTPUT_TOKENS_WITHOUT_STREAMING)
        self.assertIn(ai["anthropic"]["effort"], ("low", "medium", "high", "xhigh", "max"))
        self.assertEqual(ai["openai"]["base_url"], DEFAULT_OPENAI_BASE_URL)
        # No model is guessed for a server nobody knows yet.
        self.assertEqual(ai["openai"]["model"], "")

    def test_every_backend_has_a_section_in_the_template(self):
        with open(os.path.join(ROOT, "config.example.yaml"), encoding="utf-8") as handle:
            ai = yaml.safe_load(handle)["ai"]

        self.assertEqual(set(SECTION_OF), set(BACKENDS))
        for backend in BACKENDS:
            self.assertIn(SECTION_OF[backend], ai, backend)


def ollama_answer(content="Line 5: G21 missing", **message):
    response = mock.Mock(status_code=200, text="")
    response.json.return_value = {"message": dict({"role": "assistant", "content": content}, **message),
                                  "done": True}
    return response


def ollama_failure(status, text="boom"):
    response = mock.Mock(status_code=status, text=text)
    response.json.return_value = {"error": text}
    return response


class OllamaTests(unittest.TestCase):
    URL = "http://127.0.0.1:11434/api/chat"

    def ask(self, response, system=None, **ollama):
        client = AIClient(settings(mode="ollama", ollama=dict({"base_url": self.URL}, **ollama)))
        with mock.patch.object(claude_client.requests, "post", side_effect=(
                response if isinstance(response, Exception) else None) or [response]) as post:
            result = client.ask_ollama("Review this", system)
        return result, post

    def sent(self, post):
        return post.call_args.kwargs["json"]

    def test_the_default_is_the_chosen_model(self):
        _result, post = self.ask(ollama_answer())

        self.assertEqual(self.sent(post)["model"], "qwen3.8:27b")
        self.assertIs(self.sent(post)["stream"], False)

    def test_the_instructions_come_before_the_prompt(self):
        _result, post = self.ask(ollama_answer(), system="Be strict.")

        self.assertEqual(self.sent(post)["messages"], [
            {"role": "system", "content": "Be strict."},
            {"role": "user", "content": "Review this"},
        ])

    def test_without_instructions_there_is_no_system_message(self):
        _result, post = self.ask(ollama_answer(), system=None)

        self.assertEqual([m["role"] for m in self.sent(post)["messages"]], ["user"])

    def test_think_is_only_sent_when_the_config_says_so(self):
        # A model that does not think rejects the field.
        _result, post = self.ask(ollama_answer())
        self.assertNotIn("think", self.sent(post))

        _result, post = self.ask(ollama_answer(), think=False)
        self.assertIs(self.sent(post)["think"], False)    # False is a value, not "unset"

        _result, post = self.ask(ollama_answer(), think=True)
        self.assertIs(self.sent(post)["think"], True)

    def test_the_context_size_is_sent_when_configured(self):
        _result, post = self.ask(ollama_answer(), num_ctx=8192)
        self.assertEqual(self.sent(post)["options"], {"num_ctx": 8192})

        _result, post = self.ask(ollama_answer())
        self.assertNotIn("options", self.sent(post))

    def test_the_timeout_comes_from_the_config(self):
        _result, post = self.ask(ollama_answer(), timeout_s=45)

        self.assertEqual(post.call_args.kwargs["timeout"], 45.0)

    def test_the_answer_text_is_returned(self):
        result, _post = self.ask(ollama_answer("Line 5: G21 missing"))

        self.assertEqual(result, (True, "Line 5: G21 missing"))

    def test_a_missing_model_says_how_to_install_it(self):
        (ok, text), _post = self.ask(ollama_failure(404), model="qwen3.8:27b")

        self.assertFalse(ok)
        self.assertIn("ollama pull qwen3.8:27b", text)

    def test_a_rejected_think_field_says_to_remove_it(self):
        (ok, text), _post = self.ask(ollama_failure(400, "does not support thinking"), think=False)

        self.assertFalse(ok)
        self.assertIn("remove ai.ollama.think", text)

    def test_a_server_that_is_not_running_says_so(self):
        (ok, text), _post = self.ask(requests.exceptions.ConnectionError())

        self.assertFalse(ok)
        self.assertIn("Is Ollama running", text)
        self.assertIn("base_url", text)

    def test_a_timeout_names_the_limit_and_the_setting(self):
        (ok, text), _post = self.ask(requests.exceptions.Timeout(), timeout_s=45)

        self.assertFalse(ok)
        self.assertIn("45", text)
        self.assertIn("timeout_s", text)

    def test_an_answer_that_was_only_thinking_says_so(self):
        (ok, text), _post = self.ask(ollama_answer("", thinking="Hmm, let me see..."))

        self.assertFalse(ok)
        self.assertIn("only thought", text)

    def test_an_empty_answer_is_not_reported_as_success(self):
        (ok, _text), _post = self.ask(ollama_answer(""))

        self.assertFalse(ok)

    def test_an_answer_cut_off_by_the_context_says_so(self):
        response = ollama_answer("Line 5: G2")
        response.json.return_value["done_reason"] = "length"

        (ok, text), _post = self.ask(response)

        self.assertTrue(ok)
        self.assertIn("cut off", text)
        self.assertIn("num_ctx", text)

    def test_a_missing_address_is_a_config_error(self):
        client = AIClient(settings(mode="ollama", ollama={}))

        ok, text = client.ask_ollama("x")

        self.assertFalse(ok)
        self.assertIn("base_url missing", text)


class OllamaCheckTests(unittest.TestCase):
    def check(self, response, model=None, **ollama):
        client = AIClient(settings(ollama=dict({"base_url": "http://nas.local:11434/api/chat"}, **ollama)))
        effect = response if isinstance(response, Exception) else None
        with mock.patch.object(claude_client.requests, "get",
                               side_effect=effect, return_value=response) as get:
            return client.check_ollama(model), get

    @staticmethod
    def tags(*names):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"models": [{"name": n} for n in names]}
        response.raise_for_status.return_value = None
        return response

    def test_it_asks_the_tag_list_of_the_machine_in_the_address(self):
        _result, get = self.check(self.tags("qwen3.8:27b"))

        self.assertEqual(get.call_args.args[0], "http://nas.local:11434/api/tags")

    def test_an_installed_model_is_confirmed(self):
        (ok, text), _get = self.check(self.tags("qwen3.8:27b"), model="qwen3.8:27b")

        self.assertTrue(ok)
        self.assertIn("installed", text)

    def test_a_model_without_a_tag_matches_latest(self):
        (ok, _text), _get = self.check(self.tags("granite4:latest"), model="granite4")

        self.assertTrue(ok)

    def test_a_model_that_is_not_installed_says_what_to_run_and_what_is(self):
        (ok, text), _get = self.check(self.tags("llama3.2:3b"), model="qwen3.8:27b")

        self.assertFalse(ok)
        self.assertIn("ollama pull qwen3.8:27b", text)
        self.assertIn("llama3.2:3b", text)

    def test_a_machine_that_does_not_answer_is_named(self):
        (ok, text), _get = self.check(requests.exceptions.ConnectionError())

        self.assertFalse(ok)
        self.assertIn("nas.local:11434", text)


# ---- OpenAI-compatible --------------------------------------------------------------

def http(status, body=None, text=None, headers=None):
    """A real requests.Response, so headers are read as requests reads them."""
    response = requests.models.Response()
    response.status_code = status
    response._content = (json.dumps(body) if body is not None else (text or "")).encode("utf-8")
    response.encoding = "utf-8"
    response.headers.update(headers or {})
    return response


def completion(content="Line 5: G21 missing", finish="stop", **message):
    return http(200, {"id": "chatcmpl-1", "object": "chat.completion", "model": "m",
                      "choices": [{"index": 0, "finish_reason": finish,
                                   "message": dict({"role": "assistant", "content": content},
                                                   **message)}]})


def openai_failure(status, text="boom", code=None, kind="invalid_request_error", headers=None):
    return http(status, {"error": {"message": text, "type": kind, "param": None, "code": code}},
                headers=headers)


class OpenAICase(unittest.TestCase):
    URL = "http://localhost:1234/v1"

    def ask(self, response, system=None, env=None, **openai):
        section = dict({"base_url": self.URL, "model": "qwen/qwen3-4b"}, **openai)
        client = AIClient(settings(mode="openai", openai=section))
        effect = response if isinstance(response, Exception) else [response]
        environment = {"OPENAI_API_KEY": ""}
        environment.update(env or {})
        with mock.patch.object(claude_client.requests, "post", side_effect=effect) as post, \
                mock.patch.dict(os.environ, environment):
            result = client.ask_openai("Review this", system)
        return result, post

    @staticmethod
    def sent(post):
        return post.call_args.kwargs["json"]


class OpenAIRequestTests(OpenAICase):
    def test_it_posts_a_chat_completion_to_the_api_root(self):
        _result, post = self.ask(completion())

        self.assertEqual(post.call_args.args[0], "http://localhost:1234/v1/chat/completions")
        self.assertEqual(self.sent(post)["model"], "qwen/qwen3-4b")
        self.assertIs(self.sent(post)["stream"], False)

    def test_the_instructions_come_before_the_prompt(self):
        _result, post = self.ask(completion(), system="Be strict.")

        self.assertEqual(self.sent(post)["messages"], [
            {"role": "system", "content": "Be strict."},
            {"role": "user", "content": "Review this"},
        ])

    def test_without_instructions_there_is_no_system_message(self):
        _result, post = self.ask(completion())

        self.assertEqual([m["role"] for m in self.sent(post)["messages"]], ["user"])

    def test_a_whole_endpoint_is_cut_back_to_the_root(self):
        for given in ("http://localhost:1234/v1/chat/completions", "http://localhost:1234/v1/",
                      " http://localhost:1234/v1/models "):
            with self.subTest(given=given):
                _result, post = self.ask(completion(), base_url=given)

                self.assertEqual(post.call_args.args[0], "http://localhost:1234/v1/chat/completions")

    def test_without_an_address_it_is_openai(self):
        client = AIClient(settings(mode="openai", openai={"model": "some-model"}))
        with mock.patch.object(claude_client.requests, "post", return_value=completion()) as post, \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            client.ask_openai("x")

        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/chat/completions")

    def test_the_key_is_sent_from_the_configured_variable(self):
        _result, post = self.ask(completion(), api_key_env="LMS_KEY", env={"LMS_KEY": " sk-lms "})

        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer sk-lms"})

    def test_without_a_key_no_authorization_is_sent(self):
        # A local server needs none, and an empty "Bearer " can be refused.
        _result, post = self.ask(completion())

        self.assertEqual(post.call_args.kwargs["headers"], {})

    def test_the_optional_fields_are_only_sent_when_configured(self):
        _result, post = self.ask(completion())
        for field in ("max_completion_tokens", "max_tokens", "reasoning_effort"):
            self.assertNotIn(field, self.sent(post))

        _result, post = self.ask(completion(), max_output_tokens=4000, reasoning_effort="low")
        self.assertEqual(self.sent(post)["max_completion_tokens"], 4000)
        self.assertEqual(self.sent(post)["reasoning_effort"], "low")
        # The old name is refused by OpenAI's reasoning models.
        self.assertNotIn("max_tokens", self.sent(post))

    def test_no_sampling_parameters_are_sent(self):
        _result, post = self.ask(completion())

        self.assertEqual(set(self.sent(post)), {"model", "messages", "stream"})

    def test_the_timeout_comes_from_the_config(self):
        _result, post = self.ask(completion(), timeout_s=45)

        self.assertEqual(post.call_args.kwargs["timeout"], 45.0)

    def test_without_a_model_nothing_is_sent_and_it_says_where_to_set_one(self):
        (ok, text), post = self.ask(completion(), model="  ")

        self.assertFalse(ok)
        self.assertIn("ai.openai.model", text)
        self.assertIn("Test connection", text)
        post.assert_not_called()

    def test_a_number_that_is_not_one_is_a_config_error(self):
        (ok, text), post = self.ask(completion(), max_output_tokens="lots")

        self.assertFalse(ok)
        self.assertIn("Config error", text)
        post.assert_not_called()


class OpenAIAnswerTests(OpenAICase):
    def test_the_answer_text_is_returned(self):
        result, _post = self.ask(completion("Line 5: G21 missing"))

        self.assertEqual(result, (True, "Line 5: G21 missing"))

    def test_the_thinking_beside_the_answer_is_not_shown(self):
        result, _post = self.ask(completion("Line 5", reasoning_content="Let me think..."))

        self.assertEqual(result, (True, "Line 5"))

    def test_text_in_parts_is_joined(self):
        result, _post = self.ask(completion([{"type": "text", "text": "Line 5: "},
                                             {"type": "text", "text": "G21 missing"}]))

        self.assertEqual(result, (True, "Line 5: G21 missing"))

    def test_an_answer_cut_off_at_the_limit_says_so(self):
        (ok, text), _post = self.ask(completion("Line 5: G2", finish="length"))

        self.assertTrue(ok)
        self.assertTrue(text.startswith("Line 5: G2"))
        self.assertIn("cut off", text)
        self.assertIn("ai.openai.max_output_tokens", text)

    def test_a_limit_used_up_by_reasoning_says_so(self):
        (ok, text), _post = self.ask(completion(None, finish="length", reasoning="Hmm..."))

        self.assertFalse(ok)
        self.assertIn("still reasoning", text)
        self.assertIn("max_output_tokens", text)

    def test_an_answer_that_was_only_reasoning_says_so(self):
        (ok, text), _post = self.ask(completion("", reasoning_content="Hmm, let me see..."))

        self.assertFalse(ok)
        self.assertIn("only reasoned", text)

    def test_a_refusal_is_reported_with_what_the_model_said(self):
        (ok, text), _post = self.ask(completion(None, refusal="I can't help with <that>."))

        self.assertFalse(ok)
        self.assertIn("declined", text)
        self.assertIn("&lt;that&gt;", text)

    def test_a_content_filter_is_reported(self):
        (ok, text), _post = self.ask(completion("", finish="content_filter"))

        self.assertFalse(ok)
        self.assertIn("content filter", text)

    def test_an_empty_answer_is_not_reported_as_success(self):
        (ok, text), _post = self.ask(completion(""))

        self.assertFalse(ok)
        self.assertIn("no text", text)

    def test_an_answer_without_choices_is_not_taken_for_one(self):
        (ok, text), _post = self.ask(http(200, {"object": "list", "data": []}))

        self.assertFalse(ok)
        self.assertIn("no choices", text)

    def test_a_page_that_is_not_json_points_at_the_address(self):
        (ok, text), _post = self.ask(http(200, text="<html>LM Studio</html>"))

        self.assertFalse(ok)
        self.assertIn("/v1", text)


class OpenAIErrorTests(OpenAICase):
    def test_a_missing_key_says_which_variable_to_set(self):
        (ok, text), _post = self.ask(openai_failure(401, "Missing bearer authentication"),
                                     api_key_env="OPENROUTER_API_KEY")

        self.assertFalse(ok)
        self.assertIn("OPENROUTER_API_KEY is not set", text)

    def test_a_wrong_key_says_it_was_rejected(self):
        (ok, text), _post = self.ask(openai_failure(401, "Incorrect API key provided"),
                                     env={"OPENAI_API_KEY": "sk-wrong"})

        self.assertFalse(ok)
        self.assertIn("rejected", text)
        self.assertIn("OPENAI_API_KEY", text)

    def test_an_unknown_model_names_itself(self):
        (ok, text), _post = self.ask(openai_failure(
            404, "The model `gpt-9` does not exist or you do not have access to it.",
            code="model_not_found"), model="gpt-9")

        self.assertFalse(ok)
        self.assertIn("model 'gpt-9' was not found", text)
        self.assertIn("Test connection", text)

    def test_a_server_message_without_a_full_stop_does_not_run_into_the_next_sentence(self):
        (_ok, text), _post = self.ask(openai_failure(404, "model nope not found"), model="nope")

        self.assertIn("model nope not found. Test connection", text)

    def test_a_wrong_address_says_what_the_root_looks_like(self):
        (ok, text), _post = self.ask(http(404, text="File Not Found"), base_url="http://localhost:8080")

        self.assertFalse(ok)
        self.assertIn("nothing found", text)
        self.assertIn("/v1", text)

    def test_an_account_without_credit_is_told_so(self):
        (ok, text), _post = self.ask(openai_failure(
            429, "You exceeded your current quota, please check your plan and billing details.",
            code="insufficient_quota", kind="insufficient_quota"))

        self.assertFalse(ok)
        self.assertIn("no quota or credit", text)
        self.assertIn("billing", text)

    def test_a_rate_limit_says_how_long_to_wait(self):
        (ok, text), _post = self.ask(openai_failure(429, "Rate limit reached", code="rate_limit_exceeded",
                                                    headers={"Retry-After": "20"}))

        self.assertFalse(ok)
        self.assertIn("rate limit", text)
        self.assertIn("20 s", text)

    def test_a_rejected_optional_field_says_to_remove_it(self):
        (ok, text), _post = self.ask(openai_failure(400, "Unrecognized request argument: reasoning_effort"),
                                     reasoning_effort="high")

        self.assertFalse(ok)
        self.assertIn("Unrecognized request argument", text)
        self.assertIn("ai.openai.reasoning_effort", text)

    def test_a_bad_request_without_optional_fields_gives_no_such_hint(self):
        (ok, text), _post = self.ask(openai_failure(400, "messages is required"))

        self.assertFalse(ok)
        self.assertNotIn("remove it", text)

    def test_a_server_error_shows_its_status_and_what_it_said(self):
        (ok, text), _post = self.ask(openai_failure(503, "Model is loading", kind="server_error"))

        self.assertFalse(ok)
        self.assertIn("503", text)
        self.assertIn("Model is loading", text)

    def test_the_messages_of_other_servers_are_read_too(self):
        for response, said in ((http(500, {"error": "model crashed"}), "model crashed"),
                               (http(422, {"detail": "field required"}), "field required"),
                               (http(502, text="Bad Gateway"), "Bad Gateway")):
            with self.subTest(said=said):
                (ok, text), _post = self.ask(response)

                self.assertFalse(ok)
                self.assertIn(said, text)

    def test_the_request_id_is_shown_so_it_can_be_reported(self):
        (_ok, text), _post = self.ask(openai_failure(500, "boom", headers={"X-Request-Id": "req_abc"}))

        self.assertIn("req_abc", text)

    def test_markup_in_a_server_message_cannot_reach_the_chat(self):
        (_ok, text), _post = self.ask(openai_failure(400, "<b>bad</b>"))

        self.assertNotIn("<b>", text)
        self.assertIn("&lt;b&gt;", text)

    def test_a_server_that_is_not_running_is_named(self):
        (ok, text), _post = self.ask(requests.exceptions.ConnectionError())

        self.assertFalse(ok)
        self.assertIn("localhost:1234", text)
        self.assertIn("ai.openai.base_url", text)

    def test_a_timeout_names_the_limit_and_the_setting(self):
        (ok, text), _post = self.ask(requests.exceptions.Timeout(), timeout_s=45)

        self.assertFalse(ok)
        self.assertIn("45", text)
        self.assertIn("ai.openai.timeout_s", text)


class OpenAICheckTests(unittest.TestCase):
    def check(self, response, model=None, base_url=None, env=None, **openai):
        client = AIClient(settings(openai=dict({"base_url": "http://nas.local:1234/v1",
                                                 "model": "saved-model"}, **openai)))
        effect = response if isinstance(response, Exception) else None
        environment = {"OPENAI_API_KEY": ""}
        environment.update(env or {})
        with mock.patch.object(claude_client.requests, "get",
                               side_effect=effect, return_value=response) as get, \
                mock.patch.dict(os.environ, environment):
            return client.check_openai(model, base_url), get

    @staticmethod
    def models(*ids):
        return http(200, {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})

    def test_it_asks_the_model_list_of_the_server_with_the_key(self):
        _result, get = self.check(self.models("saved-model"), env={"OPENAI_API_KEY": "sk-x"})

        self.assertEqual(get.call_args.args[0], "http://nas.local:1234/v1/models")
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer sk-x"})

    def test_it_checks_what_is_typed_not_what_is_saved(self):
        (ok, _text), get = self.check(self.models("typed-model"), model="typed-model",
                                      base_url="http://other:8080/v1/")

        self.assertTrue(ok)
        self.assertEqual(get.call_args.args[0], "http://other:8080/v1/models")

    def test_an_offered_model_is_confirmed_without_claiming_more(self):
        (ok, text), _get = self.check(self.models("a", "saved-model"))

        self.assertTrue(ok)
        self.assertIn("offers model 'saved-model'", text)
        self.assertIn("quota", text)

    def test_a_model_that_is_not_offered_says_what_is(self):
        (ok, text), _get = self.check(self.models("llama-3.2-3b", "qwen3-4b"), model="gpt-9")

        self.assertFalse(ok)
        self.assertIn("does not offer model 'gpt-9'", text)
        self.assertIn("llama-3.2-3b, qwen3-4b", text)

    def test_without_a_model_it_lists_what_there_is(self):
        (ok, text), _get = self.check(self.models("qwen3-4b"), model="")

        self.assertFalse(ok)
        self.assertIn("no model is entered", text)
        self.assertIn("qwen3-4b", text)

    def test_a_long_list_is_cut_short_and_counted(self):
        (_ok, text), _get = self.check(self.models(*(f"m{n}" for n in range(300))), model="x")

        self.assertIn("m19", text)
        self.assertNotIn("m20,", text)
        self.assertIn("300 in all", text)

    def test_a_wrong_address_says_what_the_root_looks_like(self):
        (ok, text), _get = self.check(http(404, text="Not Found"), base_url="http://nas.local:1234")

        self.assertFalse(ok)
        self.assertIn("/v1", text)

    def test_a_key_the_server_wants_is_asked_for(self):
        (ok, text), _get = self.check(openai_failure(401, "Missing bearer authentication"))

        self.assertFalse(ok)
        self.assertIn("OPENAI_API_KEY is not set", text)

    def test_a_server_that_does_not_answer_is_named(self):
        (ok, text), _get = self.check(requests.exceptions.ConnectionError())

        self.assertFalse(ok)
        self.assertIn("nas.local:1234", text)

    def test_an_answer_that_is_not_a_model_list_is_not_taken_for_one(self):
        (ok, text), _get = self.check(http(200, text="<html>router login</html>"))

        self.assertFalse(ok)
        self.assertIn("not with a model list", text)


if __name__ == "__main__":
    unittest.main()
