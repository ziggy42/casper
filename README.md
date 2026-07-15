# Casper 👻

A coding agent you can run without installing anything.

```sh
curl -fsSL https://raw.githubusercontent.com/ziggy42/casper/main/casper.py | ANTHROPIC_API_KEY=... python3
```

## Commands

* `/clear` resets the conversation and clears the screen.
* `Ctrl-C` cancels the current turn.
* `Ctrl-D` exits.

## Config

Set an API key with one of the following env vars:
* `ANTHROPIC_API_KEY`
* `OPENAI_API_KEY`
* `GEMINI_API_KEY`

For each provider, Casper picks a default model. You can override it by also
setting `CASPER_MODEL`.