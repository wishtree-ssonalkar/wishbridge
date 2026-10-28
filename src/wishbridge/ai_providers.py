"""AI connections for Fix code besides Claude: Gemini and OpenAI (ChatGPT) with an API key, and the Codex and Gemini
command-line tools on a laptop. Plain HTTPS - no extra packages. Every warehouse's ai.py sends through these; the
prompts stay in the warehouse.

Keys come from the environment (GEMINI_API_KEY, OPENAI_API_KEY): set on the laptop, or loaded from the workspace's
Databricks secrets in the app (hosting.load_keys). They are never written to a project.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_URL = "https://api.openai.com/v1"
_FALLBACK = {("gemini", True): "gemini-2.5-flash", ("gemini", False): "gemini-2.5-pro",
             ("openai", True): "gpt-5-mini", ("openai", False): "gpt-5"}
_MODEL_ENV = {"gemini": "WISHBRIDGE_GEMINI_MODEL", "openai": "WISHBRIDGE_OPENAI_MODEL"}  # to pin a model
_PICKED: dict[tuple[str, bool, str], str] = {}

# Command-line tools a person signs in to on their laptop: (program, how to install, how to sign in)
CLI = {"codex": ("codex", "npm install -g @openai/codex", "codex login"),
       "gemini-cli": ("gemini", "npm install -g @google/gemini-cli", "gemini   (then choose: Login with Google)")}


class ProviderError(RuntimeError):
    """A message for the person: why this connection could not answer."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


# ----------------------------------------------------------------- HTTPS

def _http(url: str, headers: dict, body: dict | None = None, timeout: int = 900) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(detail).get("error") or {}
            detail = (err.get("message") or detail) + (f" [{err.get('code') or err.get('status')}]"
                                                        if err.get("code") or err.get("status") else "")
        except (ValueError, AttributeError):
            pass
        raise ProviderError(detail.strip()[:400], e.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"could not connect ({getattr(e, 'reason', e)})") from None


def _gemini_headers(key: str) -> dict:
    return {"x-goog-api-key": key}


def _openai_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# ----------------------------------------------------------------- which model

def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def pick_gemini(names: list[str], fast: bool) -> str | None:
    """The newest Gemini Flash (fast) or Pro model; a stable one wins over a preview of the same version."""
    want = "flash" if fast else "pro"
    found = []
    for n in names:
        m = re.fullmatch(r"(?:models/)?gemini-(\d+(?:\.\d+)?)-(pro|flash)(-preview(?:-[\w-]+)?)?", n)
        if m and m.group(2) == want:
            found.append(((_version(m.group(1)), not m.group(3)), n.removeprefix("models/")))
    return max(found)[1] if found else None


def pick_openai(names: list[str], fast: bool) -> str | None:
    """The newest GPT model: the mini one for fast, the full one for thorough."""
    found = []
    for n in names:
        m = re.fullmatch(r"gpt-(\d+(?:\.\d+)?)(-mini)?", n)
        if m and bool(m.group(2)) == fast:
            found.append((_version(m.group(1)), n))
    return max(found)[1] if found else None


def model(provider: str, fast: bool, key: str) -> str:
    """The model to ask: pinned in WISHBRIDGE_<PROVIDER>_MODEL, else the newest the key can use (asked once)."""
    pinned = os.environ.get(_MODEL_ENV[provider], "").strip()
    if pinned:
        return pinned
    cache = (provider, fast, key[-6:])
    if cache not in _PICKED:
        try:
            if provider == "gemini":
                listed = _http(f"{GEMINI_URL}/models?pageSize=1000", _gemini_headers(key), timeout=60)
                names = [m.get("name", "") for m in listed.get("models", [])
                         if "generateContent" in (m.get("supportedGenerationMethods") or [])]
                picked = pick_gemini(names, fast)
            else:
                listed = _http(f"{OPENAI_URL}/models", _openai_headers(key), timeout=60)
                picked = pick_openai([m.get("id", "") for m in listed.get("data", [])], fast)
        except ProviderError:
            picked = None  # the request itself will say what is wrong
        _PICKED[cache] = picked or _FALLBACK[(provider, fast)]
    return _PICKED[cache]


# ----------------------------------------------------------------- asking

