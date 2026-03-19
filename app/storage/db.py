"""SQLite хранилище для токенов пользователей."""
import sqlite3
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "users.db"


def init_db():
    """Инициализировать БД и создать таблицу users."""
    conn = sqlite3.connect(DB_PATH)
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
    
    now = datetime.utcnow().isoformat()
    
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
    token_expires_at: int
):
    """Обновить access_token и expires_at."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    now = datetime.utcnow().isoformat()
    
    cursor.execute("""
        UPDATE users
        SET access_token = ?, token_expires_at = ?, updated_at = ?
        WHERE telegram_user_id = ?
    """, (access_token, token_expires_at, now, telegram_user_id))
    
    conn.commit()
    conn.close()
    logger.info(f"Токены обновлены: telegram_user_id={telegram_user_id}")


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
