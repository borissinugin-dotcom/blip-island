"""Local SQLite persistence layer for clipboard, shelf, notes, reminders, chat, colors, and launcher."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ClipboardEntry:
    id: int
    kind: str  # text | image | file
    text_content: str
    image_path: str
    preview_text: str
    source_app: str
    pinned: bool
    content_hash: str
    created_at: float


@dataclass
class ShelfItem:
    id: int
    file_path: str
    display_name: str
    kind: str
    size_bytes: int
    added_at: float


@dataclass
class NoteItem:
    id: int
    folder: str
    title: str
    content_html: str
    content_text: str
    updated_at: float


@dataclass
class ReminderItem:
    id: int
    text: str
    due_at: float
    repeat_days: str  # comma-separated weekday ints 0..6, or "daily", or ""
    active: bool
    triggered: bool
    created_at: float


@dataclass
class ChatMessage:
    id: int
    session_id: str
    role: str  # user | assistant
    content: str
    attachments: list[str]
    created_at: float


@dataclass
class LauncherItem:
    id: int
    title: str
    target: str
    kind: str  # app | url | command
    group_name: str
    icon_path: str
    hotkey: str
    sort_order: int


class LocalStorage:
    """Thread-safe SQLite database wrapper."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clipboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                text_content TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                preview_text TEXT NOT NULL DEFAULT '',
                source_app TEXT NOT NULL DEFAULT '',
                pinned INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_clip_hash ON clipboard_history(content_hash);
            CREATE INDEX IF NOT EXISTS idx_clip_pin_time ON clipboard_history(pinned DESC, created_at DESC);

            CREATE TABLE IF NOT EXISTS shelf_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'file',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                added_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder TEXT NOT NULL DEFAULT 'Задачи',
                title TEXT NOT NULL,
                content_html TEXT NOT NULL DEFAULT '',
                content_text TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                due_at REAL NOT NULL,
                repeat_days TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                triggered INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                attachments_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id, id);

            CREATE TABLE IF NOT EXISTS picked_colors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hex_code TEXT NOT NULL,
                rgb_text TEXT NOT NULL,
                hsl_text TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS launcher_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                target TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'app',
                group_name TEXT NOT NULL DEFAULT '',
                icon_path TEXT NOT NULL DEFAULT '',
                hotkey TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # Ensure at least one default note exists
        count = self._conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        if count == 0:
            now = time.time()
            self._conn.execute(
                "INSERT INTO notes(folder, title, content_html, content_text, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("Задачи", "Быстрая заметка", "", "", now),
            )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._conn.close()

    # ------------------------------------------------------------------ Clipboard
    def add_clipboard_entry(
        self,
        kind: str,
        text_content: str,
        image_path: str,
        preview_text: str,
        source_app: str,
        content_hash: str,
        max_items: int = 300,
    ) -> int:
        now = time.time()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, pinned FROM clipboard_history WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            if existing is not None:
                self._conn.execute(
                    "UPDATE clipboard_history SET created_at = ?, source_app = ? WHERE id = ?",
                    (now, source_app, existing["id"]),
                )
                self._conn.commit()
                return int(existing["id"])

            cur = self._conn.execute(
                """
                INSERT INTO clipboard_history
                    (kind, text_content, image_path, preview_text, source_app, pinned, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (kind, text_content, image_path, preview_text, source_app, content_hash, now),
            )
            # Prune old unpinned entries beyond max_items
            rows = self._conn.execute(
                "SELECT id, image_path FROM clipboard_history WHERE pinned = 0 ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                (max_items,),
            ).fetchall()
            for r in rows:
                if r["image_path"]:
                    try:
                        Path(r["image_path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                self._conn.execute("DELETE FROM clipboard_history WHERE id = ?", (r["id"],))
            self._conn.commit()
            return int(cur.lastrowid)

    def list_clipboard(self, query: str = "", limit: int = 100) -> list[ClipboardEntry]:
        with self._lock:
            if query.strip():
                like = f"%{query.strip()}%"
                rows = self._conn.execute(
                    """
                    SELECT * FROM clipboard_history
                    WHERE preview_text LIKE ? OR text_content LIKE ? OR source_app LIKE ?
                    ORDER BY pinned DESC, created_at DESC LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM clipboard_history ORDER BY pinned DESC, created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            ClipboardEntry(
                id=r["id"],
                kind=r["kind"],
                text_content=r["text_content"],
                image_path=r["image_path"],
                preview_text=r["preview_text"],
                source_app=r["source_app"],
                pinned=bool(r["pinned"]),
                content_hash=r["content_hash"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def toggle_clipboard_pin(self, entry_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT pinned FROM clipboard_history WHERE id = ?", (entry_id,)
            ).fetchone()
            if not row:
                return False
            new_val = 0 if row["pinned"] else 1
            self._conn.execute(
                "UPDATE clipboard_history SET pinned = ? WHERE id = ?", (new_val, entry_id)
            )
            self._conn.commit()
            return bool(new_val)

    def delete_clipboard_entry(self, entry_id: int) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT image_path FROM clipboard_history WHERE id = ?", (entry_id,)
            ).fetchone()
            if row and row["image_path"]:
                try:
                    Path(row["image_path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            self._conn.execute("DELETE FROM clipboard_history WHERE id = ?", (entry_id,))
            self._conn.commit()

    def clear_unpinned_clipboard(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT image_path FROM clipboard_history WHERE pinned = 0"
            ).fetchall()
            for r in rows:
                if r["image_path"]:
                    try:
                        Path(r["image_path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
            self._conn.execute("DELETE FROM clipboard_history WHERE pinned = 0")
            self._conn.commit()

    # ------------------------------------------------------------------ Shelf
    def add_shelf_file(self, path_str: str) -> int:
        p = Path(path_str)
        kind = "dir" if p.is_dir() else "file"
        size = p.stat().st_size if p.is_file() else 0
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO shelf_items(file_path, display_name, kind, size_bytes, added_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET added_at = excluded.added_at, size_bytes = excluded.size_bytes
                """,
                (str(p), p.name or str(p), kind, size, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM shelf_items WHERE file_path = ?", (str(p),)
            ).fetchone()
            return int(row["id"])

    def list_shelf_items(self) -> list[ShelfItem]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM shelf_items ORDER BY added_at DESC"
            ).fetchall()
        return [
            ShelfItem(
                id=r["id"],
                file_path=r["file_path"],
                display_name=r["display_name"],
                kind=r["kind"],
                size_bytes=r["size_bytes"],
                added_at=r["added_at"],
            )
            for r in rows
        ]

    def remove_shelf_item(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM shelf_items WHERE id = ?", (item_id,))
            self._conn.commit()

    def clear_shelf(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM shelf_items")
            self._conn.commit()

    # ------------------------------------------------------------------ Notes
    def list_notes(self, folder: str | None = None) -> list[NoteItem]:
        with self._lock:
            if folder:
                rows = self._conn.execute(
                    "SELECT * FROM notes WHERE folder = ? ORDER BY updated_at DESC", (folder,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM notes ORDER BY updated_at DESC"
                ).fetchall()
        return [
            NoteItem(
                id=r["id"],
                folder=r["folder"],
                title=r["title"],
                content_html=r["content_html"],
                content_text=r["content_text"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def list_note_folders(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT folder FROM notes ORDER BY folder"
            ).fetchall()
        folders = [r["folder"] for r in rows]
        for base in ("Задачи", "Идеи"):
            if base not in folders:
                folders.append(base)
        return folders

    def save_note(
        self,
        note_id: int | None,
        folder: str,
        title: str,
        content_html: str,
        content_text: str,
    ) -> int:
        now = time.time()
        with self._lock:
            if note_id is None:
                cur = self._conn.execute(
                    "INSERT INTO notes(folder, title, content_html, content_text, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (folder, title, content_html, content_text, now),
                )
                self._conn.commit()
                return int(cur.lastrowid)
            self._conn.execute(
                "UPDATE notes SET folder=?, title=?, content_html=?, content_text=?, updated_at=? WHERE id=?",
                (folder, title, content_html, content_text, now, note_id),
            )
            self._conn.commit()
            return note_id

    def delete_note(self, note_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            self._conn.commit()

    # ------------------------------------------------------------------ Reminders
    def add_reminder(self, text: str, due_at: float, repeat_days: str = "") -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO reminders(text, due_at, repeat_days, active, triggered, created_at)
                VALUES (?, ?, ?, 1, 0, ?)
                """,
                (text, due_at, repeat_days, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_reminders(self, include_inactive: bool = False) -> list[ReminderItem]:
        with self._lock:
            q = "SELECT * FROM reminders" + ("" if include_inactive else " WHERE active = 1")
            rows = self._conn.execute(q + " ORDER BY due_at ASC").fetchall()
        return [
            ReminderItem(
                id=r["id"],
                text=r["text"],
                due_at=r["due_at"],
                repeat_days=r["repeat_days"],
                active=bool(r["active"]),
                triggered=bool(r["triggered"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def update_reminder(self, reminder_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"text", "due_at", "repeat_days", "active", "triggered"}
        cols = [f"{k} = ?" for k in fields if k in allowed]
        vals = [int(v) if isinstance(v, bool) else v for k, v in fields.items() if k in allowed]
        if not cols:
            return
        vals.append(reminder_id)
        with self._lock:
            self._conn.execute(f"UPDATE reminders SET {', '.join(cols)} WHERE id = ?", vals)
            self._conn.commit()

    def delete_reminder(self, reminder_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            self._conn.commit()

    # ------------------------------------------------------------------ Chat
    def add_chat_message(
        self, session_id: str, role: str, content: str, attachments: list[str] | None = None
    ) -> int:
        now = time.time()
        att_json = json.dumps(attachments or [], ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO chat_messages(session_id, role, content, attachments_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, att_json, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_chat_messages(self, session_id: str = "default", limit: int = 80) -> list[ChatMessage]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
                ) sub ORDER BY id ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [
            ChatMessage(
                id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=r["content"],
                attachments=json.loads(r["attachments_json"] or "[]"),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def clear_chat_session(self, session_id: str = "default") -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            self._conn.commit()

    # ------------------------------------------------------------------ Colors
    def add_picked_color(self, hex_code: str, rgb_text: str, hsl_text: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO picked_colors(hex_code, rgb_text, hsl_text, created_at) VALUES (?, ?, ?, ?)",
                (hex_code, rgb_text, hsl_text, now),
            )
            self._conn.execute(
                "DELETE FROM picked_colors WHERE id NOT IN (SELECT id FROM picked_colors ORDER BY created_at DESC LIMIT 30)"
            )
            self._conn.commit()

    def list_picked_colors(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM picked_colors ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ Launcher
    def add_launcher_item(
        self,
        title: str,
        target: str,
        kind: str = "app",
        group_name: str = "",
        icon_path: str = "",
        hotkey: str = "",
    ) -> int:
        with self._lock:
            max_ord = self._conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) FROM launcher_items"
            ).fetchone()[0]
            cur = self._conn.execute(
                """
                INSERT INTO launcher_items(title, target, kind, group_name, icon_path, hotkey, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (title, target, kind, group_name, icon_path, hotkey, int(max_ord) + 1),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_launcher_items(self) -> list[LauncherItem]:
        with self._lock:
            if getattr(self, "_closed", False):
                return []
            try:
                rows = self._conn.execute(
                    "SELECT * FROM launcher_items ORDER BY sort_order ASC, id ASC"
                ).fetchall()
            except sqlite3.ProgrammingError:
                return []
        return [
            LauncherItem(
                id=r["id"],
                title=r["title"],
                target=r["target"],
                kind=r["kind"],
                group_name=r["group_name"],
                icon_path=r["icon_path"],
                hotkey=r["hotkey"],
                sort_order=r["sort_order"],
            )
            for r in rows
        ]

    def update_launcher_item(self, item_id: int, **fields: Any) -> None:
        allowed = {"title", "target", "kind", "group_name", "icon_path", "hotkey", "sort_order"}
        cols = [f"{k} = ?" for k in fields if k in allowed]
        vals = [v for k, v in fields.items() if k in allowed]
        if not cols:
            return
        vals.append(item_id)
        with self._lock:
            self._conn.execute(f"UPDATE launcher_items SET {', '.join(cols)} WHERE id = ?", vals)
            self._conn.commit()

    def delete_launcher_item(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM launcher_items WHERE id = ?", (item_id,))
            self._conn.commit()
