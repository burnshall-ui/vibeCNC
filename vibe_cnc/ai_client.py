# ai_client.py — the AI backends: Claude through the Anthropic SDK, Ollama
# over HTTP, and any server that speaks the OpenAI Chat Completions API.
#
# Every chat_*, ask_* and check_* returns (ok, text). When ok is False the
# text says what to do about it, in words, rather than what the library threw:
# it is shown to the operator in the chat.
#
# A conversation is a list of {"role": "user" | "assistant", "content": str},
# the operator's turns and the model's answers in order, ending on a user turn.
# The instructions are not part of it; each backend puts them where it wants.
import html
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import requests

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
DEFAULT_OLLAMA_MODEL = "qwen3.8:27b"
# OpenAI's own API. LM Studio, llama.cpp, vLLM, OpenRouter and Ollama's /v1
# speak the same protocol at their own address. There is no default model:
# what a server offers depends on the server, and a guessed name that has
# since been retired fails on the first request, as the old Claude default did.
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# What ai.mode may name, and the config section each one reads.
BACKENDS = ("claude", "ollama", "openai")
SECTION_OF = {"claude": "anthropic", "ollama": "ollama", "openai": "openai"}

# Thinking tokens count against max_tokens, so the old 800 left nothing for the
# answer on a model that thinks by default.
DEFAULT_MAX_OUTPUT_TOKENS = 16000
# A request that is not streamed may not ask for more than the SDK allows for a
# single wait (about 21 300 tokens); above it the SDK refuses with a ValueError.
MAX_OUTPUT_TOKENS_WITHOUT_STREAMING = 21000

DEFAULT_REVIEW_PROMPT = (
    "You are a strict CNC reviewer. Follow the policies strictly.\n"
    "Answer with: 1) a list of rule violations (line, rule, short fix),\n"
    "2) optionally a unified diff. No cosmetic changes."
)

# Generating is not reviewing. The review prompt used to be sent with every
# request to Ollama, so asking for G-code answered with a list of violations.
DEFAULT_GENERATE_PROMPT = (
    "You write Fanuc-style CNC lathe G-code for a FANUC 0i-TF control: metric, "
    "diameter programming on X, G18 plane. Give the code and a short explanation "
    "in the format the user asks for. Never invent tool numbers, work offsets or "
    "stock sizes the user did not give; say what you assumed. A person reads "
    "every line before it reaches a machine."
)


def backend_of(ai: Optional[dict]) -> str:
    """The backend `ai.mode` names: one of BACKENDS, or the unknown word itself.

    Every place that picks a backend asks this, so they cannot disagree. They
    did: the main window sent anything but "claude" to Ollama, the settings
    dialog took a missing mode for "ollama" and the client for "claude".
    "anthropic" means Claude. config.example.yaml and AGENTS.md offered it,
    and a config that followed them was sent to Ollama without a word.
    """
    mode = str((ai or {}).get("mode") or "claude").strip().lower()
    return "claude" if mode == "anthropic" else mode


def _anthropic():
    """The SDK, imported when first needed.

    A missing package then costs the Claude mode, with a message saying so,
    rather than the whole application at start.
    """
    import anthropic
    return anthropic


