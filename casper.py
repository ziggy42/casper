#!/usr/bin/env python3
import itertools
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar, TypedDict

type Json = dict[str, Any]


class ToolCall(TypedDict):
  """A tool invocation normalized across providers."""
  id: str
  name: str
  args: Json


SYSTEM = f"""\
You are Casper, a coding agent running in a shell on the user's machine.
Working directory: {os.getcwd()}
Platform: {platform.system()} {platform.machine()}

Use your tools to inspect and modify the system. Prefer edit_file for changes
to existing files (read the file first); write_file is for new files or full
rewrites. Do the work, then briefly report what you did. Prefer acting over
asking."""


# ── Terminal output ──────────────────────────────────────────────────────
# Everything casper prints flows through emit/newline, so streamed text, the
# tool trace, and the waiting spinner never trample each other's lines.

def style(code: int, s: str) -> str:
  return f"\x1b[{code}m{s}\x1b[0m"


def bold(s: str) -> str:
  return style(1, s)


def dim(s: str) -> str:
  return style(2, s)


def red(s: str) -> str:
  return style(31, s)


def green(s: str) -> str:
  return style(32, s)


def plural(n: int, noun: str) -> str:
  return f"{n} {noun}" + ("" if n == 1 else "s")


class _Term:  # pylint: disable=too-few-public-methods
  """Mutable display state; the module is in effect one terminal object."""
  midline: ClassVar[bool] = False
  spinner_stop: ClassVar[threading.Event | None] = None
  spinner_thread: ClassVar[threading.Thread | None] = None


def spin() -> None:
  """Show a waiting animation until the next output through emit/newline."""
  stop = threading.Event()

  def loop() -> None:
    for frame in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
      print("\r" + dim(f"{frame} thinking…"), end="", flush=True)
      if stop.wait(0.1):
        break
    print("\r\x1b[K", end="", flush=True)  # erase the spinner line

  thread = threading.Thread(target=loop, daemon=True)
  thread.start()
  _Term.spinner_stop, _Term.spinner_thread = stop, thread


def unspin() -> None:
  if _Term.spinner_stop and _Term.spinner_thread:
    _Term.spinner_stop.set()
    # Wait for the line to be erased before anyone prints over it.
    _Term.spinner_thread.join()
    _Term.spinner_stop = _Term.spinner_thread = None


def emit(chunk: str) -> None:
  """Print a streamed piece of text, remembering if the line is unfinished."""
  unspin()
  print(chunk, end="", flush=True)
  _Term.midline = not chunk.endswith("\n")


def newline() -> None:
  """Terminate the streamed line, if one is open."""
  unspin()
  if _Term.midline:
    print()
  _Term.midline = False


# ── Tools ────────────────────────────────────────────────────────────────
# The schema and the implementations live together: a tool's "required"
# parameters must stay in step with what execute_tool destructures. The
# schema is OpenAI-style JSON Schema, which the Anthropic and Gemini APIs
# accept as-is for tool parameters.

TOOLS: list[Json] = [
    {
        "name": "bash",
        "description": "Run a shell command and return its combined stdout "
                       "and stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string",
                            "description": "The command to run."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file and return its contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Path of the file to read."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace text in a file. old_text must match exactly "
                       "(including whitespace) and be unique in the file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Path of the file to edit."},
                "old_text": {"type": "string",
                             "description": "Exact text to replace; must "
                                            "appear exactly once in the file."},
                "new_text": {"type": "string",
                             "description": "The text to replace it with."},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file, creating it (and parent "
                       "directories) if needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Path of the file to write."},
                "content": {"type": "string",
                            "description": "Full content of the file."},
            },
            "required": ["path", "content"],
        },
    },
]

# What every tool returns: (output for the model, display body, failed).
type ToolResult = tuple[str, str, bool]


def bash(command: str) -> ToolResult:
  # stderr folds into stdout so the streams interleave in real order,
  # as the tool description promises the model.
  r = subprocess.run(command, shell=True, check=False, timeout=300,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     encoding="utf-8", errors="replace")
  out = r.stdout.strip()
  if r.returncode == 0:
    return out, out, False
  body = (out + "\n" if out else "") + red(f"exit {r.returncode}")
  return out + f"\n(exit code {r.returncode})", body, True


def read_file(path: str) -> ToolResult:
  content = Path(path).expanduser().read_text(encoding="utf-8")
  read = plural(len(content.splitlines()), "line")
  return content, dim(f"Read {read}"), False


