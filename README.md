# Casper 👻

A coding agent you can run without installing anything.

Casper is a single Python file with zero dependencies. It's meant for places
you don't want to install, update, or maintain tooling — a throwaway VM, a
Docker container, a Raspberry Pi you shouldn't really have a shell on. You pipe
it straight from the web and land in a prompt:

```sh
curl -fsSL https://raw.githubusercontent.com/ziggy42/casper/main/casper.py \
    | ANTHROPIC_API_KEY=sk-... python3
```

Or download and run it:

```sh
ANTHROPIC_API_KEY=sk-... python3 casper.py
```

Requires Python 3.12+. Linux and macOS only — Windows is not supported.

## Picking a model

Casper runs on whatever you have. It selects the provider from whichever API
key is set, and each provider has a sensible default model:

| Environment variable            | Provider  | Default model         |
| ------------------------------- | --------- | --------------------- |
| `ANTHROPIC_API_KEY`             | Anthropic | `claude-opus-4-8`     |
| `OPENAI_API_KEY`                | OpenAI    | `gpt-5.1`             |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google | `gemini-flash-latest` |

They're checked in that order, so the first key present wins. Override the
model for any provider with `CASPER_MODEL`:

```sh
CASPER_MODEL=claude-sonnet-5 ANTHROPIC_API_KEY=sk-... python3 casper.py
```

## What it can do

Casper has three tools:

- **bash** — run a shell command
- **read_file** — read a file
- **write_file** — write a file

It prints each action as it happens, so you can watch exactly what it touches.
Type your request at the `>` prompt; press Ctrl-D to exit.

## Why it's safe to read before you run

Running a script piped from the internet on your machine is a big ask, so the
code is meant to be read. It's a few hundred lines of standard-library Python
with no hidden behavior — open `casper.py` and check it yourself before you run
it.

## License

Apache License 2.0. See [LICENSE](LICENSE).