def _sdk_base_url(value) -> Optional[str]:
    """The SDK adds /v1/messages itself; older configs held the whole endpoint."""
    url = (value or "").strip().rstrip("/")
    for suffix in ("/v1/messages", "/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url or None


def _api_detail(error) -> str:
    """What the API said, without the "Error code: 404 - {...}" wrapper around it."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
    return getattr(error, "message", "") or str(error)


def _openai_root(value) -> str:
    """The API root the paths are added to, as a rule ending in /v1.

    A whole endpoint, as someone copying it from a server's page may enter,
    is cut back to the root.
    """
    url = str(value or "").strip().rstrip("/") or DEFAULT_OPENAI_BASE_URL
    for suffix in ("/chat/completions", "/models"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url


def _openai_error_body(body) -> Tuple[str, str]:
    """(message, code) out of an error body, whatever shape the server uses.

    OpenAI sends {"error": {"message", "type", "code"}} with a word for the
    code, OpenRouter the same with the status as a number; other servers send
    a bare string under "error", FastAPI's "detail", or a bare "message".
    """
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or ""), str(error.get("code") or error.get("type") or "")
        if isinstance(error, str):
            return error, ""
        for key in ("detail", "message"):
            if body.get(key):
                return str(body[key]), ""
    return str(body)[:300], ""


def _openai_error(response) -> Tuple[str, str]:
    """(message, code) out of an error response; plain text if it is not JSON."""
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:300], ""
    return _openai_error_body(body)


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

# Said without repeating what was entered: when it is not a name it is
# usually the key itself, and the chat is no place for it.
NOT_A_VARIABLE_NAME = (
    "❌ ai.openai.api_key_env must be the NAME of an environment variable, such as "
    "OPENROUTER_API_KEY, not the key itself. Put the key into that variable (macOS/Linux: "
    "export OPENROUTER_API_KEY=... in your shell profile; Windows: setx OPENROUTER_API_KEY \"...\") "
    "and enter only the name in Settings → AI.")


def is_variable_name(name: str) -> bool:
    return bool(_ENV_NAME.match(name or ""))


Message = Dict[str, str]


def user(content: str) -> Message:
    return {"role": "user", "content": content}


def assistant(content: str) -> Message:
    return {"role": "assistant", "content": content}


def turns(messages: Sequence[Message]) -> List[Message]:
    """A copy of the conversation, checked; a ValueError if it is malformed.

    A malformed conversation is a mistake in the code that built it, not in
    the config, so it is raised rather than shown to the operator.

    Turns alternate and the first and the last are the user's. Claude would
    merge two user turns in a row, but the chat templates of many local models
    (served by llama.cpp, LM Studio or Ollama) refuse roles that do not
    alternate, and the last turn is the question to be answered.
    """
    copied = []
    for index, message in enumerate(messages):
        role = message.get("role") if isinstance(message, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if role not in ("user", "assistant") or not isinstance(content, str):
            raise ValueError(f"turn {index}: needs role 'user' or 'assistant' and text content, "
                             f"got {message!r}")
        copied.append({"role": role, "content": content})
    if not copied:
        raise ValueError("a conversation needs at least one user turn")
    if copied[0]["role"] != "user" or copied[-1]["role"] != "user":
        raise ValueError("a conversation starts and ends on a user turn")
    for index in range(1, len(copied)):
        if copied[index]["role"] == copied[index - 1]["role"]:
            raise ValueError(f"turns {index - 1} and {index} are both {copied[index]['role']!r}; "
                             "user and assistant alternate")
    return copied


def _with_system(messages: List[Message], system: Optional[str]) -> List[Message]:
    """Ollama and Chat Completions take the instructions as the first message."""
    return ([{"role": "system", "content": system}] if system else []) + messages


class AIClient:
    def __init__(self, settings):
        self.cfg = settings.data

    # ---- configuration -------------------------------------------------------

    def _ai(self) -> dict:
        return self.cfg.get("ai") or {}

    def _section(self, name: str) -> dict:
        return self._ai().get(name) or {}

    def review_prompt(self) -> str:
        """The instructions for a review. `ai.system_prompt`, else the one that
        used to live under `ai.ollama`, else the built-in default."""
        return (self._ai().get("system_prompt")
                or self._section("ollama").get("system_prompt")
                or DEFAULT_REVIEW_PROMPT).strip()

    def generate_prompt(self) -> str:
        return (self._ai().get("generate_prompt") or DEFAULT_GENERATE_PROMPT).strip()

    # ---- dispatch --------------------------------------------------------------

    def chat(self, messages: Sequence[Message], system: Optional[str] = None) -> Tuple[bool, str]:
        """Continues a conversation with the backend ai.mode names: the answer
        to its last turn. An unknown mode is said, not guessed."""
        backend = backend_of(self._ai())
        if backend == "claude":
            return self.chat_claude(messages, system)
        if backend == "ollama":
            return self.chat_ollama(messages, system)
        if backend == "openai":
            return self.chat_openai(messages, system)
        turns(messages)     # a malformed conversation is a mistake whatever the mode
        return (False, f"❌ config.yaml: ai.mode '{html.escape(backend)}' is not known. "
                       "Use claude, ollama or openai (Settings → AI).")

    def ask(self, prompt: str, system: Optional[str] = None) -> Tuple[bool, str]:
        """One question, no history."""
        return self.chat([user(prompt)], system)

    # ---- Claude (Anthropic SDK) --------------------------------------------------

    def _claude_client(self, sdk, section: dict, timeout: Optional[float] = None,
                       max_retries: Optional[int] = None):
        kwargs = {}
        key = os.environ.get(section.get("api_key_env") or "ANTHROPIC_API_KEY", "").strip()
        if key:
            # Without one the SDK still looks for a token or a login profile.
            kwargs["api_key"] = key
        base_url = _sdk_base_url(section.get("base_url"))
        if base_url:
            kwargs["base_url"] = base_url
        kwargs["timeout"] = timeout if timeout is not None else float(section.get("timeout_s") or 180)
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        return sdk.Anthropic(**kwargs)

    def ask_claude(self, prompt: str, system: Optional[str] = None) -> Tuple[bool, str]:
        return self.chat_claude([user(prompt)], system)

    def chat_claude(self, messages: Sequence[Message],
                    system: Optional[str] = None) -> Tuple[bool, str]:
        """Ask Claude. The SDK retries rate limits, overload and 5xx by itself."""
        conversation = turns(messages)
        try:
            sdk = _anthropic()
        except ImportError:
            return (False, "❌ The 'anthropic' package is not installed. "
                           "Run: pip install -r requirements.txt")

        section = self._section("anthropic")
        model = (section.get("model") or DEFAULT_CLAUDE_MODEL).strip()
        key_env = section.get("api_key_env") or "ANTHROPIC_API_KEY"
        timeout = float(section.get("timeout_s") or 180)
        try:
            max_tokens = min(int(section.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS),
                             MAX_OUTPUT_TOKENS_WITHOUT_STREAMING)
            request = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": conversation,
            }
            if system:
                request["system"] = system
            effort = (section.get("effort") or "").strip()
            if effort:
                request["output_config"] = {"effort": effort}

            message = self._claude_client(sdk, section).messages.create(**request)
        except Exception as e:
            return (False, self._claude_error(sdk, e, model, key_env, timeout))
        return self._claude_answer(message, max_tokens)

    @staticmethod
    def _claude_answer(message, max_tokens: int) -> Tuple[bool, str]:
        """Text out of a response, and an honest word when there is none."""
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        stop = message.stop_reason

        if stop == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            explanation = getattr(details, "explanation", None)
            said = f" ({category})" if category else ""
            said += f": {html.escape(str(explanation))}" if explanation else "."
            return (False, f"❌ Claude declined this request{said}")
        if not text:
            if stop == "max_tokens":
                return (False, f"❌ Claude used all {max_tokens} output tokens before it wrote an "
                               "answer -- thinking counts too. Raise ai.anthropic.max_output_tokens.")
            return (False, f"❌ Claude returned no text (stop reason: {stop}).")
        if stop == "max_tokens":
            text += (f"<br><br>⚠️ The answer was cut off at {max_tokens} tokens. "
                     "Raise ai.anthropic.max_output_tokens.")
        return (True, text)

    @staticmethod
    def _claude_error(sdk, error, model: str, key_env: str, timeout: float) -> str:
        """One message for each way the call can fail, most specific first.

        The SDK has a class for every status code, so what is told to the
        operator comes from the kind of failure, not from matching its text.
        """
        if isinstance(error, TypeError) and "authentication method" in str(error):
            return (f"❌ No Claude credentials found. Set the {key_env} environment variable "
                    f"(macOS/Linux: export {key_env}=sk-ant-... in your shell profile; "
                    f"Windows: setx {key_env} \"sk-ant-...\") and restart VibeCNC.")

        detail = html.escape(_api_detail(error))
        request_id = getattr(error, "request_id", None)
        where = f" (request {request_id})" if request_id else ""

        if isinstance(error, sdk.AuthenticationError):
            return (f"❌ Claude API: the API key was rejected (401). Check {key_env}: it may be "
                    f"wrong, revoked or from another account.{where}")
        if isinstance(error, sdk.PermissionDeniedError):
            return f"❌ Claude API: not allowed (403): {detail}{where}"
        if isinstance(error, sdk.NotFoundError):
            return (f"❌ Claude API: model '{html.escape(model)}' was not found (404). It may have "
                    "been retired, or this account cannot use it. Choose a current model in "
                    f"Settings → AI, for example {DEFAULT_CLAUDE_MODEL}.{where}")
        if isinstance(error, sdk.BadRequestError):
            return f"❌ Claude API rejected the request (400): {detail}{where}"
        if isinstance(error, sdk.RateLimitError):
            wait = ""
            response = getattr(error, "response", None)
            if response is not None and response.headers.get("retry-after"):
                wait = f" Try again in {html.escape(response.headers['retry-after'])} s."
            return f"❌ Claude API: rate limit reached.{wait}{where}"
        if isinstance(error, sdk.OverloadedError):
            return ("❌ Claude API is overloaded (529). It was retried automatically; "
                    f"try again in a minute.{where}")
        if isinstance(error, sdk.APITimeoutError):
            return (f"❌ Claude API: no answer within {timeout:g} s. Try again, or raise "
                    "ai.anthropic.timeout_s.")
        if isinstance(error, sdk.APIConnectionError):
            return ("❌ Claude API: no connection. Check your internet connection "
                    "(and ai.anthropic.base_url, if you set one).")
        if isinstance(error, sdk.APIStatusError):
            hint = ""
            if getattr(error, "type", "") == "billing_error":
                hint = " Check the credit balance of your Anthropic account."
            return (f"❌ Claude API error {error.status_code} "
                    f"({getattr(error, 'type', 'error')}): {detail}.{hint}{where}")
        if isinstance(error, ValueError):
            return f"❌ Config error: {html.escape(str(error))}"
        return f"❌ Unexpected error: {type(error).__name__}: {html.escape(str(error))}"

    def check_claude(self, model: Optional[str] = None) -> Tuple[bool, str]:
        """Is there a working key, and is this model one it can use? Costs nothing:
        it asks the Models API about the model, it does not run it.

        It cannot see the credit balance: an account with no credits passes
        this check and is turned away (400) on the first real request. Seen
        on 2026-11-17, so the message says so instead of implying more."""
        try:
            sdk = _anthropic()
        except ImportError:
            return (False, "❌ The 'anthropic' package is not installed. "
                           "Run: pip install -r requirements.txt")
        section = self._section("anthropic")
        model = (model or section.get("model") or DEFAULT_CLAUDE_MODEL).strip()
        key_env = section.get("api_key_env") or "ANTHROPIC_API_KEY"
        try:
            info = self._claude_client(sdk, section, timeout=10.0, max_retries=0).models.retrieve(model)
        except Exception as e:
            return (False, self._claude_error(sdk, e, model, key_env, 10.0))
        name = getattr(info, "display_name", "") or model
        return (True, f"✅ Claude API reachable. Model '{model}' ({name}) is available to this key. "
                      "This check does not look at the credit balance; the first analysis does.")

    # ---- Ollama (local) ------------------------------------------------------------

    def ask_ollama(self, prompt: str, system: Optional[str] = None) -> Tuple[bool, str]:
        return self.chat_ollama([user(prompt)], system)

    def chat_ollama(self, messages: Sequence[Message],
                    system: Optional[str] = None) -> Tuple[bool, str]:
        """Ask Ollama over /api/chat."""
        conversation = turns(messages)
        try:
            o = self._section("ollama")
            base_url = o.get("base_url", "")
            if not base_url:
                return (False, "❌ config.yaml invalid: ai.ollama.base_url missing")

            model = (o.get("model") or DEFAULT_OLLAMA_MODEL).strip()
            timeout = float(o.get("timeout_s") or 300)
            data = {"model": model, "messages": _with_system(conversation, system), "stream": False}
            # A model that thinks first answers slowly on a small machine, and
            # one that does not think rejects the field -- so it is only sent
            # when the config says so.
            if o.get("think") is not None:
                data["think"] = bool(o["think"])
            # Ollama's default context is 4096 tokens below 24 GiB of VRAM.
            if o.get("num_ctx"):
                data["options"] = {"num_ctx": int(o["num_ctx"])}

            try:
                r = requests.post(base_url, json=data, timeout=timeout)
            except requests.exceptions.ConnectionError:
                return (False, "❌ Ollama: Connection failed. Is Ollama running? (e.g. 'ollama serve'), "
                               "and is ai.ollama.base_url the right machine?")
            except requests.exceptions.Timeout:
                return (False, f"❌ Ollama: no answer within {timeout:g} s. The model may be too big "
                               "for this machine; raise ai.ollama.timeout_s or use a smaller model.")
            except requests.exceptions.RequestException as e:
                return (False, f"❌ Ollama: Network error - {html.escape(str(e))}")

            if r.status_code == 404:
                return (False, f"❌ Ollama: Model '{html.escape(model)}' not found. "
                               f"Please install it with: 'ollama pull {html.escape(model)}'")
            if r.status_code != 200:
                error_msg = html.escape(r.text[:300]) if r.text else "Unknown error"
                hint = ""
                if r.status_code == 400 and "think" in data:
                    hint = " (If the model does not think, remove ai.ollama.think from the config.)"
                return (False, f"❌ Ollama HTTP {r.status_code}: {error_msg}{hint}")

            try:
                j = r.json()
            except ValueError:
                return (False, "❌ Ollama: Invalid JSON response")

            message = j.get("message") or {}
            text = (message.get("content") or "").strip()
            if not text:
                if message.get("thinking"):
                    return (False, "❌ Ollama: the model only thought and wrote no answer. Set "
                                   "ai.ollama.think to false, or raise ai.ollama.num_ctx.")
                return (False, "❌ Ollama returned an empty answer.")
            if j.get("done_reason") == "length":
                text += ("<br><br>⚠️ The answer was cut off. "
                         "Raise ai.ollama.num_ctx, or shorten the program.")
            return (True, text)

        except (KeyError, TypeError, ValueError) as e:
            return (False, f"❌ Config error: {html.escape(str(e))}")
        except Exception as e:
            return (False, f"❌ Unexpected error: {type(e).__name__}: {html.escape(str(e))}")

    def check_ollama(self, model: Optional[str] = None) -> Tuple[bool, str]:
        """Is Ollama there, and is the model installed? Nothing is run."""
        o = self._section("ollama")
        base_url = o.get("base_url", "")
        if not base_url:
            return (False, "❌ config.yaml invalid: ai.ollama.base_url missing")
        model = (model or o.get("model") or DEFAULT_OLLAMA_MODEL).strip()
        parts = urlsplit(base_url)
        tags_url = f"{parts.scheme}://{parts.netloc}/api/tags"
        try:
            r = requests.get(tags_url, timeout=5)
            r.raise_for_status()
            installed = [m.get("name") or m.get("model") for m in (r.json().get("models") or [])]
        except requests.exceptions.ConnectionError:
            return (False, f"❌ Ollama: nothing answers at {parts.netloc}. Is Ollama running "
                           "('ollama serve'), and is ai.ollama.base_url the right machine?")
        except requests.exceptions.Timeout:
            return (False, f"❌ Ollama: {parts.netloc} did not answer within 5 s.")
        except (requests.exceptions.RequestException, ValueError) as e:
            return (False, f"❌ Ollama: {html.escape(str(e))}")

        if model in installed or f"{model}:latest" in installed:
            return (True, f"✅ Ollama reachable at {parts.netloc}. Model '{model}' is installed.")
        shown = ", ".join(n for n in installed if n) or "none"
        return (False, f"❌ Ollama is running, but model '{html.escape(model)}' is not installed. "
                       f"Run: ollama pull {html.escape(model)}  (installed: {html.escape(shown)})")

    # ---- OpenAI-compatible (Chat Completions over HTTP) -----------------------------
    #
    # Plain HTTP rather than the openai package: the protocol is what OpenAI,
    # LM Studio, llama.cpp, vLLM and OpenRouter have in common, and a request
    # that sticks to it reaches all of them. Nothing is retried; the message
    # says what happened and the operator asks again.

    def _openai_headers(self, key_env: str) -> dict:
        """A bearer token when the variable holds one. A local server needs none."""
        key = os.environ.get(key_env, "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def ask_openai(self, prompt: str, system: Optional[str] = None) -> Tuple[bool, str]:
        return self.chat_openai([user(prompt)], system)

    def chat_openai(self, messages: Sequence[Message],
                    system: Optional[str] = None) -> Tuple[bool, str]:
        """Ask a server that speaks the OpenAI Chat Completions API."""
        conversation = turns(messages)
        section = self._section("openai")
        root = _openai_root(section.get("base_url"))
        who = urlsplit(root).netloc or root
        model = str(section.get("model") or "").strip()
        if not model:
            return (False, "❌ No model is set for the OpenAI-compatible server (ai.openai.model). "
                           "Enter one in Settings → AI; Test connection lists what the server offers.")
        key_env = str(section.get("api_key_env") or "OPENAI_API_KEY").strip()
        if not is_variable_name(key_env):
            return (False, NOT_A_VARIABLE_NAME)
        headers = self._openai_headers(key_env)
        try:
            timeout = float(section.get("timeout_s") or 180)
            request = {"model": model, "messages": _with_system(conversation, system),
                       "stream": False}
            # Neither is sent unless the config asks: a server that does not
            # know a field may reject the request for it.
            optional = []
            if section.get("max_output_tokens"):
                # max_tokens is the older name; OpenAI's reasoning models refuse it.
                request["max_completion_tokens"] = int(section["max_output_tokens"])
                optional.append("max_output_tokens")
            effort = str(section.get("reasoning_effort") or "").strip()
            if effort:
                request["reasoning_effort"] = effort
                optional.append("reasoning_effort")
        except (TypeError, ValueError) as e:
            return (False, f"❌ Config error in ai.openai: {html.escape(str(e))}")

        try:
            r = requests.post(root + "/chat/completions", json=request, headers=headers,
                              timeout=timeout)
        except requests.exceptions.ConnectionError:
            return (False, f"❌ Nothing answers at {html.escape(who)}. Is the server running, and is "
                           "ai.openai.base_url right? (Check your internet connection for a "
                           "cloud service.)")
        except requests.exceptions.Timeout:
            return (False, f"❌ {html.escape(who)}: no answer within {timeout:g} s. Try again, or "
                           "raise ai.openai.timeout_s.")
        except requests.exceptions.RequestException as e:
            return (False, f"❌ {html.escape(who)}: network error - {html.escape(str(e))}")

        if r.status_code != 200:
            return (False, self._openai_status_error(r, who, model, key_env, bool(headers), optional))
        try:
            answer = r.json()
        except ValueError:
            return (False, f"❌ {html.escape(who)} did not answer with JSON. Is ai.openai.base_url "
                           "the API root (it usually ends in /v1)?")
        return self._openai_answer(answer, who)

    @staticmethod
    def _openai_answer(answer, who: str) -> Tuple[bool, str]:
        """Text out of a chat completion, and an honest word when there is none."""
        who = html.escape(who)
        choices = answer.get("choices") if isinstance(answer, dict) else None
        if not choices and isinstance(answer, dict) and answer.get("error"):
            # OpenRouter answers 200 with nothing but an error when the model
            # failed after the request was accepted, every fallback included.
            detail, _code = _openai_error_body(answer)
            return (False, f"❌ {who}: the model failed to answer: {html.escape(detail) or 'no details'}")
        if not choices or not isinstance(choices[0], dict):
            return (False, f"❌ {who} answered, but not with a chat completion (no choices).")
        choice = choices[0]
        if choice.get("finish_reason") == "error" or choice.get("error"):
            # ... or it broke off halfway, with the error on the choice.
            detail, _code = _openai_error_body({"error": choice.get("error") or ""})
            return (False, f"❌ {who}: the model broke off while answering: "
                           f"{html.escape(detail) or 'no details'}")
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            # Some servers send the text in parts, as a request may.
            content = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        text = str(content or "").strip()
        finish = choice.get("finish_reason")

        if message.get("refusal"):
            return (False, f"❌ The model declined this request: {html.escape(str(message['refusal']))}")
        if finish == "content_filter":
            return (False, f"❌ {who}: the server's content filter stopped the answer.")
        if not text:
            # vLLM, llama.cpp and LM Studio put a model's thinking beside the answer.
            thought = message.get("reasoning_content") or message.get("reasoning")
            if finish == "length":
                still = " -- it was still reasoning" if thought else ""
                return (False, f"❌ {who}: the output limit was used up before the model wrote an "
                               f"answer{still}. Raise ai.openai.max_output_tokens (or the server's own "
                               "limit), or lower ai.openai.reasoning_effort.")
            if thought:
                return (False, f"❌ {who}: the model only reasoned and wrote no answer.")
            return (False, f"❌ {who} returned no text (finish reason: {html.escape(str(finish))}).")
        if finish == "length":
            text += ("<br><br>⚠️ The answer was cut off at the output limit. Raise "
                     "ai.openai.max_output_tokens, or the server's own limit (the context length "
                     "of a local server).")
        return (True, text)

    @staticmethod
    def _openai_status_error(r, who: str, model: str, key_env: str, sent_key: bool,
                             optional=()) -> str:
        """One message for each status a Chat Completions server answers with."""
        detail, code = _openai_error(r)
        detail = html.escape(detail.strip())
        # Where more is said after it, the server's words end as a sentence.
        said = detail if not detail or detail[-1] in ".!?" else detail + "."
        who = html.escape(who)
        request_id = r.headers.get("x-request-id")
        where = f" (request {html.escape(request_id)})" if request_id else ""
        status = r.status_code

        if status == 401:
            if not sent_key:
                return (f"❌ {who} wants an API key (401), and {key_env} is not set. Set it "
                        f"(macOS/Linux: export {key_env}=... in your shell profile; Windows: "
                        f"setx {key_env} \"...\") and restart VibeCNC.{where}")
            return (f"❌ {who}: the API key was rejected (401). Check {key_env}: it may be wrong, "
                    f"revoked or for another service.{where}")
        if status == 403:
            return f"❌ {who}: not allowed (403): {detail}{where}"
        wait = ""
        if r.headers.get("retry-after"):
            wait = f" Try again in {html.escape(r.headers['retry-after'])} s."

        if status == 404:
            if code == "model_not_found" or "model" in detail.lower():
                return (f"❌ {who}: model '{html.escape(model)}' was not found (404): {said} "
                        f"Test connection in Settings → AI lists what the server offers.{where}")
            what = f": {said}" if detail else "."
            return (f"❌ {who}: not found (404){what} If the address is wrong: ai.openai.base_url "
                    f"should be the API root, which usually ends in /v1.{where}")
        # OpenAI says an empty account with 429 insufficient_quota, OpenRouter
        # with 402.
        if status == 402 or (status == 429 and (code == "insufficient_quota"
                                                or "quota" in detail.lower())):
            return (f"❌ {who}: the account has no quota or credit left ({status}): {said} Check the "
                    f"billing of the account the key belongs to.{wait}{where}")
        if status == 429:
            return f"❌ {who}: rate limit reached (429).{wait}{where}"
        if status == 400:
            hint = ""
            if optional:
                fields = " or ".join(f"ai.openai.{name}" for name in optional)
                hint = f" If the server does not know {fields}, remove it from the config."
            return f"❌ {who} rejected the request (400): {detail}{hint}{where}"
        return f"❌ {who}: error {status}: {detail or 'no details'}{where}"

    def check_openai(self, model: Optional[str] = None, base_url: Optional[str] = None,
                     key_env: Optional[str] = None) -> Tuple[bool, str]:
        """Does the server answer, and does it offer this model? Nothing is run.

        Model, address and key variable are the ones given (the settings
        dialog passes what is typed, saved or not), else the saved ones. Like
        the Claude check it cannot see a credit balance or a quota, and where
        the model list is public (OpenRouter) it cannot see whether the key
        works either.
        """
        section = self._section("openai")
        root = _openai_root(base_url if base_url is not None else section.get("base_url"))
        who = urlsplit(root).netloc or root
        model = str(model if model is not None else section.get("model") or "").strip()
        key_env = str(key_env or section.get("api_key_env") or "OPENAI_API_KEY").strip()
        if not is_variable_name(key_env):
            return (False, NOT_A_VARIABLE_NAME)
        headers = self._openai_headers(key_env)
        try:
            r = requests.get(root + "/models", headers=headers, timeout=10)
        except requests.exceptions.ConnectionError:
            return (False, f"❌ Nothing answers at {html.escape(who)}. Is the server running, and is "
                           "the address right?")
        except requests.exceptions.Timeout:
            return (False, f"❌ {html.escape(who)} did not answer within 10 s.")
        except requests.exceptions.RequestException as e:
            return (False, f"❌ {html.escape(who)}: {html.escape(str(e))}")

        if r.status_code == 404:
            return (False, f"❌ {html.escape(who)}: nothing at {html.escape(root)}/models (404). The "
                           "address should be the API root, which usually ends in /v1 -- or this "
                           "server does not list its models.")
        if r.status_code != 200:
            return (False, self._openai_status_error(r, who, model, key_env, bool(headers)))
        try:
            listed = r.json().get("data")
            offered = [str(m.get("id")) for m in listed if isinstance(m, dict) and m.get("id")]
        except (ValueError, AttributeError, TypeError):
            return (False, f"❌ {html.escape(who)} answered, but not with a model list. Is the "
                           "address the API root (it usually ends in /v1)?")

        shown = ", ".join(offered[:20]) or "none"
        if len(offered) > 20:
            shown += f", ... ({len(offered)} in all)"
        shown = html.escape(shown)
        if not model:
            return (False, f"❌ {html.escape(who)} answers, but no model is entered. It offers: {shown}")
        if model in offered:
            return (True, f"✅ {html.escape(who)} answers and offers model '{html.escape(model)}'. "
                          "This check does not run the model, so it cannot see a credit balance "
                          "or quota -- and where anyone may read the model list, as on OpenRouter, "
                          "not whether the key works either. The first analysis shows both.")
        return (False, f"❌ {html.escape(who)} answers, but does not offer model "
                       f"'{html.escape(model)}'. It offers: {shown}")