def edit_file(path: str, old_text: str, new_text: str) -> ToolResult:
  target = Path(path).expanduser()
  content = target.read_text(encoding="utf-8")
  count = content.count(old_text)
  if count == 0:
    raise ValueError(f"text not found in {path}; "
                     "it must match exactly, including whitespace")
  if count > 1:
    raise ValueError(f"found {count} occurrences of the text in {path}; "
                     "the text must be unique — provide more context")
  target.write_text(content.replace(old_text, new_text), encoding="utf-8")
  body = dim(f"Replaced {plural(len(old_text.splitlines()), 'line')} "
             f"with {plural(len(new_text.splitlines()), 'line')}")
  return "ok", body, False


def write_file(path: str, content: str) -> ToolResult:
  target = Path(path).expanduser()
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(content, encoding="utf-8")
  wrote = plural(len(content.splitlines()), "line")
  return "ok", dim(f"Wrote {wrote}"), False


def execute_tool(name: str, args: Json) -> ToolResult:
  """Dispatch one tool call to its implementation, converting failures."""
  try:
    # Dict patterns match on "at least these keys", so extra fields a model
    # invents are tolerated; missing required ones fall through to the error.
    match name, args:
      case "bash", {"command": command}:
        return bash(command)
      case "read_file", {"path": path}:
        return read_file(path)
      case "edit_file", {"path": path,
                         "old_text": old_text, "new_text": new_text}:
        return edit_file(path, old_text, new_text)
      case "write_file", {"path": path, "content": content}:
        return write_file(path, content)
      case _:
        out = f"unknown tool or missing arguments: {name}"
        return out, red(out), True
  except Exception as e:  # pylint: disable=broad-exception-caught
    # Any tool failure is fed back to the model as text so it can adapt.
    return f"error: {e}", red(f"error: {e}"), True


def tool_title(name: str, args: Json) -> str:
  """Render a call as "name(one-line summary)", capped so it can't wrap."""
  summary = " ".join(str(args.get("command") or args.get("path") or "").split())
  if len(summary) > 60:
    summary = summary[:59] + "…"
  return name + (f"({summary})" if summary else "")


# How much of a tool's output is shown: at most MAX_RESULT_LINES lines (the
# rest collapse into a count), each capped at MAX_RESULT_COLS characters so a
# single giant line (minified JS, base64) can't flood the terminal.
MAX_RESULT_LINES = 10
MAX_RESULT_COLS = 200


def run_tool(name: str, args: Json) -> str:
  """Execute one tool call, tracing it to the terminal: a pending line while
  the tool runs, rewritten with a green/red dot once it finishes, then the
  result body indented beneath it."""
  title = bold(tool_title(name, args))
  emit(f" {dim('●')} {title}")
  out, body, failed = execute_tool(name, args)
  emit(f"\r {red('●') if failed else green('●')} {title}\n")
  lines = body.rstrip("\n").split("\n") if body else [dim("(no output)")]
  if len(lines) > MAX_RESULT_LINES:
    hidden = len(lines) - MAX_RESULT_LINES
    lines = lines[:MAX_RESULT_LINES] + [dim(f"… +{plural(hidden, 'line')}")]
  # The reset guards against the cut landing inside an ANSI color sequence.
  lines = [line if len(line) <= MAX_RESULT_COLS
           else line[:MAX_RESULT_COLS] + "\x1b[0m" + dim("…")
           for line in lines]
  for i, line in enumerate(lines):
    emit(("   " + dim("⎿") + "  " if i == 0 else "      ") + line + "\n")
  if len(out) > 50_000:
    out = out[:50_000] + "\n... (output truncated)"
  return out or "(no output)"


# ── Providers ────────────────────────────────────────────────────────────
# Each provider keeps the conversation in its own wire format in
# self.messages, prints response text as it streams in, and returns the list
# of tool calls to run, each normalized to a ToolCall.

def sse_post(url: str, headers: dict[str, str], body: Json) -> Iterator[Json]:
  """POST `body`; yield each JSON payload of the SSE reply as it arrives."""
  req = urllib.request.Request(
      url, data=json.dumps(body).encode(),
      headers={"content-type": "application/json"} | headers)
  for attempt in (1, 2, 3):
    try:
      # The timeout is per socket read, so it bounds the silence before the
      # next chunk, not the total duration of the response.
      with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
          data = line.decode().strip()
          if data.startswith("data:"):
            data = data.removeprefix("data:").strip()
            if data == "[DONE]":
              return
            yield json.loads(data)
      return
    except urllib.error.HTTPError as e:
      # Overload and rate-limit errors are transient more often than not;
      # they arrive before any output, so retrying is always safe here.
      if e.code in (429, 500, 502, 503, 529) and attempt < 3:
        time.sleep(attempt)
        continue
      raise RuntimeError(f"API error {e.code}: {e.read().decode()}") from None
    except OSError as e:
      raise RuntimeError(f"network error: {e}") from None


