# claude_client.py — the AI backends: Claude through the Anthropic SDK, and
# Ollama over HTTP.
#
# Every ask_* and check_* returns (ok, text). When ok is False the text says
# what to do about it, in words, rather than what the library threw: it is
# shown to the operator in the chat.
import html
import os
from typing import Optional, Tuple
from urllib.parse import urlsplit

import requests

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
DEFAULT_OLLAMA_MODEL = "qwen3.8:27b"

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
    """The backend `ai.mode` names: "claude", "ollama", or the unknown word itself.

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

    def ask(self, prompt: str, system: Optional[str] = None) -> Tuple[bool, str]:
        """Asks the backend ai.mode names. An unknown mode is said, not guessed."""
        backend = backend_of(self._ai())
        if backend == "claude":
            return self.ask_claude(prompt, system)
        if backend == "ollama":
            return self.ask_ollama(prompt, system)
        return (False, f"❌ config.yaml: ai.mode '{html.escape(backend)}' is not known. "
                       "Use claude or ollama (Settings → AI).")

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
        """Ask Claude. The SDK retries rate limits, overload and 5xx by itself."""
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
                "messages": [{"role": "user", "content": prompt}],
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
        """Ask Ollama API with comprehensive error handling"""
        try:
            o = self._section("ollama")
            base_url = o.get("base_url", "")
            if not base_url:
                return (False, "❌ config.yaml invalid: ai.ollama.base_url missing")

            model = (o.get("model") or DEFAULT_OLLAMA_MODEL).strip()
            timeout = float(o.get("timeout_s") or 300)
            messages = [{"role": "user", "content": prompt}]
            if system:
                messages.insert(0, {"role": "system", "content": system})
            data = {"model": model, "messages": messages, "stream": False}
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
