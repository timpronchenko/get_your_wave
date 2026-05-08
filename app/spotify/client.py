"""HTTP клиент для Spotify Web API."""
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import httpx

from app.storage import db
from app.spotify import oauth

logger = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
TOP_50_GLOBAL_PLAYLIST_ID = "37i9dQZEVXbMDoHDwVN2tF"

# Дефолт httpx — 5 с; Spotify иногда отвечает дольше → таймаут и пустое сообщение в логе
_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=15.0)

# Spotify track id (base62)
_TRACK_ID_RE = re.compile(r"^[0-9A-Za-z]{22}$")
_SPOTIFY_URI_RE = re.compile(r"spotify:track:([0-9A-Za-z]+)")
_OPEN_TRACK_URL_RE = re.compile(
    r"open\.spotify\.com/track/([0-9A-Za-z]+)",
    re.IGNORECASE,
)


def parse_track_uri_from_text(text: str) -> Optional[str]:
    """Извлечь spotify:track:... из текста (URI или ссылка open.spotify.com)."""
    text = text.strip()
    if not text:
        return None

    m = _SPOTIFY_URI_RE.search(text)
    if m:
        tid = m.group(1)
        if _TRACK_ID_RE.match(tid):
            return f"spotify:track:{tid}"

    m = _OPEN_TRACK_URL_RE.search(text)
    if m:
        tid = m.group(1)
        if _TRACK_ID_RE.match(tid):
            return f"spotify:track:{tid}"

    return None


