"""Multi-provider AI chat client, file/image attachment parser, Whisper/Gemini STT, and image uploader."""

from __future__ import annotations

import base64
import io
import math
import os
import re
import shutil
import struct
import subprocess
import wave
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Iterable

import httpx
from PIL import Image
from PyQt6.QtCore import QIODevice, QObject, QTimer, pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from ..core.config import AI_PROVIDER_PRESETS, AppConfig


def encode_image_attachment(image_path: Path, max_dim: int = 1600) -> tuple[str, str]:
    """Load, downscale if large, and return (mime_type, base64_data)."""
    with Image.open(image_path) as img:
        if max(img.width, img.height) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img.convert("RGBA").save(buf, format="PNG", optimize=True)
            mime = "image/png"
        else:
            img.convert("RGB").save(buf, format="JPEG", quality=88)
            mime = "image/jpeg"
        return mime, base64.b64encode(buf.getvalue()).decode("ascii")


def _extract_pdf_text_fallback(raw: bytes) -> str:
    """Lightweight dependency-free extractor for uncompressed & FlateDecode PDF text operators."""
    chunks: list[str] = []
    # Decompress FlateDecode streams if possible, plus scan raw bytes
    streams: list[bytes] = [raw]
    for m in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", raw, re.DOTALL):
        s_bytes = m.group(1)
        try:
            streams.append(zlib.decompress(s_bytes))
        except Exception:
            streams.append(s_bytes)
    for st in streams:
        for txt_m in re.finditer(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*(?:Tj|'|\"|TJ)", st):
            part = txt_m.group(1).replace(rb"\(", b"(").replace(rb"\)", b")").replace(rb"\\", b"\\")
            decoded = part.decode("utf-8", errors="ignore") or part.decode("latin-1", errors="ignore")
            if decoded.strip():
                chunks.append(decoded)
    return " ".join(chunks)[:24000]


def extract_document_text(path: Path, max_chars: int = 24000) -> str:
    """Extract readable text from txt/code, docx, xlsx, pptx, or pdf attachments."""
    ext = path.suffix.lower()
    if ext in {".docx", ".xlsx", ".pptx"}:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                texts: list[str] = []
                names = sorted(zf.namelist())
                if ext == ".docx":
                    targets = [n for n in names if n == "word/document.xml"]
                elif ext == ".xlsx":
                    targets = [n for n in names if n == "xl/sharedStrings.xml" or n.startswith("xl/worksheets/sheet")]
                else:
                    targets = [n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
                for tname in targets:
                    root = ET.fromstring(zf.read(tname))
                    for elem in root.iter():
                        if elem.text and elem.text.strip():
                            texts.append(elem.text.strip())
                return "\n".join(texts)[:max_chars]
        except Exception as exc:
            return f"[Could not parse {path.name}: {exc}]"

    if ext == ".pdf":
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)[:max_chars]
        except Exception:
            raw = path.read_bytes()
            txt = _extract_pdf_text_fallback(raw)
            return txt or f"[PDF binary {path.name}: {len(raw)} bytes]"

    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception as exc:
        return f"[Could not read {path.name}: {exc}]"


class AIService:
    """Unified chat completion client across OpenAI-compat, Google Gemini, Anthropic, and Ollama."""

    @staticmethod
    def _resolve_key(cfg: AppConfig, provider: str) -> str:
        key = cfg.get_secret(provider) or cfg.get_secret(f"ai_{provider}")
        if not key and provider in ("gemini", "gemini_local"):
            key = cfg.get_secret("gemini") or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        return key.strip()

    @staticmethod
    def find_gemini_cli() -> str:
        """Locate locally installed Google Gemini CLI executable on PATH or npm global bin."""
        for cand in ("gemini", "gemini.cmd", "gemini.exe"):
            hit = shutil.which(cand)
            if hit:
                return hit
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            p = Path(appdata) / "npm" / "gemini.cmd"
            if p.is_file():
                return str(p)
        return ""

    def send_chat(
        self,
        cfg: AppConfig,
        history: list[dict[str, str]],
        user_text: str,
        attachments: list[str] | None = None,
        system_prompt: str | None = None,
        *,
        pure_text_only: bool = False,
        provider_override: str | None = None,
    ) -> str:
        """Send multi-turn chat request; when pure_text_only=True operates strictly in text Q&A without tools or files."""
        provider = provider_override or cfg.ai_provider
        preset = AI_PROVIDER_PRESETS.get(provider, AI_PROVIDER_PRESETS["custom"])
        kind = preset.get("kind", "openai")
        if provider_override and provider_override != cfg.ai_provider:
            if provider == "gemini_local" and cfg.ai_base_url and ("127.0.0.1" in cfg.ai_base_url or "localhost" in cfg.ai_base_url):
                base_url = cfg.ai_base_url.rstrip("/")
            else:
                base_url = preset.get("base_url", "").rstrip("/")
            model = preset.get("default_model", "").strip()
        else:
            base_url = (cfg.ai_base_url or preset.get("base_url", "")).rstrip("/")
            model = (cfg.ai_model or preset.get("default_model", "")).strip()
        api_key = self._resolve_key(cfg, provider)

        if provider not in ("ollama", "gemini_local") and not api_key:
            raise ValueError(f"API key for provider '{preset.get('name', provider)}' is not configured.")

        if pure_text_only:
            sys_text = system_prompt or (
                "You are Gemini integrated inside Dynamic Island Borh-edition in PURE TEXT Q&A MODE. "
                "Respond directly with plain text answers to user questions. "
                "Do not invoke functions, tools, filesystem edits, or external actions."
            )
        else:
            sys_text = system_prompt or (
                "You are Dynamic Island Borh-edition AI Assistant on Windows. "
                "Respond clearly, accurately, and concisely in the user's language."
            )

        # Build attachment parts only when NOT in pure-text mode
        image_parts: list[tuple[str, str]] = []
        doc_snippets: list[str] = []
        if not pure_text_only:
            for att in attachments or []:
                p = Path(att)
                if not p.exists():
                    continue
                if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
                    image_parts.append(encode_image_attachment(p))
                else:
                    doc_text = extract_document_text(p)
                    doc_snippets.append(f"[Attached file: {p.name}]\n{doc_text}")

        combined_user_text = user_text
        if doc_snippets:
            combined_user_text = (user_text + "\n\n" + "\n\n".join(doc_snippets)).strip()

        if kind == "gemini_local":
            return self._call_gemini_local(base_url, api_key, model, sys_text, history, combined_user_text)
        if kind == "gemini":
            return self._call_gemini(base_url, api_key, model, sys_text, history, combined_user_text, image_parts)
        if kind == "anthropic":
            return self._call_anthropic(base_url, api_key, model, sys_text, history, combined_user_text, image_parts)
        return self._call_openai_compat(base_url, api_key, model, sys_text, history, combined_user_text, image_parts)

    def _call_gemini_local(
        self,
        base_url: str,
        api_key: str,
        model: str,
        sys_text: str,
        history: list[dict[str, str]],
        user_text: str,
    ) -> str:
        """Connect pure-text query to local Gemini CLI, local companion bridge, localhost HTTP gateway, or Gemini REST."""
        prompt_parts = [f"System: {sys_text}"]
        for turn in history[-8:]:
            role = "User" if turn.get("role") == "user" else "Gemini"
            if turn.get("content"):
                prompt_parts.append(f"{role}: {turn['content']}")
        prompt_parts.append(f"User: {user_text}")
        flat_prompt = "\n\n".join(prompt_parts)

        # 1) Custom local OpenAI-compatible gateway URL (e.g. local proxy / Ollama / LM Studio running Gemini)
        if base_url and ("127.0.0.1" in base_url or "localhost" in base_url):
            if base_url.rstrip("/").endswith("/v1") or "/v1" in base_url:
                return self._call_openai_compat(base_url, api_key, model, sys_text, history, user_text, [])
            # Check built-in BorhIsland local Gemini companion bridge (/gemini/health -> /gemini/ask)
            try:
                with httpx.Client(timeout=2.0) as client:
                    hr = client.get(f"{base_url.rstrip('/')}/gemini/health")
                    if hr.status_code == 200 and hr.json().get("worker_online"):
                        ar = client.post(
                            f"{base_url.rstrip('/')}/gemini/ask",
                            json={"prompt": flat_prompt, "model": model},
                            timeout=40.0,
                        )
                        if ar.status_code == 200 and ar.json().get("ok"):
                            return str(ar.json().get("answer", "")).strip()
            except Exception:
                pass

        # 2) Local Gemini CLI installed on the user's computer (pure headless text-only prompt)
        cli = self.find_gemini_cli()
        if cli:
            res = subprocess.run(
                [cli, "-p", flat_prompt],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                creationflags=0x08000000,
            )
            out = (res.stdout or "").strip()
            if res.returncode == 0 and out:
                return out
            err = (res.stderr or "").strip()
            if err:
                raise RuntimeError(f"Gemini CLI: {err[:300]}")

        # 3) Fallback to Google Gemini REST API if GEMINI_API_KEY / GOOGLE_API_KEY / stored key is set
        if api_key:
            return self._call_gemini(
                AI_PROVIDER_PRESETS["gemini"]["base_url"],
                api_key,
                model or "gemini-2.5-flash",
                sys_text,
                history,
                user_text,
                [],
            )

        raise RuntimeError(
            "Локальный Gemini не найден. Установите Gemini CLI (`gemini`), запустите локальный клиент-мост на "
            "`http://127.0.0.1:17483/gemini/pending`, либо укажите GEMINI_API_KEY / ключ в Настройки → AI."
        )

    def _call_openai_compat(
        self,
        base_url: str,
        api_key: str,
        model: str,
        sys_text: str,
        history: list[dict[str, str]],
        user_text: str,
        images: list[tuple[str, str]],
    ) -> str:
        messages: list[dict] = [{"role": "system", "content": sys_text}]
        for turn in history[-14:]:
            role = turn.get("role", "user")
            if role in ("user", "assistant") and turn.get("content"):
                messages.append({"role": role, "content": turn["content"]})

        if images:
            content: list[dict] = [{"type": "text", "text": user_text or "Analyze the attached image(s)."}]
            for mime, b64 in images:
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_text})

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(url, headers=headers, json={"model": model, "messages": messages, "temperature": 0.4})
            if resp.status_code >= 400:
                raise RuntimeError(f"AI HTTP {resp.status_code}: {resp.text[:400]}")
            data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Unexpected AI response: {data}")
        return str(choices[0].get("message", {}).get("content", "")).strip()

    def _call_gemini(
        self,
        base_url: str,
        api_key: str,
        model: str,
        sys_text: str,
        history: list[dict[str, str]],
        user_text: str,
        images: list[tuple[str, str]],
    ) -> str:
        contents: list[dict] = []
        for turn in history[-14:]:
            role = "model" if turn.get("role") == "assistant" else "user"
            if turn.get("content"):
                contents.append({"role": role, "parts": [{"text": turn["content"]}]})
        user_parts: list[dict] = [{"text": user_text or "Analyze the attached image(s)."}]
        for mime, b64 in images:
            user_parts.append({"inline_data": {"mime_type": mime, "data": b64}})
        contents.append({"role": "user", "parts": user_parts})

        clean_model = model if model.startswith("models/") else f"models/{model}"
        url = f"{base_url}/{clean_model}:generateContent"
        payload = {
            "system_instruction": {"parts": [{"text": sys_text}]},
            "contents": contents,
        }
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(url, params={"key": api_key}, json=payload)
            if resp.status_code >= 400:
                raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:400]}")
            data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            raise RuntimeError(f"Unexpected Gemini response: {data}")
        parts = cands[0].get("content", {}).get("parts", [])
        return "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()

    def _call_anthropic(
        self,
        base_url: str,
        api_key: str,
        model: str,
        sys_text: str,
        history: list[dict[str, str]],
        user_text: str,
        images: list[tuple[str, str]],
    ) -> str:
        messages: list[dict] = []
        for turn in history[-14:]:
            role = turn.get("role", "user")
            if role in ("user", "assistant") and turn.get("content"):
                messages.append({"role": role, "content": turn["content"]})
        content: list[dict] = []
        for mime, b64 in images:
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
        content.append({"type": "text", "text": user_text or "Analyze the attached image(s)."})
        messages.append({"role": "user", "content": content})

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(
                f"{base_url}/messages",
                headers=headers,
                json={"model": model, "max_tokens": 2048, "system": sys_text, "messages": messages},
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Anthropic HTTP {resp.status_code}: {resp.text[:400]}")
            data = resp.json()
        blocks = data.get("content") or []
        return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()

    def transcribe_audio(self, wav_path: Path, cfg: AppConfig) -> str:
        """Transcribe recorded WAV speech via Groq/OpenAI Whisper or Google Gemini."""
        provider = cfg.stt_provider or "groq"
        preset = AI_PROVIDER_PRESETS.get(provider, AI_PROVIDER_PRESETS["groq"])
        api_key = self._resolve_key(cfg, provider)
        if not api_key:
            raise ValueError(f"API key for STT provider '{preset.get('name', provider)}' is not configured.")
        model = (cfg.stt_model or preset.get("stt_model", "whisper-large-v3-turbo")).strip()
        base_url = (cfg.stt_base_url or preset.get("base_url", "")).rstrip("/")

        if preset.get("kind") == "gemini":
            b64 = base64.b64encode(wav_path.read_bytes()).decode("ascii")
            clean_model = model if model.startswith("models/") else f"models/{model}"
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": "Transcribe the spoken audio accurately verbatim. Output only the transcription text."},
                            {"inline_data": {"mime_type": "audio/wav", "data": b64}},
                        ],
                    }
                ]
            }
            with httpx.Client(timeout=45.0) as client:
                r = client.post(f"{base_url}/{clean_model}:generateContent", params={"key": api_key}, json=payload)
                r.raise_for_status()
                cands = r.json().get("candidates") or []
                parts = cands[0].get("content", {}).get("parts", []) if cands else []
                return " ".join(p.get("text", "") for p in parts).strip()

        url = f"{base_url}/audio/transcriptions"
        with wav_path.open("rb") as f, httpx.Client(timeout=45.0) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": model},
                files={"file": (wav_path.name, f, "audio/wav")},
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"STT HTTP {resp.status_code}: {resp.text[:300]}")
            return str(resp.json().get("text", "")).strip()

    def upload_screenshot(self, image_path: Path, provider: str, api_key: str) -> str:
        """Upload screenshot PNG to ImgBB or Imgur and return public URL."""
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        prov = provider.lower()
        with httpx.Client(timeout=20.0) as client:
            if prov == "imgbb":
                if not api_key:
                    raise ValueError("ImgBB API key is required")
                r = client.post("https://api.imgbb.com/1/upload", data={"key": api_key, "image": b64})
                r.raise_for_status()
                return str(r.json()["data"]["url"])
            if prov == "imgur":
                if not api_key:
                    raise ValueError("Imgur Client-ID is required")
                r = client.post(
                    "https://api.imgur.com/3/image",
                    headers={"Authorization": f"Client-ID {api_key}"},
                    data={"image": b64, "type": "base64"},
                )
                r.raise_for_status()
                return str(r.json()["data"]["link"])
        raise ValueError(f"Unsupported upload provider: {provider}")