class Provider:
  model: str

  def __init__(self, key: str) -> None:
    self.key = key
    self.messages: list[Json] = []

  def send_user(self, text: str) -> list[ToolCall]:
    raise NotImplementedError

  def send_results(self,
                   results: list[tuple[ToolCall, str]]) -> list[ToolCall]:
    raise NotImplementedError


class Anthropic(Provider):
  model = "claude-opus-4-8"

  def send_user(self, text: str) -> list[ToolCall]:
    self.messages.append({"role": "user", "content": text})
    return self._request()

  def send_results(self,
                   results: list[tuple[ToolCall, str]]) -> list[ToolCall]:
    self.messages.append({
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": call["id"], "content": out}
            for call, out in results
        ],
    })
    return self._request()

  def _request(self) -> list[ToolCall]:
    spin()
    events = sse_post(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
        {
            "model": self.model,
            "max_tokens": 8192,
            "stream": True,
            "system": SYSTEM,
            "messages": self.messages,
            "tools": [
                {"name": t["name"], "description": t["description"],
                 "input_schema": t["parameters"]}
                for t in TOOLS
            ],
        },
    )
    # Tool arguments stream as JSON fragments; collect them in "input" as a
    # string, then parse once the stream ends.
    blocks: list[Json] = []
    for ev in events:
      if ev["type"] == "content_block_start":
        block = ev["content_block"]
        if block["type"] == "tool_use":
          blocks.append({"type": "tool_use", "id": block["id"],
                         "name": block["name"], "input": ""})
        else:
          blocks.append({"type": "text", "text": ""})
      elif ev["type"] == "content_block_delta":
        delta = ev["delta"]
        if delta["type"] == "text_delta":
          blocks[-1]["text"] += delta["text"]
          emit(delta["text"])
        elif delta["type"] == "input_json_delta":
          blocks[-1]["input"] += delta["partial_json"]
      elif ev["type"] == "error":
        raise RuntimeError(f"API error: {ev['error']['message']}")
    newline()
    for b in blocks:
      if b["type"] == "tool_use":
        b["input"] = json.loads(b["input"] or "{}")
    # The API rejects empty text blocks in the history.
    blocks = [b for b in blocks if b.get("text") != ""]
    self.messages.append({"role": "assistant", "content": blocks})
    return [{"id": b["id"], "name": b["name"], "args": b["input"]}
            for b in blocks if b["type"] == "tool_use"]


class OpenAI(Provider):
  model = "gpt-5.6"

  def send_user(self, text: str) -> list[ToolCall]:
    self.messages.append({"role": "user", "content": text})
    return self._request()

  def send_results(self,
                   results: list[tuple[ToolCall, str]]) -> list[ToolCall]:
    for call, out in results:
      self.messages.append(
          {"role": "tool", "tool_call_id": call["id"], "content": out})
    return self._request()

  def _request(self) -> list[ToolCall]:
    spin()
    events = sse_post(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {self.key}"},
        {
            "model": self.model,
            "stream": True,
            "messages": [{"role": "system", "content": SYSTEM},
                         *self.messages],
            "tools": [{"type": "function", "function": t} for t in TOOLS],
        },
    )
    # Tool calls stream as fragments addressed by "index"; ids and names
    # arrive on the first fragment, argument JSON dribbles in across the rest.
    text = ""
    tool_calls: list[Json] = []
    for ev in events:
      if not ev.get("choices"):
        continue
      delta = ev["choices"][0]["delta"]
      if delta.get("content"):
        text += delta["content"]
        emit(delta["content"])
      for tc in delta.get("tool_calls") or []:
        if tc["index"] == len(tool_calls):
          tool_calls.append({"id": "", "type": "function",
                             "function": {"name": "", "arguments": ""}})
        slot = tool_calls[tc["index"]]
        slot["id"] += tc.get("id") or ""
        func = tc.get("function") or {}
        slot["function"]["name"] += func.get("name") or ""
        slot["function"]["arguments"] += func.get("arguments") or ""
    newline()
    msg: Json = {"role": "assistant", "content": text or None}
    if tool_calls:
      msg["tool_calls"] = tool_calls
    self.messages.append(msg)
    return [{"id": tc["id"], "name": tc["function"]["name"],
             "args": json.loads(tc["function"]["arguments"] or "{}")}
            for tc in tool_calls]


