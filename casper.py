#!/usr/bin/env python3
"""Casper — a coding agent you can run without installing anything.

    curl -fsSL https://raw.githubusercontent.com/ziggy42/casper/main/casper.py \\
        | ANTHROPIC_API_KEY=sk-... python3

Zero dependencies, single file, Linux and macOS only. The provider is picked
from whichever API key is set: ANTHROPIC_API_KEY, OPENAI_API_KEY, or
GEMINI_API_KEY / GOOGLE_API_KEY. Override the default model with CASPER_MODEL.
"""

import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request

SYSTEM = f"""You are Casper, a coding agent running in a shell on the user's machine.
Working directory: {os.getcwd()}
Platform: {platform.system()} {platform.machine()}

Use your tools to inspect and modify the system. Do the work, then briefly
report what you did. Prefer acting over asking."""

# One schema shared by all providers (OpenAI-style JSON Schema, which the
# Anthropic and Gemini APIs accept as-is for tool parameters).
TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command and return its combined stdout and stderr.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The command to run."}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file and return its contents.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path of the file to read."}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file, creating it (and parent directories) if needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to write."},
                "content": {"type": "string", "description": "Full content of the file."},
            },
            "required": ["path", "content"],
        },
    },
]


def run_tool(name, args):
  try:
    if name == "bash":
      print(f"  $ {args['command']}")
      r = subprocess.run(args["command"], shell=True, check=False,
                         capture_output=True, text=True, timeout=300)
      out = (r.stdout + r.stderr).strip()
      if r.returncode != 0:
        out += f"\n(exit code {r.returncode})"
    elif name == "read_file":
      print(f"  read {args['path']}")
      with open(args["path"], encoding="utf-8") as f:
        out = f.read()
    elif name == "write_file":
      print(f"  write {args['path']}")
      parent = os.path.dirname(args["path"])
      if parent:
        os.makedirs(parent, exist_ok=True)
      with open(args["path"], "w", encoding="utf-8") as f:
        f.write(args["content"])
      out = "ok"
    else:
      out = f"unknown tool: {name}"
  except Exception as e:  # pylint: disable=broad-exception-caught
    # Any tool failure is fed back to the model as text so it can adapt.
    out = f"error: {e}"
  if len(out) > 50_000:
    out = out[:50_000] + "\n... (output truncated)"
  return out or "(no output)"


def http_post(url, headers, body):
  req = urllib.request.Request(
      url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers}
  )
  try:
    with urllib.request.urlopen(req) as r:
      return json.load(r)
  except urllib.error.HTTPError as e:
    raise RuntimeError(f"API error {e.code}: {e.read().decode()}") from None


# Each provider keeps the conversation in its own wire format and returns a
# normalized (text, tool_calls) pair, where a tool call is {id, name, args}.

class Anthropic:
  name = "anthropic"
  model = "claude-opus-4-8"

  def __init__(self, key):
    self.key = key
    self.messages = []

  def send_user(self, text):
    self.messages.append({"role": "user", "content": text})
    return self._request()

  def send_results(self, results):
    self.messages.append({
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": call["id"], "content": out}
            for call, out in results
        ],
    })
    return self._request()

  def _request(self):
    resp = http_post(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
        {
            "model": self.model,
            "max_tokens": 8192,
            "system": SYSTEM,
            "messages": self.messages,
            "tools": [
                {"name": t["name"], "description": t["description"],
                 "input_schema": t["parameters"]}
                for t in TOOLS
            ],
        },
    )
    self.messages.append({"role": "assistant", "content": resp["content"]})
    text = "".join(b["text"] for b in resp["content"] if b["type"] == "text")
    calls = [
        {"id": b["id"], "name": b["name"], "args": b["input"]}
        for b in resp["content"] if b["type"] == "tool_use"
    ]
    return text, calls


class OpenAI:
  name = "openai"
  model = "gpt-5.1"

  def __init__(self, key):
    self.key = key
    self.messages = [{"role": "system", "content": SYSTEM}]

  def send_user(self, text):
    self.messages.append({"role": "user", "content": text})
    return self._request()

  def send_results(self, results):
    for call, out in results:
      self.messages.append(
          {"role": "tool", "tool_call_id": call["id"], "content": out})
    return self._request()

  def _request(self):
    resp = http_post(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {self.key}"},
        {
            "model": self.model,
            "messages": self.messages,
            "tools": [{"type": "function", "function": t} for t in TOOLS],
        },
    )
    msg = resp["choices"][0]["message"]
    self.messages.append(msg)
    calls = [
        {"id": tc["id"], "name": tc["function"]["name"],
         "args": json.loads(tc["function"]["arguments"])}
        for tc in msg.get("tool_calls") or []
    ]
    return msg.get("content") or "", calls


class Google:
  name = "google"
  model = "gemini-flash-latest"

  def __init__(self, key):
    self.key = key
    self.contents = []

  def send_user(self, text):
    self.contents.append({"role": "user", "parts": [{"text": text}]})
    return self._request()

  def send_results(self, results):
    self.contents.append({
        "role": "user",
        "parts": [
            {"functionResponse": {
                "name": call["name"], "response": {"output": out}}}
            for call, out in results
        ],
    })
    return self._request()

  def _request(self):
    resp = http_post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
        {"x-goog-api-key": self.key},
        {
            "system_instruction": {"parts": [{"text": SYSTEM}]},
            "contents": self.contents,
            "tools": [{
                "function_declarations": [
                    {"name": t["name"], "description": t["description"],
                      "parameters": t["parameters"]}
                    for t in TOOLS
                ]
            }],
        },
    )
    parts = resp["candidates"][0]["content"].get("parts", [])
    self.contents.append({"role": "model", "parts": parts})
    text = "".join(p["text"] for p in parts if "text" in p)
    calls = [
        {"id": p["functionCall"]["name"], "name": p["functionCall"]["name"],
         "args": p["functionCall"].get("args", {})}
        for p in parts if "functionCall" in p
    ]
    return text, calls


def pick_provider():
  if os.environ.get("ANTHROPIC_API_KEY"):
    return Anthropic(os.environ["ANTHROPIC_API_KEY"])
  if os.environ.get("OPENAI_API_KEY"):
    return OpenAI(os.environ["OPENAI_API_KEY"])
  if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
    return Google(os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"])
  sys.exit(
      "casper: set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY / GOOGLE_API_KEY")


def main():
  # When piped from curl, stdin is the script itself — reattach it to the
  # terminal so the prompt works.
  if not sys.stdin.isatty():
    os.dup2(os.open("/dev/tty", os.O_RDONLY), 0)
  try:
    # Imported for its side effect: hooks line editing and history into input().
    import readline  # pylint: disable=import-outside-toplevel,unused-import  # noqa: F401
  except ImportError:
    pass

  provider = pick_provider()
  provider.model = os.environ.get("CASPER_MODEL", provider.model)
  print(f"casper · {provider.name} · {provider.model} · ctrl-d to exit")

  while True:
    try:
      line = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
      print()
      return
    if not line:
      continue
    try:
      text, calls = provider.send_user(line)
      while True:
        if text:
          print(text)
        if not calls:
          break
        results = [(call, run_tool(call["name"], call["args"]))
                   for call in calls]
        text, calls = provider.send_results(results)
    except RuntimeError as e:
      print(f"casper: {e}")


if __name__ == "__main__":
  main()
