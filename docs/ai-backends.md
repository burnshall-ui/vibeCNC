# AI backends

Vibe CNC talks to one backend at a time, chosen by `ai.mode` in `config.yaml`
or in **Settings → AI**. The editor, the simulation and the linter need none:
`ai.offline: true` turns the AI off.

Keys are read from environment variables, never from `config.yaml`. The
config holds only the variable's *name* (`api_key_env`). Start the app from a
shell that has the variable set.

**Test connection** in Settings → AI asks the backend whether the address,
the key and the model are right, without running the model. It cannot see a
credit balance or a quota: an empty account passes the test and is refused on
the first analysis.

## Claude (`claude`)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."     # macOS / Linux, e.g. in the shell profile
setx ANTHROPIC_API_KEY "sk-ant-..."       # Windows
```

```yaml
ai:
  mode: claude
  anthropic:
    model: claude-sonnet-5-5        # cheaper: claude-haiku-5-5, stronger: claude-opus-5-5
    api_key_env: ANTHROPIC_API_KEY
    max_output_tokens: 16000        # thinking counts too; about 21000 is the most without streaming
    effort: medium                  # low | medium | high | xhigh | max; empty for models without it
```

API credits are bought in the Anthropic Console and are separate from a
claude.ai subscription. An account without credits is refused with "Your
credit balance is too low". `claude-sonnet-4-20250514`, an earlier default,
was retired on 2026-06-15.

## Ollama (`ollama`)

Ollama runs the model on your own hardware. It needs macOS 14 or newer,
Windows or Linux; on an Intel Mac it runs on the CPU only.

```bash
ollama pull qwen3.8:27b     # about 18 GB: a 32 GB machine or a 24 GB GPU
ollama serve                # if it does not run as a service
```

```yaml
ai:
  mode: ollama
  ollama:
    base_url: http://127.0.0.1:11434/api/chat   # another machine: http://<host>:11434/api/chat
    model: qwen3.8:27b                           # smaller: qwen3.5:4b, about 3.5 GB
    num_ctx: 8192                                # Ollama's default is 4096 below 24 GiB of VRAM
    think: false                                 # qwen3.x and gpt-oss; remove for other models
```

No local model has been measured on G-code yet. Run the evals
([evals.md](evals.md)) before relying on one.

## OpenAI-compatible servers (`openai`)

Anything that speaks the OpenAI Chat Completions API: OpenAI itself,
OpenRouter, LM Studio, llama.cpp's `llama-server`, vLLM, or Ollama's `/v1`
address. No extra package is needed.

```yaml
ai:
  mode: openai
  openai:
    base_url: https://api.openai.com/v1   # LM Studio: http://localhost:1234/v1
    model: ""                             # as the server names it
    api_key_env: OPENAI_API_KEY           # leave the variable unset for a local server
    # max_output_tokens: 16000            # sent as max_completion_tokens, only when set
    # reasoning_effort: medium            # only when set
```

There is no default model, because what a server offers depends on the
server; with no model entered, Test connection lists the ones it has. On
OpenAI an empty account shows up on the first analysis as "no quota or credit
left (429)".

### OpenRouter

- Address `https://openrouter.ai/api/v1`, key variable `OPENROUTER_API_KEY`.
  Enter the variable's name in Settings → AI, never the key itself.
- Model ids look like `vendor/model`. Free ones end in `:free` and have a low
  daily limit.
- Its model list answers without a key, so a green Test connection says
  nothing about the key; the first analysis does. An empty account is
  reported as "no quota or credit left (402)".
- What is analysed goes to OpenRouter and to the provider behind the model.

## Prompts

`ai.system_prompt` sets what a review is told to do, `ai.generate_prompt` what
a generation is told. The review prompt itself, with the shop rules, the tool
table, the cycle cards and the code, is built in
`vibe_cnc/analysis_prompt.py`, so the review eval sends exactly what
**AI ANALYZE** sends.