async def get_track_by_id(access_token: str, track_id: str) -> Optional[Dict]:
    """GET /v1/tracks/{id} — для отображения названия."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/tracks/{track_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            t = resp.json()
            return {
                "uri": t["uri"],
                "name": t["name"],
                "artists": ", ".join(a["name"] for a in t.get("artists", [])),
            }
        except Exception as e:
            logger.warning("get_track_by_id: %s", e)
            return None


async def search_first_track(
    access_token: str, query: str, limit: int = 5
) -> Optional[Dict]:
    """Поиск трека по строке; возвращает первый результат или None."""
    query = query.strip()
    if not query:
        return None

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/search",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"q": query, "type": "track", "limit": min(limit, 50)},
            )
            resp.raise_for_status()
            items = resp.json().get("tracks", {}).get("items") or []
            if not items:
                logger.info("Поиск не дал результатов: %r", query[:80])
                return None
            t = items[0]
            return {
                "uri": t["uri"],
                "name": t["name"],
                "artists": ", ".join(a["name"] for a in t.get("artists", [])),
            }
        except httpx.HTTPStatusError as e:
            logger.error("Ошибка search: %s — %s", e.response.status_code, e.response.text)
            return None
        except Exception as e:
            logger.error("Ошибка при search: %s", e)
            return None


async def search_tracks(
    access_token: str, query: str, limit: int = 5
) -> List[Dict]:
    """Поиск треков по строке; до limit результатов."""
    query = query.strip()
    if not query:
        return []

    limit = max(1, min(limit, 50))

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/search",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"q": query, "type": "track", "limit": limit},
            )
            resp.raise_for_status()
            items = resp.json().get("tracks", {}).get("items") or []
            out: List[Dict] = []
            for t in items:
                out.append(
                    {
                        "uri": t["uri"],
                        "name": t["name"],
                        "artists": ", ".join(a["name"] for a in t.get("artists", [])),
                    }
                )
            return out
        except httpx.HTTPStatusError as e:
            logger.error("Ошибка search: %s — %s", e.response.status_code, e.response.text)
            return []
        except Exception as e:
            logger.error("Ошибка при search: %s", e)
            return []


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
        new_refresh_token = refresh_result.get('refresh_token')
        expires_in = refresh_result.get('expires_in', 3600)
        new_expires_at = int(time.time()) + expires_in

        db.update_tokens(telegram_user_id, new_access_token, new_expires_at, new_refresh_token)
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
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{SPOTIFY_API_BASE}/playlists/{TOP_50_GLOBAL_PLAYLIST_ID}/tracks",
                headers={'Authorization': f'Bearer {access_token}'},
                params={'limit': limit}
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
    """Добавить треки в плейлист (до 100 URI за запрос по правилам Spotify)."""
    # Разбить на чанки — лимит API 100 треков за POST
    chunk_size = 100
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
        for i in range(0, len(track_uris), chunk_size):
            chunk = track_uris[i : i + chunk_size]
            try:
                response = await http.post(
                    f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json={"uris": chunk},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logger.error("401 Unauthorized при добавлении треков")
                else:
                    logger.error(
                        "Ошибка добавления треков: %s — %s",
                        e.response.status_code,
                        e.response.text,
                    )
                return False
            except httpx.TimeoutException as e:
                logger.error(
                    "Таймаут при добавлении треков (увеличен до 60 с). %r",
                    e,
                    exc_info=True,
                )
                return False
            except Exception as e:
                logger.error(
                    "Ошибка при добавлении треков: %r",
                    e,
                    exc_info=True,
                )
                return False
        logger.info(
            "Добавлено %s треков в плейлист %s",
            len(track_uris),
            playlist_id,
        )
        return True


async def unfollow_playlist(access_token: str, playlist_id: str) -> bool:
    """Удалить (unfollow) плейлист из библиотеки пользователя."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
        try:
            resp = await http.delete(
                f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/followers",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            logger.info("Плейлист %s удалён (unfollow)", playlist_id)
            return True
        except httpx.HTTPStatusError as e:
            logger.error("Ошибка unfollow: %s — %s", e.response.status_code, e.response.text)
            return False
        except Exception as e:
            logger.error("Ошибка при unfollow: %s", e)
            return False


async def make_playlist_with_top_tracks(telegram_user_id: int) -> Optional[str]:
    """Создать плейлист с топ-20 треками из Top 50 Global."""
    access_token = await ensure_valid_token(telegram_user_id)
    if not access_token:
        return None

    me = await get_me(access_token)
    if not me:
        access_token = await ensure_valid_token(telegram_user_id)
        if not access_token:
            return None
        me = await get_me(access_token)
        if not me:
            return None

    user_id = me['id']

    tracks = await get_top_tracks(access_token, limit=20)
    if not tracks:
        return None

    name = "Diploma MVP — Top 20"
    playlist = await create_playlist(access_token, user_id, name, public=False)
    if not playlist:
        return None

    playlist_id = playlist['id']
    track_uris = [track['uri'] for track in tracks]
    if not await add_tracks_to_playlist(access_token, playlist_id, track_uris):
        return None

    url = playlist.get('external_urls', {}).get('spotify')
    db.add_playlist(
        telegram_user_id,
        name,
        source="top20",
        spotify_playlist_id=playlist_id,
        url=url,
        tracks_count=len(track_uris),
    )
    return url


async def make_playlist_with_custom_track(
    telegram_user_id: int, query_or_link: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Создать приватный плейлист и добавить один трек.

    query_or_link — текст для поиска или ссылка/URI Spotify.

    Возвращает (playlist_url, track_label, error_message).
    При успехе error_message is None.
    """
    access_token = await ensure_valid_token(telegram_user_id)
    if not access_token:
        return None, None, "Нет доступа к аккаунту (переподключитесь: /disconnect → /connect)."

    me = await get_me(access_token)
    if not me:
        return None, None, "Не удалось получить профиль Spotify."

    user_id = me["id"]

    uri = parse_track_uri_from_text(query_or_link)
    track_label: Optional[str] = None

    if uri:
        tid = uri.replace("spotify:track:", "", 1)
        info = await get_track_by_id(access_token, tid)
        track_label = (
            f"{info['artists']} — {info['name']}" if info else uri
        )
    else:
        found = await search_first_track(access_token, query_or_link)
        if not found:
            return None, None, "Трек не найден. Уточните запрос или пришлите ссылку на трек Spotify."
        uri = found["uri"]
        track_label = f"{found['artists']} — {found['name']}"

    playlist = await create_playlist(
        access_token,
        user_id,
        "Diploma MVP — Custom track",
        public=False,
    )
    if not playlist:
        return None, None, "Не удалось создать плейлист."

    playlist_id = playlist["id"]
    if not await add_tracks_to_playlist(access_token, playlist_id, [uri]):
        return None, track_label, "Плейлист создан, но не удалось добавить трек."

    url = playlist.get("external_urls", {}).get("spotify")
    db.add_playlist(
        telegram_user_id,
        f"Diploma MVP — {track_label}" if track_label else "Diploma MVP — Custom track",
        source="track",
        spotify_playlist_id=playlist_id,
        url=url,
        prompt=query_or_link,
        tracks_count=1,
    )
    return url, track_label, None


async def make_playlist_from_ai(
    telegram_user_id: int,
    ai_tracks: List[Dict[str, str]],
    playlist_name: str = "AI Playlist",
) -> Tuple[Optional[str], int, int, Optional[str]]:
    """Создать плейлист из списка треков, сгенерированных ИИ (одним шагом).

    Оставлено для обратной совместимости. Предпочитаемый сценарий —
    `resolve_ai_tracks_to_uris` + предпросмотр + `create_playlist_from_uris`.
    """
    resolved, total, resolve_err = await resolve_ai_tracks_to_uris(
        telegram_user_id, ai_tracks
    )
    if resolve_err:
        return None, 0, total, resolve_err
    if not resolved:
        return None, 0, total, "Не удалось найти ни одного трека на Spotify."

    uris = [r["uri"] for r in resolved]
    url, playlist_id, err = await create_playlist_from_uris(
        telegram_user_id, uris, playlist_name,
        source="ai",
    )
    found = len(resolved)
    if err:
        return None, found, total, err
    return url, found, total, None


async def resolve_ai_tracks_to_uris(
    telegram_user_id: int,
    ai_tracks: List[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], int, Optional[str]]:
    """Резолвит треки {title, artist} в список {uri, label} через поиск Spotify.

    Возвращает (resolved, total, error). Сам плейлист не создаёт.
    """
    access_token = await ensure_valid_token(telegram_user_id)
    if not access_token:
        return [], len(ai_tracks), (
            "Нет доступа к аккаунту (переподключитесь: /disconnect → /connect)."
        )

    me = await get_me(access_token)
    if not me:
        return [], len(ai_tracks), "Не удалось получить профиль Spotify."

    resolved: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in ai_tracks:
        query = f"{item.get('artist', '')} {item.get('title', '')}".strip()
        if not query:
            continue
        found = await search_first_track(access_token, query, limit=1)
        if not found or found["uri"] in seen:
            continue
        seen.add(found["uri"])
        resolved.append(
            {
                "uri": found["uri"],
                "label": f"{found['artists']} — {found['name']}",
            }
        )
    return resolved, len(ai_tracks), None


async def create_playlist_from_uris(
    telegram_user_id: int,
    uris: List[str],
    playlist_name: str,
    *,
    source: str = "ai",
    prompt: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Создать плейлист из готового списка URI. Записывает факт в историю.

    Возвращает (playlist_url, playlist_id, error).
    """
    if not uris:
        return None, None, "Пустой список треков."

    access_token = await ensure_valid_token(telegram_user_id)
    if not access_token:
        return None, None, (
            "Нет доступа к аккаунту (переподключитесь: /disconnect → /connect)."
        )

    me = await get_me(access_token)
    if not me:
        return None, None, "Не удалось получить профиль Spotify."

    user_id = me["id"]
    playlist = await create_playlist(access_token, user_id, playlist_name, public=False)
    if not playlist:
        return None, None, "Не удалось создать плейлист."

    playlist_id = playlist["id"]
    if not await add_tracks_to_playlist(access_token, playlist_id, uris):
        return None, playlist_id, "Плейлист создан, но не удалось добавить треки."

    url = playlist.get("external_urls", {}).get("spotify")
    db.add_playlist(
        telegram_user_id,
        playlist_name,
        source=source,
        spotify_playlist_id=playlist_id,
        url=url,
        prompt=prompt,
        tracks_count=len(uris),
    )
    return url, playlist_id, None