def _explain(provider: str, e: ProviderError) -> str:
    name = "Gemini" if provider == "gemini" else "OpenAI"
    text = str(e)
    low = text.lower()
    if e.status == 401 or "api key not valid" in low or "invalid api key" in low or "incorrect api key" in low:
        return f"{name} does not accept this key (wrong, deleted or disabled). Create a new key and paste it."
    if e.status == 429 and ("insufficient_quota" in low or "billing" in low or "credit" in low):
        return f"The {name} key is valid, but the account has no credits or billing: add them, then try again."
    if e.status == 429:
        return f"{name}'s limit for this key is used up for now (free-tier limits are low) - wait, or turn on billing: {text}"
    if e.status == 403:
        return (f"The key may not use the {name} API" + (" (enable the Generative Language API in the key's Google "
                "Cloud project)" if provider == "gemini" else "") + f": {text}")
    if e.status == 404:
        return f"The {name} model is not available to this key: {text}"
    if e.status == 0:
        return f"Could not reach {name} from here: {text}"
    return f"{name} error {e.status}: {text}"


def _key(provider: str) -> str:
    env = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
    key = os.environ.get(env, "").strip()
    if not key:
        raise ProviderError(f"No {env} yet: paste the key in Fix code.")
    return key


def ask_gemini(system: str, user: str, fast: bool = True) -> str:
    key = _key("gemini")
    name = model("gemini", fast, key)
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": 65536}}
    try:
        r = _http(f"{GEMINI_URL}/models/{name}:generateContent", _gemini_headers(key), body)
    except ProviderError as e:
        raise ProviderError(_explain("gemini", e), e.status) from None
    cand = (r.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    if not text:
        why = (r.get("promptFeedback") or {}).get("blockReason") or cand.get("finishReason") or "no text"
        raise ProviderError(f"Gemini ({name}) gave no answer ({why}).")
    return ("WARNING: the answer was cut short.\n" if cand.get("finishReason") == "MAX_TOKENS" else "") + text


def ask_openai(system: str, user: str, fast: bool = True) -> str:
    key = _key("openai")
    name = model("openai", fast, key)
    body = {"model": name, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        r = _http(f"{OPENAI_URL}/chat/completions", _openai_headers(key), body)
    except ProviderError as e:
        raise ProviderError(_explain("openai", e), e.status) from None
    choice = (r.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    if not text:
        raise ProviderError(f"OpenAI ({name}) gave no answer ({choice.get('finish_reason') or 'no text'}).")
    return ("WARNING: the answer was cut short.\n" if choice.get("finish_reason") == "length" else "") + text


def test(provider: str, key: str) -> tuple[bool, str]:
    """The smallest real request with this key: (works, what happened)."""
    name = model(provider, True, key)
    try:
        if provider == "gemini":
            _http(f"{GEMINI_URL}/models/{name}:generateContent", _gemini_headers(key),
                  {"contents": [{"role": "user", "parts": [{"text": "Reply with OK"}]}],
                   "generationConfig": {"maxOutputTokens": 16}}, timeout=60)
        else:
            _http(f"{OPENAI_URL}/chat/completions", _openai_headers(key),
                  {"model": name, "messages": [{"role": "user", "content": "Reply with OK"}],
                   "max_completion_tokens": 16}, timeout=60)
    except ProviderError as e:
        return False, _explain(provider, e)
    return True, f"The key works - {'Gemini' if provider == 'gemini' else 'OpenAI'} answered ({name})."


# ----------------------------------------------------------------- command-line tools on the laptop

def cli_exe(via: str) -> str | None:
    return shutil.which(CLI[via][0])


def ask_cli(via: str, system: str, user: str, timeout: int = 600, on_start=None) -> str:
    """Codex or Gemini CLI, headless and read-only, in an empty folder: it can only answer with text.
    on_start(process) lets Fix code cancel it."""
    exe = cli_exe(via)
    program, install, sign_in = CLI[via]
    if not exe:
        raise ProviderError(f"{program} is not installed on this computer ({install}).")
    with tempfile.TemporaryDirectory(prefix=f"wishbridge-{via}-", ignore_cleanup_errors=True) as empty:
        out = Path(empty) / "answer.txt"
        if via == "codex":
            cmd = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--output-last-message", str(out), "-"]
        else:
            cmd = [exe, "-p", "Answer the request above."]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", cwd=empty,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if on_start is not None:
            on_start(proc)
        try:
            stdout, stderr = proc.communicate(system + "\n\n" + user, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise ProviderError(f"{program} did not answer within {timeout // 60} minutes - ask again.") from None
        answer = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else stdout
    if proc.returncode != 0 or not (answer or "").strip():
        tail = ((stderr or "") + (stdout or "")).strip()[-400:] or f"exit code {proc.returncode}"
        hint = (" - sign in first: " + sign_in) if re.search(r"log ?in|auth|401|credential|api key", tail, re.I) else ""
        raise ProviderError(f"{program} could not answer: {tail}{hint}")
    return answer