class VoiceRecorder(QObject):
    """Records 16kHz mono PCM WAV audio from system mic and emits RMS amplitude."""

    level_changed = pyqtSignal(float)  # 0..1

    def __init__(self, out_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._source: QAudioSource | None = None
        self._io: QIODevice | None = None
        self._pcm = bytearray()
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> bool:
        if self._recording:
            return False
        dev = QMediaDevices.defaultAudioInput()
        if dev.isNull():
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(16000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not dev.isFormatSupported(fmt):
            fmt = dev.preferredFormat()
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._fmt = fmt
        self._pcm.clear()
        self._source = QAudioSource(dev, fmt, self)
        self._io = self._source.start()
        if self._io is None:
            self._source = None
            return False
        self._io.readyRead.connect(self._on_ready_read)
        self._recording = True
        return True

    def _on_ready_read(self) -> None:
        if self._io is None:
            return
        chunk = bytes(self._io.readAll())
        if not chunk:
            return
        self._pcm.extend(chunk)
        # Compute RMS on Int16 samples
        count = len(chunk) // 2
        if count > 0:
            samples = struct.unpack(f"<{count}h", chunk[:count * 2])
            sq = sum(s * s for s in samples[::4]) / max(1, len(samples[::4]))
            rms = min(1.0, math.sqrt(sq) / 14000.0)
            self.level_changed.emit(rms)

    def stop(self) -> Path | None:
        if not self._recording:
            return None
        self._recording = False
        if self._io is not None:
            self._on_ready_read()
        if self._source is not None:
            self._source.stop()
            self._source.deleteLater()
            self._source = None
        self._io = None
        if len(self._pcm) < 3200:
            return None
        out_path = self.out_dir / "dictation_latest.wav"
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._fmt.sampleRate())
            wf.writeframes(bytes(self._pcm))
        return out_path
