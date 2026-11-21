"""Command Bar (Alt+Space) engine: apps + UWP + files index, AST math, units, CBR currency, shell, system."""

from __future__ import annotations

import ast
import math
import operator
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from .reminder_parser import ParsedReminder, parse_duration_seconds, parse_reminder_phrase

CREATE_NO_WINDOW = 0x08000000


@dataclass
class IndexedItem:
    title: str
    subtitle: str
    target: str
    kind: str  # app | uwp | file | calc | unit | currency | shell | system | timer | reminder | web


# Safe AST evaluator for calculator expressions
_AST_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitXor: operator.pow,
}
_AST_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MATH_FUNCS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log10,
    "ln": math.log,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
}
_MATH_CONSTS = {"pi": math.pi, "e": math.e}


def safe_eval_math(expr: str) -> float | None:
    """Safely evaluate math expressions with AST whitelist (supports ^, %, pi, sqrt, etc.)."""
    s = expr.strip().lstrip("=").replace(",", ".").replace("×", "*").replace("÷", "/").replace("^", "**")
    if not s or not re.search(r"[\d]", s):
        return None
    # Percent suffix like "200 * 15%" -> "200 * (15/100)"
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s):
        return None  # Plain single number isn't a calculation

    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError:
        return None

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            low = node.id.lower()
            if low in _MATH_CONSTS:
                return _MATH_CONSTS[low]
            raise ValueError("unknown identifier")
        if isinstance(node, ast.BinOp) and type(node.op) in _AST_OPS:
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 1000:
                raise ValueError("exponent too large")
            return float(_AST_OPS[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _AST_UNARY:
            return float(_AST_UNARY[type(node.op)](_eval(node.operand)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fname = node.func.id.lower()
            if fname in _MATH_FUNCS and len(node.args) == 1 and not node.keywords:
                return float(_MATH_FUNCS[fname](_eval(node.args[0])))
        raise ValueError("disallowed expression")

    try:
        res = _eval(tree)
        if math.isfinite(res):
            return res
    except Exception:
        return None
    return None


# Unit conversion table -> (category, factor_to_base)
_UNIT_FACTORS: dict[str, tuple[str, float]] = {
    # length -> meter
    "mm": ("len", 0.001), "мм": ("len", 0.001),
    "cm": ("len", 0.01), "см": ("len", 0.01),
    "m": ("len", 1.0), "м": ("len", 1.0),
    "km": ("len", 1000.0), "км": ("len", 1000.0),
    "in": ("len", 0.0254), "дюйм": ("len", 0.0254),
    "ft": ("len", 0.3048), "фут": ("len", 0.3048),
    "mi": ("len", 1609.344), "mile": ("len", 1609.344), "miles": ("len", 1609.344), "миль": ("len", 1609.344),
    # weight -> gram
    "mg": ("wt", 0.001), "мг": ("wt", 0.001),
    "g": ("wt", 1.0), "г": ("wt", 1.0),
    "kg": ("wt", 1000.0), "кг": ("wt", 1000.0),
    "lb": ("wt", 453.59237), "lbs": ("wt", 453.59237), "фунт": ("wt", 453.59237),
    "oz": ("wt", 28.349523), "унц": ("wt", 28.349523),
    # data -> byte
    "b": ("data", 1.0), "б": ("data", 1.0),
    "kb": ("data", 1024.0), "кб": ("data", 1024.0),
    "mb": ("data", 1024.0 ** 2), "мб": ("data", 1024.0 ** 2),
    "gb": ("data", 1024.0 ** 3), "гб": ("data", 1024.0 ** 3),
    "tb": ("data", 1024.0 ** 4), "тб": ("data", 1024.0 ** 4),
    # speed -> m/s
    "m/s": ("spd", 1.0), "м/с": ("spd", 1.0),
    "km/h": ("spd", 1.0 / 3.6), "kmh": ("spd", 1.0 / 3.6), "км/ч": ("spd", 1.0 / 3.6),
    "mph": ("spd", 0.44704),
}

_TEMP_ALIASES = {"c": "C", "°c": "C", "f": "F", "°f": "F", "k": "K"}
_CURRENCY_ALIASES = {
    "rub": "RUB", "руб": "RUB", "₽": "RUB",
    "usd": "USD", "$": "USD", "дол": "USD",
    "eur": "EUR", "€": "EUR", "евро": "EUR",
    "gbp": "GBP", "£": "GBP",
    "cny": "CNY", "¥": "CNY", "юан": "CNY",
    "kzt": "KZT", "тенге": "KZT",
    "byn": "BYN", "try": "TRY", "jpy": "JPY", "chf": "CHF", "uah": "UAH", "aed": "AED",
}

_CONV_RE = re.compile(
    r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*([^\d\s]+)\s+(?:to|in|в|->)\s+([^\d\s]+)\s*$",
    re.IGNORECASE,
)


def convert_units(expr: str) -> tuple[str, str] | None:
    """Try converting units like '10 km to mi' or '25 c in f'. Returns (formatted_result, value_str)."""
    m = _CONV_RE.match(expr)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    u1 = m.group(2).lower()
    u2 = m.group(3).lower()

    if u1 in _TEMP_ALIASES and u2 in _TEMP_ALIASES:
        t1, t2 = _TEMP_ALIASES[u1], _TEMP_ALIASES[u2]
        c = val if t1 == "C" else ((val - 32.0) * 5.0 / 9.0 if t1 == "F" else val - 273.15)
        out = c if t2 == "C" else (c * 9.0 / 5.0 + 32.0 if t2 == "F" else c + 273.15)
        res_s = f"{out:.2f}".rstrip("0").rstrip(".")
        return f"{val:g} °{t1} = {res_s} °{t2}", res_s

    if u1 in _UNIT_FACTORS and u2 in _UNIT_FACTORS:
        c1, f1 = _UNIT_FACTORS[u1]
        c2, f2 = _UNIT_FACTORS[u2]
        if c1 == c2:
            out = (val * f1) / f2
            res_s = f"{out:.4f}".rstrip("0").rstrip(".")
            return f"{val:g} {u1} = {res_s} {u2}", res_s
    return None


class CurrencyRatesProvider:
    """Fetches Central Bank of Russia daily rates with in-memory cache and static fallback."""

    def __init__(self) -> None:
        self._rates_to_rub: dict[str, float] = {
            "RUB": 1.0,
            "USD": 95.0,
            "EUR": 103.0,
            "CNY": 13.2,
            "GBP": 120.0,
            "KZT": 0.19,
            "BYN": 29.0,
            "TRY": 2.8,
            "JPY": 0.62,
            "CHF": 108.0,
            "UAH": 2.3,
            "AED": 25.8,
        }
        self._fetched_at = 0.0

    def refresh_if_needed(self) -> None:
        if time.time() - self._fetched_at < 3600 * 6:
            return
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get("https://www.cbr-xml-daily.ru/daily_json.js")
                r.raise_for_status()
                valute = r.json().get("Valute", {})
                new_rates = {"RUB": 1.0}
                for code, entry in valute.items():
                    nom = float(entry.get("Nominal", 1) or 1)
                    val = float(entry.get("Value", 0) or 0)
                    if val > 0:
                        new_rates[code.upper()] = val / nom
                self._rates_to_rub.update(new_rates)
                self._fetched_at = time.time()
        except Exception:
            self._fetched_at = time.time() - 3600 * 5

    def convert(self, expr: str) -> tuple[str, str] | None:
        m = _CONV_RE.match(expr)
        if not m:
            # Also support shorthand like "100 usd" -> RUB
            m2 = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*([A-Za-z$€₽£¥А-Яа-я]{1,6})\s*$", expr)
            if not m2:
                return None
            src_raw = m2.group(2).lower()
            c1 = _CURRENCY_ALIASES.get(src_raw) or (src_raw.upper() if src_raw.upper() in self._rates_to_rub else None)
            if not c1 or c1 == "RUB":
                return None
            val = float(m2.group(1).replace(",", "."))
            c2 = "RUB"
        else:
            val = float(m.group(1).replace(",", "."))
            s1, s2 = m.group(2).lower(), m.group(3).lower()
            c1 = _CURRENCY_ALIASES.get(s1) or (s1.upper() if s1.upper() in self._rates_to_rub else None)
            c2 = _CURRENCY_ALIASES.get(s2) or (s2.upper() if s2.upper() in self._rates_to_rub else None)
        if not c1 or not c2:
            return None
        self.refresh_if_needed()
        if c1 not in self._rates_to_rub or c2 not in self._rates_to_rub:
            return None
        rub_val = val * self._rates_to_rub[c1]
        out = rub_val / self._rates_to_rub[c2]
        res_s = f"{out:.2f}"
        return f"{val:g} {c1} = {res_s} {c2}", res_s


class CommandService:
    """Indexes Start Menu apps, UWP apps, and Recent files; parses smart command queries."""

    def __init__(self) -> None:
        self._items: list[IndexedItem] = []
        self._indexed = False
        self.currency = CurrencyRatesProvider()

    def ensure_indexed(self, force: bool = False) -> None:
        if self._indexed and not force:
            return
        self._indexed = True
        items: list[IndexedItem] = []

        # 1. Start Menu & Desktop shortcuts
        search_roots = [
            Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path.home() / "Desktop",
        ]
        seen_names: set[str] = set()
        for root in search_roots:
            if not root.exists():
                continue
            for p in root.rglob("*.lnk"):
                stem = p.stem.strip()
                low = stem.lower()
                if not stem or low in seen_names or "uninstall" in low or "удалить" in low:
                    continue
                seen_names.add(low)
                items.append(IndexedItem(title=stem, subtitle=str(root.name), target=str(p), kind="app"))

        # 2. UWP / Store Apps via shell:AppsFolder
        try:
            res = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=6,
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode == 0 and res.stdout.strip():
                import json

                apps = json.loads(res.stdout)
                if isinstance(apps, dict):
                    apps = [apps]
                for entry in apps:
                    name = str(entry.get("Name", "")).strip()
                    appid = str(entry.get("AppID", "")).strip()
                    if name and appid and name.lower() not in seen_names:
                        seen_names.add(name.lower())
                        items.append(
                            IndexedItem(
                                title=name,
                                subtitle="Microsoft Store / UWP",
                                target=f"shell:AppsFolder\\{appid}",
                                kind="uwp",
                            )
                        )
        except Exception:
            pass

        # 3. Windows Recent files
        recent_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
        if recent_dir.exists():
            recent_files = sorted(
                (p for p in recent_dir.glob("*.lnk") if p.is_file()),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )[:30]
            for rf in recent_files:
                name = rf.stem.strip()
                if name:
                    items.append(IndexedItem(title=name, subtitle="Recent File", target=str(rf), kind="file"))

        self._items = items

    def search(self, query: str) -> list[IndexedItem]:
        """Return prioritized command bar results for query."""
        q = query.strip()
        results: list[IndexedItem] = []

        if not q:
            return self._default_suggestions()

        # Shell prefix `> command`
        if q.startswith(">"):
            cmd = q[1:].strip()
            results.append(
                IndexedItem(
                    title=f"Run in PowerShell: {cmd}" if cmd else "Type shell command after '>'",
                    subtitle="Enter: execute and show output",
                    target=cmd,
                    kind="shell",
                )
            )
            return results

        # Timer keyword
        timer_m = re.match(r"^(?:timer|таймер)\s+(.+)$", q, re.IGNORECASE)
        if timer_m:
            secs = parse_duration_seconds(timer_m.group(1))
            if secs:
                results.append(
                    IndexedItem(
                        title=f"Start timer: {secs // 60}m {secs % 60}s" if secs >= 60 else f"Start timer: {secs}s",
                        subtitle="Dynamic Island Timer",
                        target=str(secs),
                        kind="timer",
                    )
                )

        # Reminder keyword or natural phrase
        rem_m = re.match(r"^(?:remind(?:er)?|напомни(?:ть)?)\s+(.+)$", q, re.IGNORECASE)
        if rem_m:
            parsed: ParsedReminder | None = parse_reminder_phrase(rem_m.group(1))
            if parsed:
                results.append(
                    IndexedItem(
                        title=f"Reminder: {parsed.title}",
                        subtitle=parsed.due_at.strftime("%Y-%m-%d %H:%M") + (f" ({parsed.repeat_rule})" if parsed.repeat_rule != "none" else ""),
                        target=rem_m.group(1),
                        kind="reminder",
                    )
                )

        # Unit conversion
        unit_conv = convert_units(q)
        if unit_conv:
            results.append(IndexedItem(title=unit_conv[0], subtitle="Enter: copy result", target=unit_conv[1], kind="unit"))

        # Currency conversion
        curr_conv = self.currency.convert(q)
        if curr_conv:
            results.append(IndexedItem(title=curr_conv[0], subtitle="CBR Exchange Rate — Enter: copy", target=curr_conv[1], kind="currency"))

        # Math expression
        math_val = safe_eval_math(q)
        if math_val is not None:
            formatted = f"{math_val:.6f}".rstrip("0").rstrip(".") if abs(math_val) < 1e12 else f"{math_val:g}"
            results.append(IndexedItem(title=f"= {formatted}", subtitle="Calculator — Enter: copy", target=formatted, kind="calc"))

        # System quick commands
        ql = q.lower()
        sys_cmds = [
            ("lock screen / заблокировать", "Lock Windows", "lock"),
            ("sleep / сон / спящий режим", "Put PC to Sleep", "sleep"),
            ("restart / перезагрузка", "Restart Windows", "restart"),
            ("shutdown / выключить", "Shut Down PC", "shutdown"),
            ("theme dark light / тема / светлая / тёмная", "Toggle Windows Dark/Light Theme", "theme"),
            ("settings / настройки / parameters", "Open Island Settings", "settings"),
            ("empty recycle bin / очистить корзину", "Empty Recycle Bin", "empty_bin"),
            ("mute mic / микрофон", "Toggle Microphone Mute", "mute_mic"),
        ]
        for aliases, title, cmd_id in sys_cmds:
            if any(tok in aliases for tok in ql.split() if len(tok) >= 2):
                results.append(IndexedItem(title=title, subtitle="System Command", target=cmd_id, kind="system"))

        # Indexed apps & recent files
        self.ensure_indexed()
        scored: list[tuple[int, IndexedItem]] = []
        for item in self._items:
            tlow = item.title.lower()
            if ql == tlow:
                scored.append((100, item))
            elif tlow.startswith(ql):
                scored.append((80, item))
            elif ql in tlow:
                scored.append((50, item))
        scored.sort(key=lambda x: (-x[0], len(x[1].title)))
        results.extend(item for _, item in scored[:12])

        # Always offer Web Search at the end
        results.append(IndexedItem(title=f"Search Web: {q}", subtitle="Open in default browser", target=q, kind="web"))
        return results

    def _default_suggestions(self) -> list[IndexedItem]:
        self.ensure_indexed()
        top_apps = [i for i in self._items if i.kind in ("app", "uwp")][:6]
        return [
            IndexedItem(title="> ipconfig", subtitle="Prefix '>' runs cmd / PowerShell", target="ipconfig", kind="shell"),
            IndexedItem(title="100 usd to rub", subtitle="Math, units & CBR currency converter", target="", kind="currency"),
            *top_apps,
        ]

    @staticmethod
    def launch_target(target: str, run_as_admin: bool = False) -> None:
        if target.startswith("shell:AppsFolder\\"):
            subprocess.Popen(["explorer.exe", target])
            return
        if run_as_admin:
            ps_t = target.replace("'", "''")
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", f"Start-Process -FilePath '{ps_t}' -Verb RunAs"],
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            os.startfile(target)  # type: ignore[attr-defined]

    @staticmethod
    def run_shell_command(cmd: str) -> str:
        """Execute shell command and return combined output."""
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; {cmd}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        out = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        return out.strip() or f"(exit code {res.returncode})"
