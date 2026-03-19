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

logger = logging.getLogger(__name__)

# Хранилище PKCE verifier и state в памяти (TTL 10 минут)
_pkce_store: Dict[str, Dict] = {}
_STATE_TTL = 600  # 10 минут


def generate_pkce() -> tuple[str, str]:
    """Сгенерировать code_verifier и code_challenge для PKCE."""
    # Code verifier: случайная строка 43-128 символов
    code_verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode('utf-8').rstrip('=')
    
    # Code challenge: SHA256(code_verifier), base64url
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    
    return code_verifier, code_challenge


def generate_state(telegram_user_id: int) -> str:
    """Сгенерировать state с привязкой к telegram_user_id."""
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce()
    
    _pkce_store[state] = {
        'telegram_user_id': telegram_user_id,
        'code_verifier': code_verifier,
        'code_challenge': code_challenge,
        'created_at': time.time()
    }
    
    logger.info(f"Сгенерирован state для telegram_user_id={telegram_user_id}")
    return state


def get_pkce_data(state: str) -> Optional[Dict]:
    """Получить PKCE данные по state (с проверкой TTL)."""
    if state not in _pkce_store:
        return None
    
    data = _pkce_store[state]
    age = time.time() - data['created_at']
    
    if age > _STATE_TTL:
        logger.warning(f"State истёк: state={state}, age={age:.1f}s")
        del _pkce_store[state]
        return None
    
    return data


def clear_state(state: str):
    """Удалить state из хранилища."""
    if state in _pkce_store:
        del _pkce_store[state]


def get_authorization_url(telegram_user_id: int) -> str:
    """Получить URL для авторизации Spotify."""
    state = generate_state(telegram_user_id)
    
    # Получить code_challenge из хранилища (уже создан в generate_state)
    pkce_data = _pkce_store[state]
    code_challenge = pkce_data['code_challenge']
    
    params = {
        'response_type': 'code',
        'client_id': settings.spotify_client_id,
        'redirect_uri': settings.spotify_redirect_uri,
        'code_challenge_method': 'S256',
        'code_challenge': code_challenge,
        'state': state,
        'scope': 'playlist-modify-private playlist-modify-public'
    }
    
    url = f"https://accounts.spotify.com/authorize?{urlencode(params)}"
    logger.info(f"Сгенерирован auth URL для telegram_user_id={telegram_user_id}")
    return url


async def exchange_code_for_tokens(code: str, state: str) -> Optional[Dict]:
    """Обменять authorization code на токены."""
    pkce_data = get_pkce_data(state)
    if not pkce_data:
        logger.error(f"Неверный или истёкший state: {state}")
        return None
    
    code_verifier = pkce_data['code_verifier']
    
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': settings.spotify_redirect_uri,
        'client_id': settings.spotify_client_id,
        'code_verifier': code_verifier
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                'https://accounts.spotify.com/api/token',
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            response.raise_for_status()
            tokens = response.json()
            
            logger.info(f"Токены получены для state={state}")
            clear_state(state)
            
            return tokens
        except httpx.HTTPStatusError as e:
            logger.error(f"Ошибка обмена кода: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при обмене кода: {e}")
            return None


async def refresh_access_token(refresh_token: str) -> Optional[Dict]:
    """Обновить access_token используя refresh_token."""
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': settings.spotify_client_id
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                'https://accounts.spotify.com/api/token',
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info("Access token обновлён")
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Ошибка обновления токена: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при обновлении токена: {e}")
            return None
