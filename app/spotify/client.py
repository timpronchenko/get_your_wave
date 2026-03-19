"""HTTP клиент для Spotify Web API."""
import logging
import time
from typing import Dict, List, Optional

import httpx

from app.storage import db
from app.spotify import oauth

logger = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
TOP_50_GLOBAL_PLAYLIST_ID = "37i9dQZEVXbMDoHDwVN2tF"


async def ensure_valid_token(telegram_user_id: int) -> Optional[str]:
    """Проверить и обновить токен при необходимости."""
    user = db.get_user(telegram_user_id)
    if not user:
        return None
    
    access_token = user['access_token']
    expires_at = user['token_expires_at']
    
    # Проверка: токен истекает в течение 60 секунд?
    if time.time() >= expires_at - 60:
        logger.info(f"Токен истекает, обновляю для telegram_user_id={telegram_user_id}")
        refresh_result = await oauth.refresh_access_token(user['refresh_token'])
        
        if not refresh_result:
            logger.error("Не удалось обновить токен")
            return None
        
        new_access_token = refresh_result['access_token']
        expires_in = refresh_result.get('expires_in', 3600)
        new_expires_at = int(time.time()) + expires_in
        
        db.update_tokens(telegram_user_id, new_access_token, new_expires_at)
        access_token = new_access_token
    
    return access_token


async def get_me(access_token: str) -> Optional[Dict]:
    """Получить информацию о текущем пользователе."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{SPOTIFY_API_BASE}/me",
                headers={'Authorization': f'Bearer {access_token}'}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("401 Unauthorized при получении /me")
            else:
                logger.error(f"Ошибка /me: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при запросе /me: {e}")
            return None


async def get_top_tracks(access_token: str, limit: int = 20) -> List[Dict]:
    """Получить топ треки из Top 50 Global плейлиста."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{SPOTIFY_API_BASE}/playlists/{TOP_50_GLOBAL_PLAYLIST_ID}/tracks",
                headers={'Authorization': f'Bearer {access_token}'},
                params={'limit': limit, 'fields': 'items(track(uri,name,artists))'}
            )
            response.raise_for_status()
            data = response.json()
            
            tracks = []
            for item in data.get('items', []):
                track = item.get('track')
                if track:
                    tracks.append({
                        'uri': track['uri'],
                        'name': track['name'],
                        'artists': ', '.join(a['name'] for a in track.get('artists', []))
                    })
            
            logger.info(f"Получено {len(tracks)} треков из Top 50 Global")
            return tracks
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("401 Unauthorized при получении треков")
            else:
                logger.error(f"Ошибка получения треков: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"Ошибка при получении треков: {e}")
            return []


async def create_playlist(access_token: str, user_id: str, name: str, public: bool = False) -> Optional[Dict]:
    """Создать плейлист."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{SPOTIFY_API_BASE}/users/{user_id}/playlists",
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                },
                json={
                    'name': name,
                    'public': public,
                    'description': 'Создано через Telegram бота для дипломного проекта'
                }
            )
            response.raise_for_status()
            playlist = response.json()
            logger.info(f"Плейлист создан: {playlist.get('id')}")
            return playlist
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("401 Unauthorized при создании плейлиста")
            else:
                logger.error(f"Ошибка создания плейлиста: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при создании плейлиста: {e}")
            return None


async def add_tracks_to_playlist(access_token: str, playlist_id: str, track_uris: List[str]) -> bool:
    """Добавить треки в плейлист."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks",
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                },
                json={'uris': track_uris}
            )
            response.raise_for_status()
            logger.info(f"Добавлено {len(track_uris)} треков в плейлист {playlist_id}")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("401 Unauthorized при добавлении треков")
            else:
                logger.error(f"Ошибка добавления треков: {e.response.status_code} - {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"Ошибка при добавлении треков: {e}")
            return False


async def make_playlist_with_top_tracks(telegram_user_id: int) -> Optional[str]:
    """Создать плейлист с топ-20 треками из Top 50 Global."""
    # Проверить и обновить токен
    access_token = await ensure_valid_token(telegram_user_id)
    if not access_token:
        return None
    
    # Получить информацию о пользователе
    me = await get_me(access_token)
    if not me:
        # Попробовать обновить токен и повторить
        access_token = await ensure_valid_token(telegram_user_id)
        if not access_token:
            return None
        me = await get_me(access_token)
        if not me:
            return None
    
    user_id = me['id']
    
    # Получить топ-20 треков
    tracks = await get_top_tracks(access_token, limit=20)
    if not tracks:
        return None
    
    # Создать плейлист
    playlist = await create_playlist(
        access_token,
        user_id,
        "Diploma MVP — Top 20",
        public=False
    )
    if not playlist:
        return None
    
    playlist_id = playlist['id']
    
    # Добавить треки
    track_uris = [track['uri'] for track in tracks]
    success = await add_tracks_to_playlist(access_token, playlist_id, track_uris)
    if not success:
        return None
    
    # Вернуть ссылку на плейлист
    return playlist.get('external_urls', {}).get('spotify')
