"""Единая точка входа: FastAPI (OAuth callback) + Telegram-бот + Mini App API."""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from telegram import Update
from telegram.ext import Application

from app.logging_config import setup_logging
from app.config import settings
from app.storage import db
from app.spotify import oauth
from app.spotify import client as spotify_client
from app.spotify.client import get_me
from app.ai.deepseek import generate_playlist
from app.webapp_auth import validate_init_data
from app.bot import _register_handlers

setup_logging()


def _require_real_env():
    """Понятная ошибка, если в .env остались плейсхолдеры или пустые значения."""
    t = settings.telegram_bot_token.strip()
    c = settings.spotify_client_id.strip()
    if not t or t == "your_telegram_bot_token_here":
        raise RuntimeError(
            "В файле .env в корне проекта задайте TELEGRAM_BOT_TOKEN (токен от @BotFather). "
            "Сохраните файл на диске и перезапустите сервер."
        )
    if not c or c == "your_spotify_client_id_here":
        raise RuntimeError(
            "В файле .env задайте SPOTIFY_CLIENT_ID из Spotify Developer Dashboard. "
            "Сохраните файл и перезапустите сервер."
        )


_require_real_env()

logger = logging.getLogger(__name__)

_tg_app: Application | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """При старте — Telegram-бот, при остановке — корректно гасим."""
    global _tg_app

    logger.info("Redirect URI: %s", settings.spotify_redirect_uri)

    db.init_db()

    _tg_app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(20)
        .build()
    )
    _register_handlers(_tg_app)

    await _tg_app.initialize()
    await _tg_app.start()
    await _tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Telegram-бот запущен (polling)")

    yield

    logger.info("Останавливаю Telegram-бот…")
    await _tg_app.updater.stop()
    await _tg_app.stop()
    await _tg_app.shutdown()
    logger.info("Telegram-бот остановлен")


app = FastAPI(title="Spotify OAuth Callback", lifespan=lifespan)


# ─── HTTP endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


