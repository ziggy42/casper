# Casper 👻

A coding agent you can run without installing anything.

```sh
curl -fsSL https://raw.githubusercontent.com/ziggy42/casper/main/casper.py \
    | ANTHROPIC_API_KEY=sk-... python3
```

## Picking a model

Casper runs on whatever you have. It selects the provider from whichever API
key is set, and each provider has a sensible default model:

| Environment variable | Provider  | Default model            |
| -------------------- | --------- | ------------------------ |
| `ANTHROPIC_API_KEY`  | Anthropic | `claude-opus-4-8`        |
| `OPENAI_API_KEY`     | OpenAI    | `gpt-5.6`                |
| `GEMINI_API_KEY`     | Google    | `gemini-3-flash-preview` |

Defaults are pinned model IDs, not moving aliases like `gemini-flash-latest`,
so casper's behavior doesn't change underneath you — and because the newest
model is usually also the most overloaded one. Point `CASPER_MODEL` at an
alias if you'd rather always ride the latest.

They're checked in that order, so the first key present wins. Override the
model for any provider with `CASPER_MODEL`:

```sh
CASPER_MODEL=claude-sonnet-5 ANTHROPIC_API_KEY=sk-... python3 casper.py
```
