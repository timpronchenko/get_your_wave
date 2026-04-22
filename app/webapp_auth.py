"""Валидация Telegram WebApp initData (HMAC-SHA256)."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, unquote

from app.config import settings


def validate_init_data(init_data: str, *, max_age: int = 86400) -> dict | None:
    """Проверить подпись initData от Telegram WebApp.

    Возвращает dict с данными пользователя (user, auth_date и т.д.)
    или None если подпись невалидна / данные просрочены.
    """
    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            return None

        # Собираем строку для проверки (все поля кроме hash, отсортированные)
        data_check_pairs = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            data_check_pairs.append(f"{key}={values[0]}")
        data_check_pairs.sort()
        data_check_string = "\n".join(data_check_pairs)

        # HMAC ключ: HMAC_SHA256(bot_token, "WebAppData")
        secret_key = hmac.new(
            b"WebAppData",
            settings.telegram_bot_token.encode(),
            hashlib.sha256,
        ).digest()

        # Проверка подписи
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        # Проверка возраста
        auth_date_str = parsed.get("auth_date", ["0"])[0]
        auth_date = int(auth_date_str)
        if time.time() - auth_date > max_age:
            return None

        # Извлекаем данные пользователя
        user_json = parsed.get("user", [None])[0]
        if not user_json:
            return None

        user = json.loads(unquote(user_json))
        return {
            "user": user,
            "telegram_user_id": user.get("id"),
            "auth_date": auth_date,
        }
    except Exception:
        return None