SUCCESS_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Авторизация успешна</title>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         display:flex;justify-content:center;align-items:center;height:100vh;
         margin:0;background:#f5f5f5}}
    .box{{text-align:center;padding:40px;background:#fff;border-radius:12px;
         box-shadow:0 2px 10px rgba(0,0,0,.1);max-width:400px;margin:16px}}
    h1{{color:#1DB954;margin-bottom:12px}}
    p{{color:#666;margin-bottom:20px}}
    .btn{{display:inline-block;background:#1DB954;color:#fff;text-decoration:none;
         padding:12px 24px;border-radius:8px;font-weight:600;font-size:16px}}
    .btn:hover{{background:#1aa34a}}
  </style>
</head>
<body>
  <div class="box">
    <h1>&#10003; Авторизация успешна!</h1>
    <p>Spotify подключён. Вы уже получили уведомление в Telegram.</p>
    <a class="btn" href="https://t.me/{bot_username}">Открыть бота</a>
  </div>
</body>
</html>"""


@app.get("/callback")
async def callback(request: Request):
    """OAuth callback от Spotify."""
    error = request.query_params.get("error")
    if error:
        logger.error("Spotify вернул ошибку: %s", error)
        raise HTTPException(status_code=400, detail=f"Spotify error: {error}")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    success, tg_user_id, spotify_user_id, error_msg = await oauth.process_oauth_callback(code, state)
    if not success:
        raise HTTPException(status_code=400, detail=error_msg)

    # Отправить уведомление в Telegram
    bot_username = ""
    if _tg_app:
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            me = await _tg_app.bot.get_me()
            bot_username = me.username or ""
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 Создать AI-плейлист", callback_data="menu:ai")],
                [InlineKeyboardButton("🔥 Топ-20 Global", callback_data="menu:top20")],
                [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
            ])
            await _tg_app.bot.send_message(
                chat_id=tg_user_id,
                text=(
                    "✅ <b>Spotify подключён!</b>\n\n"
                    f"Spotify ID: <code>{spotify_user_id}</code>\n\n"
                    "Теперь можно создавать плейлисты!"
                ),
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Не удалось отправить уведомление в Telegram: %s", e)

    return HTMLResponse(content=SUCCESS_HTML.format(bot_username=bot_username))


# ─── Mini App API ────────────────────────────────────────────────────────────

def _get_tg_user_id(x_telegram_init_data: str = Header(None)) -> int | None:
    """Извлечь telegram_user_id из заголовка X-Telegram-Init-Data."""
    if not x_telegram_init_data:
        return None
    data = validate_init_data(x_telegram_init_data)
    if not data:
        return None
    return data.get("telegram_user_id")


def _require_user(x_telegram_init_data: str = Header(None)) -> int:
    uid = _get_tg_user_id(x_telegram_init_data)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid Telegram auth")
    return uid


@app.get("/api/me")
async def api_me(x_telegram_init_data: str = Header(None)):
    uid = _require_user(x_telegram_init_data)
    user = db.get_user(uid)
    if not user:
        return {"connected": False}
    return {
        "connected": True,
        "spotify_user_id": user["spotify_user_id"],
        "token_expires_at": user["token_expires_at"],
    }


@app.get("/api/history")
async def api_history(x_telegram_init_data: str = Header(None)):
    uid = _require_user(x_telegram_init_data)
    items = db.list_playlists(uid, limit=20)
    return {"playlists": items}


@app.post("/api/generate")
async def api_generate(request: Request, x_telegram_init_data: str = Header(None)):
    uid = _require_user(x_telegram_init_data)
    body = await request.json()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Empty prompt")

    if not db.get_user(uid):
        raise HTTPException(status_code=403, detail="Spotify not connected")

    ai_tracks = await generate_playlist(prompt)
    if not ai_tracks:
        return {"tracks": [], "total": 0, "error": "AI не смог подобрать треки"}

    resolved, total, err = await spotify_client.resolve_ai_tracks_to_uris(uid, ai_tracks)
    if err:
        return {"tracks": [], "total": total, "error": err}

    return {"tracks": resolved, "total": total, "prompt": prompt}


@app.post("/api/create-playlist")
async def api_create_playlist(request: Request, x_telegram_init_data: str = Header(None)):
    uid = _require_user(x_telegram_init_data)
    body = await request.json()
    uris = body.get("uris", [])
    name = body.get("name", "CatchTheWave Playlist")
    prompt = body.get("prompt")
    source = body.get("source", "ai")

    if not uris:
        raise HTTPException(status_code=400, detail="No tracks")

    url, pid, err = await spotify_client.create_playlist_from_uris(
        uid, uris, name, source=source, prompt=prompt,
    )
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"url": url, "playlist_id": pid}


@app.get("/api/top20")
async def api_top20(x_telegram_init_data: str = Header(None)):
    uid = _require_user(x_telegram_init_data)
    access_token = await spotify_client.ensure_valid_token(uid)
    if not access_token:
        raise HTTPException(status_code=403, detail="Spotify not connected")
    tracks = await spotify_client.get_top_tracks(access_token, limit=20)
    return {"tracks": [{"uri": t["uri"], "label": f"{t['artists']} — {t['name']}"} for t in tracks]}


@app.post("/api/search")
async def api_search(request: Request, x_telegram_init_data: str = Header(None)):
    uid = _require_user(x_telegram_init_data)
    body = await request.json()
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Empty query")

    access_token = await spotify_client.ensure_valid_token(uid)
    if not access_token:
        raise HTTPException(status_code=403, detail="Spotify not connected")

    tracks = await spotify_client.search_tracks(access_token, query, limit=10)
    return {"tracks": tracks}


@app.delete("/api/playlist/{playlist_id}")
async def api_delete_playlist(playlist_id: int, x_telegram_init_data: str = Header(None)):
    uid = _require_user(x_telegram_init_data)
    playlist = db.get_playlist(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if playlist["telegram_user_id"] != uid:
        raise HTTPException(status_code=403, detail="Not your playlist")

    spotify_pid = playlist.get("spotify_playlist_id")
    if spotify_pid:
        access_token = await spotify_client.ensure_valid_token(uid)
        if access_token:
            await spotify_client.unfollow_playlist(access_token, spotify_pid)

    db.delete_playlist(playlist_id)
    return {"ok": True}


@app.get("/api/connect-url")
async def api_connect_url(x_telegram_init_data: str = Header(None)):
    uid = _require_user(x_telegram_init_data)
    auth_url = oauth.get_authorization_url(uid)
    return {"url": auth_url}


# Mount Mini App static files
_MINIAPP_DIR = Path(__file__).parent / "miniapp"
if _MINIAPP_DIR.is_dir():
    app.mount("/miniapp", StaticFiles(directory=_MINIAPP_DIR, html=True), name="miniapp")


# ─── Standalone ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="info")
