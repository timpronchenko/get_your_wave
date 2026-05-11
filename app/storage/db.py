"""SQLite хранилище для токенов пользователей и истории плейлистов."""
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "users.db"


def init_db():
    """Инициализировать БД: таблицы users и playlists."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id INTEGER PRIMARY KEY,
            spotify_user_id TEXT NOT NULL,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            token_expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            spotify_playlist_id TEXT,
            name TEXT NOT NULL,
            url TEXT,
            source TEXT NOT NULL,
            prompt TEXT,
            tracks_count INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_playlists_user_created
        ON playlists(telegram_user_id, created_at DESC)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_playlists_spotify_pid
        ON playlists(spotify_playlist_id)
    """)

    conn.commit()
    conn.close()
    logger.info(f"База данных инициализирована: {DB_PATH}")


def get_user(telegram_user_id: int) -> Optional[Dict[str, Any]]:
    """Получить пользователя по telegram_user_id."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM users WHERE telegram_user_id = ?
    """, (telegram_user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return None


def save_user(
    telegram_user_id: int,
    spotify_user_id: str,
    access_token: str,
    refresh_token: str,
    token_expires_at: int
):
    """Сохранить или обновить пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute("""
        INSERT INTO users (
            telegram_user_id, spotify_user_id, access_token,
            refresh_token, token_expires_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
            spotify_user_id = excluded.spotify_user_id,
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            token_expires_at = excluded.token_expires_at,
            updated_at = excluded.updated_at
    """, (telegram_user_id, spotify_user_id, access_token,
          refresh_token, token_expires_at, now, now))
    
    conn.commit()
    conn.close()
    logger.info(f"Пользователь сохранён: telegram_user_id={telegram_user_id}")


def update_tokens(
    telegram_user_id: int,
    access_token: str,
    token_expires_at: int,
    refresh_token: str | None = None,
):
    """Обновить access_token, expires_at и (опционально) refresh_token."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    now = datetime.now(timezone.utc).isoformat()

    if refresh_token:
        cursor.execute("""
            UPDATE users
            SET access_token = ?, token_expires_at = ?, refresh_token = ?, updated_at = ?
            WHERE telegram_user_id = ?
        """, (access_token, token_expires_at, refresh_token, now, telegram_user_id))
    else:
        cursor.execute("""
            UPDATE users
            SET access_token = ?, token_expires_at = ?, updated_at = ?
            WHERE telegram_user_id = ?
        """, (access_token, token_expires_at, now, telegram_user_id))

    conn.commit()
    conn.close()
    logger.info("Токены обновлены: telegram_user_id=%s (refresh_token %s)",
                telegram_user_id, "обновлён" if refresh_token else "без изменений")


def delete_user(telegram_user_id: int) -> bool:
    """Удалить пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM users WHERE telegram_user_id = ?", (telegram_user_id,))
    deleted = cursor.rowcount > 0
    
    conn.commit()
    conn.close()
    
    if deleted:
        logger.info(f"Пользователь удалён: telegram_user_id={telegram_user_id}")
    
    return deleted


def add_playlist(
    telegram_user_id: int,
    name: str,
    source: str,
    *,
    spotify_playlist_id: Optional[str] = None,
    url: Optional[str] = None,
    prompt: Optional[str] = None,
    tracks_count: Optional[int] = None,
) -> int:
    """Записать факт создания плейлиста в историю. Возвращает id строки."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO playlists (
            telegram_user_id, spotify_playlist_id, name, url,
            source, prompt, tracks_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (telegram_user_id, spotify_playlist_id, name, url,
         source, prompt, tracks_count, now),
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info(
        "История плейлистов: добавлен id=%s (user=%s, source=%s)",
        row_id, telegram_user_id, source,
    )
    return row_id


def get_playlist(playlist_id: int) -> Optional[Dict[str, Any]]:
    """Получить один плейлист по id."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def increment_tracks_count(spotify_playlist_id: str, added: int = 1):
    """Увеличить tracks_count для плейлиста по spotify_playlist_id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE playlists SET tracks_count = COALESCE(tracks_count, 0) + ? WHERE spotify_playlist_id = ?",
        (added, spotify_playlist_id),
    )
    conn.commit()
    conn.close()


def delete_playlist(playlist_id: int) -> bool:
    """Удалить плейлист из истории по id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        logger.info("Плейлист удалён из истории: id=%s", playlist_id)
    return deleted


def list_playlists(telegram_user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    """Вернуть последние N плейлистов пользователя (новые сверху)."""
    limit = max(1, min(int(limit), 50))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, spotify_playlist_id, name, url, source, prompt,
               tracks_count, created_at
        FROM playlists
        WHERE telegram_user_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (telegram_user_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