class Google(Provider):
  # Pinned on purpose: the "gemini-flash-latest" alias tracks the newest
  # Flash model, which tends to be the most overloaded one (503s, queues).
  model = "gemini-3-flash-preview"

  def send_user(self, text: str) -> list[ToolCall]:
    self.messages.append({"role": "user", "parts": [{"text": text}]})
    return self._request()

  def send_results(self,
                   results: list[tuple[ToolCall, str]]) -> list[ToolCall]:
    self.messages.append({
        "role": "user",
        "parts": [
            {"functionResponse": {
                "name": call["name"], "response": {"output": out}}}
            for call, out in results
        ],
    })
    return self._request()

  def _request(self) -> list[ToolCall]:
    spin()
    events = sse_post(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{self.model}:streamGenerateContent?alt=sse",
        {"x-goog-api-key": self.key},
        {
            "system_instruction": {"parts": [{"text": SYSTEM}]},
            "contents": self.messages,
            "tools": [{
                "function_declarations": [
                    {"name": t["name"], "description": t["description"],
                      "parameters": t["parameters"]}
                    for t in TOOLS
                ]
            }],
        },
    )
    # Merge adjacent plain-text fragments; keep every other part verbatim
    # (functionCall, thoughtSignature, ...) so the history replays cleanly.
    parts: list[Json] = []
    for ev in events:
      candidates = ev.get("candidates")
      if not candidates:
        continue
      for part in candidates[0].get("content", {}).get("parts", []):
        if "text" in part:
          emit(part["text"])
        if set(part) == {"text"} and parts and set(parts[-1]) == {"text"}:
          parts[-1]["text"] += part["text"]
        else:
          parts.append(part)
    newline()
    self.messages.append({"role": "model", "parts": parts})
    return [{"id": p["functionCall"]["name"],
             "name": p["functionCall"]["name"],
             "args": p["functionCall"].get("args", {})}
            for p in parts if "functionCall" in p]


# ── Entry ────────────────────────────────────────────────────────────────

def pick_provider() -> Provider:
  """Build the provider for whichever API key is set, CASPER_MODEL applied."""
  provider: Provider
  if os.environ.get("ANTHROPIC_API_KEY"):
    provider = Anthropic(os.environ["ANTHROPIC_API_KEY"])
  elif os.environ.get("OPENAI_API_KEY"):
    provider = OpenAI(os.environ["OPENAI_API_KEY"])
  elif os.environ.get("GEMINI_API_KEY"):
    provider = Google(os.environ["GEMINI_API_KEY"])
  else:
    sys.exit("casper: set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
             "or GEMINI_API_KEY")
  provider.model = os.environ.get("CASPER_MODEL", provider.model)
  return provider


def main() -> None:
  # When piped from curl, stdin is the script itself — reattach it to the
  # terminal so the prompt works.
  if not sys.stdin.isatty():
    try:
      os.dup2(os.open("/dev/tty", os.O_RDONLY), 0)
    except OSError:
      sys.exit("casper: no terminal available")
  try:
    # Imported for its side effect: hooks line editing and history into input().
    import readline  # pylint: disable=import-outside-toplevel,unused-import  # noqa: F401
  except ImportError:
    pass

  provider = pick_provider()
  hints = dim(f"{provider.model} · /clear to reset · ctrl-d to exit")
  banner = f"👻 {bold('casper')}  {hints}"
  print(banner)

  while True:
    try:
      line = input("> ").strip()
    except EOFError:
      print()
      return
    except KeyboardInterrupt:
      print()
      continue
    if not line:
      continue
    if line == "/clear":
      provider.messages.clear()
      # Erase the screen and scrollback, home the cursor, restate the banner.
      print(f"\x1b[2J\x1b[3J\x1b[H{banner}")
      continue
    # If a turn dies partway (API error, ctrl-c), drop it from the history
    # entirely: a tool call left without its result would make every
    # subsequent request fail.
    checkpoint = len(provider.messages)
    try:
      calls = provider.send_user(line)
      while calls:
        results = [(call, run_tool(call["name"], call["args"]))
                   for call in calls]
        calls = provider.send_results(results)
    except KeyboardInterrupt:
      del provider.messages[checkpoint:]
      newline()
      print("casper: interrupted")
    except Exception as e:  # pylint: disable=broad-exception-caught
      # RuntimeError carries API/network errors; anything else is a provider
      # sending events we don't expect. Neither may kill the REPL, and the
      # rollback above keeps the history consistent either way.
      del provider.messages[checkpoint:]
      newline()
      detail = str(e) if isinstance(e, RuntimeError) else f"unexpected: {e!r}"
      print(f"casper: {detail}")


if __name__ == "__main__":
  main()
