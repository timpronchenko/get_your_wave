"""Spotify OAuth с PKCE (Authorization Code Flow)."""
import secrets
import hashlib
import base64
import logging
import time
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.storage import db

logger = logging.getLogger(__name__)

_pkce_store: Dict[str, Dict] = {}
_STATE_TTL = 600  # 10 минут


def generate_pkce() -> tuple[str, str]:
    """Сгенерировать code_verifier и code_challenge для PKCE."""
    code_verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode('utf-8').rstrip('=')

    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')

    return code_verifier, code_challenge


def create_state(telegram_user_id: int) -> str:
    """Сгенерировать state с привязкой к telegram_user_id и сохранить PKCE."""
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce()

    _pkce_store[state] = {
        'telegram_user_id': telegram_user_id,
        'code_verifier': code_verifier,
        'code_challenge': code_challenge,
        'created_at': time.time(),
    }

    logger.info("Сгенерирован state для telegram_user_id=%s", telegram_user_id)
    return state


def pop_pkce_data(state: str) -> Optional[Dict]:
    """Извлечь и удалить PKCE-данные по state (с проверкой TTL).

    Возвращает dict с ключами telegram_user_id, code_verifier, code_challenge
    или None, если state невалиден / истёк.
    """
    data = _pkce_store.pop(state, None)
    if data is None:
        return None

    age = time.time() - data['created_at']
    if age > _STATE_TTL:
        logger.warning("State истёк: age=%.1fs", age)
        return None

    return data


def get_authorization_url(telegram_user_id: int) -> str:
    """Получить URL для авторизации Spotify."""
    state = create_state(telegram_user_id)
    pkce_data = _pkce_store[state]

    params = {
        'response_type': 'code',
        'client_id': settings.spotify_client_id,
        'redirect_uri': settings.spotify_redirect_uri,
        'code_challenge_method': 'S256',
        'code_challenge': pkce_data['code_challenge'],
        'state': state,
        'scope': 'playlist-modify-private playlist-modify-public',
    }

    url = f"https://accounts.spotify.com/authorize?{urlencode(params)}"
    logger.info("Auth URL сгенерирован для telegram_user_id=%s", telegram_user_id)
    return url


async def exchange_code_for_tokens(code: str, code_verifier: str) -> Optional[Dict]:
    """Обменять authorization code на токены (PKCE)."""
    payload = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': settings.spotify_redirect_uri,
        'client_id': settings.spotify_client_id,
        'code_verifier': code_verifier,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(
                'https://accounts.spotify.com/api/token',
                data=payload,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            resp.raise_for_status()
            tokens = resp.json()
            logger.info("Токены получены")
            return tokens
        except httpx.HTTPStatusError as e:
            logger.error("Ошибка обмена кода: %s — %s", e.response.status_code, e.response.text)
            return None
        except Exception as e:
            logger.error("Ошибка при обмене кода: %s", e)
            return None


async def process_oauth_callback(code: str, state: str) -> tuple[bool, int | None, str | None, str | None]:
    """Обработать OAuth callback (общая логика для HTTP и Telegram).

    Возвращает (success, telegram_user_id, spotify_user_id, error_message).
    """
    from app.spotify.client import get_me

    pkce_data = pop_pkce_data(state)
    if pkce_data is None:
        return False, None, None, "Невалидный или истёкший state"

    telegram_user_id: int = pkce_data["telegram_user_id"]
    code_verifier: str = pkce_data["code_verifier"]

    tokens = await exchange_code_for_tokens(code, code_verifier)
    if tokens is None:
        return False, telegram_user_id, None, "Не удалось обменять код на токены"

    access_token = tokens["access_token"]
    refresh_tok = tokens["refresh_token"]
    expires_at = int(time.time()) + tokens.get("expires_in", 3600)

    me = await get_me(access_token)
    if me is None:
        return False, telegram_user_id, None, "Не удалось получить профиль Spotify"

    db.save_user(
        telegram_user_id=telegram_user_id,
        spotify_user_id=me["id"],
        access_token=access_token,
        refresh_token=refresh_tok,
        token_expires_at=expires_at,
    )

    logger.info("Пользователь авторизован: tg=%s spotify=%s", telegram_user_id, me["id"])
    return True, telegram_user_id, me["id"], None


async def refresh_access_token(refresh_token: str) -> Optional[Dict]:
    """Обновить access_token используя refresh_token."""
    payload = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': settings.spotify_client_id,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(
                'https://accounts.spotify.com/api/token',
                data=payload,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info("Access token обновлён")
            return result
        except httpx.HTTPStatusError as e:
            logger.error("Ошибка обновления токена: %s — %s", e.response.status_code, e.response.text)
            return None
        except Exception as e:
            logger.error("Ошибка при обновлении токена: %s", e)
            return None
