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

For a temporary machine where you do not want a key in a command, shell history,
or config file, leave these variables unset. Casper will ask you to pick a
provider and enter its key interactively; the key is hidden while you type and
is kept only for that Casper process.

For each provider, Casper picks a default model. You can override it by also
setting `CASPER_MODEL`.
