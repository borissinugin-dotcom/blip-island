"""AI Agents integration: local HTTP server for Claude Code hooks & Codex notify, hook installer, and local token/cost scanner."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from ..core.config import DEFAULT_AGENT_PORT

# Approximate per-1M USD token pricing matching common Claude & OpenAI Codex models
_MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "claude-sonnet-4": (3.0, 15.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-haiku": (0.8, 4.0),
    "gpt-5": (1.25, 10.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4o": (2.5, 10.0),
    "o3": (2.0, 8.0),
    "o4-mini": (1.1, 4.4),
}


@dataclass
class AgentUsageSummary:
    claude_sessions: int = 0
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0
    claude_cost_usd: float = 0.0
    codex_sessions: int = 0
    codex_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_cost_usd: float = 0.0
    today_tokens: int = 0
    models_seen: list[str] = field(default_factory=list)


def _estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    low = model.lower()
    in_p, out_p = (2.5, 10.0)
    for k, prices in _MODEL_PRICING_PER_1M.items():
        if k in low:
            in_p, out_p = prices
            break
    return (in_tok * in_p + out_tok * out_p) / 1_000_000.0


def scan_local_llm_usage(days: int = 7) -> AgentUsageSummary:
    """Scan local Claude Code & Codex CLI JSONL session transcripts for token & cost totals."""
    summary = AgentUsageSummary()
    cutoff = time.time() - days * 86400
    today_date = datetime.now().date()
    models: set[str] = set()

    # 1. Claude Code sessions: ~/.claude/projects/**/*.jsonl
    claude_root = Path.home() / ".claude" / "projects"
    if claude_root.exists():
        for jpath in claude_root.rglob("*.jsonl"):
            try:
                if jpath.stat().st_mtime < cutoff:
                    continue
                summary.claude_sessions += 1
                is_today = datetime.fromtimestamp(jpath.stat().st_mtime).date() == today_date
                with jpath.open("r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if '"usage"' not in line:
                            continue
                        obj = json.loads(line)
                        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
                        usage = msg.get("usage")
                        if not isinstance(usage, dict):
                            continue
                        in_t = int(usage.get("input_tokens", 0) or 0) + int(usage.get("cache_read_input_tokens", 0) or 0) + int(usage.get("cache_creation_input_tokens", 0) or 0)
                        out_t = int(usage.get("output_tokens", 0) or 0)
                        model = str(msg.get("model") or obj.get("model") or "claude-sonnet-4")
                        models.add(model)
                        summary.claude_input_tokens += in_t
                        summary.claude_output_tokens += out_t
                        summary.claude_cost_usd += _estimate_cost(model, in_t, out_t)
                        if is_today:
                            summary.today_tokens += in_t + out_t
            except Exception:
                continue

    # 2. Codex CLI sessions: ~/.codex/sessions/**/*.jsonl
    codex_root = Path.home() / ".codex" / "sessions"
    if codex_root.exists():
        for jpath in codex_root.rglob("*.jsonl"):
            try:
                if jpath.stat().st_mtime < cutoff:
                    continue
                summary.codex_sessions += 1
                is_today = datetime.fromtimestamp(jpath.stat().st_mtime).date() == today_date
                last_total = 0
                cur_model = "gpt-5"
                with jpath.open("r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        obj = json.loads(line)
                        if obj.get("model"):
                            cur_model = str(obj["model"])
                            models.add(cur_model)
                        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
                        info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
                        usage = info.get("total_token_usage") or info.get("last_token_usage") or info.get("usage")
                        if isinstance(usage, dict):
                            in_t = int(usage.get("input_tokens", 0) or 0)
                            out_t = int(usage.get("output_tokens", 0) or 0)
                            tot = in_t + out_t
                            if tot > last_total:
                                d_in = max(0, in_t - summary.codex_input_tokens) if "total_token_usage" in info else in_t
                                summary.codex_input_tokens += in_t if "total_token_usage" not in info else max(0, tot - last_total)
                                if "total_token_usage" not in info:
                                    summary.codex_output_tokens += out_t
                                    summary.codex_cost_usd += _estimate_cost(cur_model, in_t, out_t)
                                else:
                                    summary.codex_cost_usd += _estimate_cost(cur_model, tot - last_total, 0)
                                if is_today:
                                    summary.today_tokens += (tot - last_total) if "total_token_usage" in info else tot
                                last_total = tot
            except Exception:
                continue

    summary.models_seen = sorted(models)[:8]
    return summary


class _PendingPermission:
    def __init__(self, req_id: str, tool_name: str, summary: str, choices: list[str] | None = None) -> None:
        self.req_id = req_id
        self.tool_name = tool_name
        self.summary = summary
        self.choices = choices or ["allow", "deny"]
        self.event = threading.Event()
        self.decision = "deny"
        self.allowed = False


class _PendingGeminiQuery:
    def __init__(self, qid: str, prompt: str, model: str = "gemini-2.5-flash") -> None:
        self.qid = qid
        self.prompt = prompt
        self.model = model
        self.event = threading.Event()
        self.answer = ""
        self.error = ""
        self.created_at = time.monotonic()


class AgentsService(QObject):
    """Local HTTP webhook bridge for Claude Code hooks, Codex notify, OpenCode, and pure-text local Gemini."""

    agent_event = pyqtSignal(str, str, str)  # (agent_name, title, detail)
    permission_requested = pyqtSignal(str, str, str)  # (request_id, tool_name, detail)
    choice_requested = pyqtSignal(str, str, str, list)  # (request_id, title, detail, choices)

    def __init__(self, data_dir: Path, port: int = DEFAULT_AGENT_PORT, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._pending: dict[str, _PendingPermission] = {}
        self._pending_lock = threading.Lock()
        self._gemini_pending: dict[str, _PendingGeminiQuery] = {}
        self._gemini_last_poll = 0.0
        self.recent_events: list[tuple[str, str, str]] = []

    def is_gemini_worker_online(self, max_age_s: float = 20.0) -> bool:
        return (time.monotonic() - self._gemini_last_poll) <= max_age_s

    def submit_gemini_text_query(self, prompt: str, model: str = "gemini-2.5-flash", timeout: float = 40.0) -> str:
        qid = uuid.uuid4().hex[:12]
        q = _PendingGeminiQuery(qid, prompt, model)
        with self._pending_lock:
            self._gemini_pending[qid] = q
        signaled = q.event.wait(timeout=timeout)
        with self._pending_lock:
            self._gemini_pending.pop(qid, None)
        if not signaled:
            raise TimeoutError("Локальный Gemini-клиент не ответил вовремя.")
        if q.error:
            raise RuntimeError(q.error)
        return q.answer.strip()

    def _start_http(self, port: int | None = None) -> bool:
        if port is not None:
            self.port = port
        self.stop()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _send_json(self, code: int, obj: dict) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/health"):
                    self._send_json(200, {"ok": True, "service": "BorhIsland", "port": service.port})
                elif self.path.startswith("/gemini/health"):
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "worker_online": service.is_gemini_worker_online(),
                            "mode": "pure_text_only",
                        },
                    )
                elif self.path.startswith("/gemini/pending"):
                    service._gemini_last_poll = time.monotonic()
                    with service._pending_lock:
                        items = [
                            {"id": q.qid, "prompt": q.prompt, "model": q.model}
                            for q in service._gemini_pending.values()
                            if not q.event.is_set()
                        ]
                    self._send_json(200, {"ok": True, "queries": items})
                elif self.path.startswith("/usage"):
                    u = scan_local_llm_usage()
                    self._send_json(200, u.__dict__)
                else:
                    self._send_json(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(min(length, 2_000_000))
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
                except Exception:
                    payload = {}

                if self.path.startswith("/gemini/reply"):
                    service._gemini_last_poll = time.monotonic()
                    qid = str(payload.get("id") or "")
                    ans = str(payload.get("answer") or payload.get("text") or "")
                    err = str(payload.get("error") or "")
                    with service._pending_lock:
                        q = service._gemini_pending.get(qid)
                        if q is not None:
                            q.answer = ans
                            q.error = err
                            q.event.set()
                            self._send_json(200, {"ok": True})
                            return
                    self._send_json(404, {"ok": False, "error": "query_not_found"})
                    return

                if self.path.startswith("/gemini/ask"):
                    prompt = str(payload.get("prompt") or "").strip()
                    model = str(payload.get("model") or "gemini-2.5-flash")
                    if not prompt:
                        self._send_json(400, {"ok": False, "error": "empty_prompt"})
                        return
                    if not service.is_gemini_worker_online():
                        self._send_json(503, {"ok": False, "error": "gemini_worker_offline"})
                        return
                    try:
                        ans = service.submit_gemini_text_query(prompt, model=model, timeout=38.0)
                        self._send_json(200, {"ok": True, "answer": ans})
                    except Exception as exc:
                        self._send_json(504, {"ok": False, "error": str(exc)})
                    return

                if "/codex" in self.path:
                    msg = str(payload.get("last-assistant-message") or payload.get("message") or payload.get("type") or "Codex finished")
                    service._record_event("Codex CLI", "Codex Notification", msg[:240])
                    self._send_json(200, {"ok": True})
                    return

                if "/opencode" in self.path:
                    ev = str(payload.get("event") or payload.get("type") or "session.idle")
                    msg = str(payload.get("message") or payload.get("detail") or ev)
                    service._record_event("OpenCode", f"OpenCode: {ev}", msg[:220])
                    self._send_json(200, {"ok": True})
                    return

                # Claude Code hook payload
                event_name = str(payload.get("hook_event_name") or payload.get("event") or "Notification")
                tool_name = str(payload.get("tool_name") or "")
                tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}

                if event_name in ("PreToolUse", "PermissionRequest"):
                    raw_choices = payload.get("choices")
                    options_list: list[str] = []
                    if isinstance(raw_choices, list) and raw_choices:
                        options_list = [str(x) for x in raw_choices[:4]]
                    elif isinstance(tool_input.get("questions"), list) and tool_input["questions"]:
                        q0 = tool_input["questions"][0]
                        if isinstance(q0, dict) and isinstance(q0.get("options"), list):
                            options_list = [str(o.get("label") or o) for o in q0["options"][:4] if o]

                    detail = ""
                    if "command" in tool_input:
                        detail = str(tool_input["command"])
                    elif "file_path" in tool_input:
                        detail = str(tool_input["file_path"])
                    elif options_list:
                        detail = str(tool_input["questions"][0].get("question") or "")
                    elif tool_input:
                        detail = json.dumps(tool_input, ensure_ascii=False)[:180]
                    req_id = uuid.uuid4().hex[:10]
                    choices = options_list or ["allow", "allow_session", "deny"]
                    perm = _PendingPermission(req_id, tool_name or "Tool", detail or event_name, choices=choices)
                    with service._pending_lock:
                        service._pending[req_id] = perm
                    service._record_event("Claude Code", f"Allow {perm.tool_name}?", perm.summary[:200])
                    if options_list:
                        service.choice_requested.emit(req_id, perm.tool_name, perm.summary, options_list)
                    else:
                        service.permission_requested.emit(req_id, perm.tool_name, perm.summary)

                    # Block up to 110 seconds waiting for user click on Island live card
                    signaled = perm.event.wait(timeout=110.0)
                    with service._pending_lock:
                        service._pending.pop(req_id, None)

                    dec_raw = perm.decision if signaled else "ask"
                    decision = "allow" if dec_raw in ("allow", "allow_session") or dec_raw in options_list else ("deny" if signaled else "ask")
                    self._send_json(
                        200,
                        {
                            "hookSpecificOutput": {
                                "hookEventName": event_name,
                                "permissionDecision": decision,
                                "selectedChoice": dec_raw if dec_raw in options_list else "",
                                "permissionDecisionReason": f"BorhIsland user decision: {dec_raw}",
                            }
                        },
                    )
                    return

                msg = str(payload.get("message") or payload.get("title") or event_name)
                if tool_name:
                    msg = f"{tool_name}: {msg}"
                service._record_event("Claude Code", f"Claude: {event_name}", msg[:220])
                self._send_json(200, {"ok": True})

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            return True
        except OSError:
            self._server = None
            return False

    def stop(self) -> None:
        if hasattr(self, "_oc_timer") and self._oc_timer is not None:
            try:
                self._oc_timer.stop()
            except Exception:
                pass
        with self._pending_lock:
            for perm in self._pending.values():
                perm.allowed = False
                perm.event.set()
            self._pending.clear()
            for q in self._gemini_pending.values():
                q.error = "stopped"
                q.event.set()
            self._gemini_pending.clear()
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    def _record_event(self, agent: str, title: str, detail: str) -> None:
        self.recent_events.insert(0, (agent, title, detail))
        if len(self.recent_events) > 30:
            self.recent_events = self.recent_events[:30]
        self.agent_event.emit(agent, title, detail)

    def resolve_permission(self, request_id: str, allow: bool | str) -> bool:
        with self._pending_lock:
            perm = self._pending.get(request_id)
            if perm is None:
                return False
            if isinstance(allow, str):
                perm.decision = allow
                perm.allowed = allow != "deny"
            else:
                perm.allowed = bool(allow)
                perm.decision = "allow" if allow else "deny"
            perm.event.set()
            return True

    def start(self, port: int | None = None) -> bool:
        ok = self._start_http(port)
        if not hasattr(self, "_oc_timer") or self._oc_timer is None:
            from PyQt6.QtCore import QTimer

            self._oc_seen: dict[str, float] = {}
            self._oc_timer = QTimer(self)
            self._oc_timer.setInterval(4500)
            self._oc_timer.timeout.connect(self._poll_opencode_sessions)
            self._oc_timer.start()
        return ok

    def _poll_opencode_sessions(self) -> None:
        if getattr(self, "_oc_busy", False):
            return
        roots = [
            Path.home() / ".local" / "share" / "opencode" / "storage" / "session",
            Path.home() / "AppData" / "Local" / "opencode" / "storage" / "session",
        ]
        existing = [r for r in roots if r.exists()]
        if not existing:
            return
        self._oc_busy = True

        def _work() -> None:
            try:
                now = time.time()
                for r in existing:
                    try:
                        for fp in r.rglob("*.json"):
                            st = fp.stat().st_mtime
                            prev = self._oc_seen.get(str(fp))
                            self._oc_seen[str(fp)] = st
                            if prev is not None and st > prev and (now - st) < 30.0:
                                self._record_event("OpenCode", "OpenCode Activity", fp.stem[:60])
                    except Exception:
                        continue
            finally:
                self._oc_busy = False

        threading.Thread(target=_work, daemon=True).start()

    # ------------------------------------------------------------------ Hook Installers (with .bak backup)
    def _ensure_hook_sender_script(self) -> Path:
        script_path = self.data_dir / "send_agent_hook.py"
        code = (
            "import sys, urllib.request\n"
            "url = sys.argv[1]\n"
            "data = (sys.argv[2].encode('utf-8') if len(sys.argv) > 2 else sys.stdin.buffer.read())\n"
            "req = urllib.request.Request(url, data=data or b'{}', headers={'Content-Type': 'application/json'})\n"
            "try:\n"
            "    with urllib.request.urlopen(req, timeout=115) as r:\n"
            "        sys.stdout.buffer.write(r.read())\n"
            "except Exception:\n"
            "    sys.stdout.write('{}')\n"
        )
        script_path.write_text(code, encoding="utf-8")
        return script_path

    def install_claude_hooks(self, home_dir: Path | None = None) -> Path:
        base = (home_dir or Path.home()) / ".claude"
        base.mkdir(parents=True, exist_ok=True)
        settings_path = base / "settings.json"
        if settings_path.exists():
            shutil.copy2(settings_path, base / "settings.json.bak")
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}

        sender = self._ensure_hook_sender_script()
        py_exe = shutil.which("python") or os.sys.executable
        cmd_str = f'"{py_exe}" "{sender}" "http://127.0.0.1:{self.port}/hook/claude"'

        hooks = data.setdefault("hooks", {})
        for ev_name in ("PreToolUse", "Notification", "Stop"):
            entries = [e for e in hooks.get(ev_name, []) if "send_agent_hook.py" not in json.dumps(e)]
            entries.append({"matcher": "*", "hooks": [{"type": "command", "command": cmd_str, "timeout": 120}]})
            hooks[ev_name] = entries

        settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return settings_path

    def uninstall_claude_hooks(self, home_dir: Path | None = None) -> bool:
        settings_path = (home_dir or Path.home()) / ".claude" / "settings.json"
        if not settings_path.exists():
            return False
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        for ev_name in list(hooks.keys()):
            hooks[ev_name] = [e for e in hooks[ev_name] if "send_agent_hook.py" not in json.dumps(e)]
        settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def install_codex_notify(self, home_dir: Path | None = None) -> Path:
        base = (home_dir or Path.home()) / ".codex"
        base.mkdir(parents=True, exist_ok=True)
        cfg_path = base / "config.toml"
        existing = ""
        if cfg_path.exists():
            shutil.copy2(cfg_path, base / "config.toml.bak")
            existing = cfg_path.read_text(encoding="utf-8", errors="ignore")

        sender = self._ensure_hook_sender_script()
        py_exe = (shutil.which("python") or os.sys.executable).replace("\\", "/")
        sender_s = str(sender).replace("\\", "/")
        notify_line = f'notify = ["{py_exe}", "{sender_s}", "http://127.0.0.1:{self.port}/hook/codex"]'

        lines = [ln for ln in existing.splitlines() if not ln.strip().startswith("notify =")]
        lines.insert(0, notify_line)
        cfg_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return cfg_path
