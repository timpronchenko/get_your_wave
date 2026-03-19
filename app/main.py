"""FastAPI сервер для обработки OAuth callback."""
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
import uvicorn

from app.config import settings
from app.storage import db
from app.spotify import oauth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Spotify OAuth Callback")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/callback")
async def callback(request: Request):
    """Обработка OAuth callback от Spotify."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    
    if error:
        logger.error(f"Ошибка авторизации: {error}")
        return {"error": error, "message": "Авторизация отклонена или произошла ошибка"}
    
    if not code or not state:
        logger.error("Отсутствуют code или state в callback")
        raise HTTPException(status_code=400, detail="Missing code or state")
    
    # Обменять code на токены
    tokens = await oauth.exchange_code_for_tokens(code, state)
    if not tokens:
        logger.error("Не удалось обменять code на токены")
        raise HTTPException(status_code=400, detail="Failed to exchange code for tokens")
    
    # Получить telegram_user_id из state
    pkce_data = oauth.get_pkce_data(state)
    if not pkce_data:
        logger.error(f"Не найден telegram_user_id для state={state}")
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    
    telegram_user_id = pkce_data['telegram_user_id']
    
    # Получить информацию о пользователе Spotify
    from app.spotify.client import get_me
    import time
    
    access_token = tokens['access_token']
    me = await get_me(access_token)
    if not me:
        logger.error("Не удалось получить информацию о пользователе Spotify")
        raise HTTPException(status_code=500, detail="Failed to get user info")
    
    spotify_user_id = me['id']
    refresh_token = tokens['refresh_token']
    expires_in = tokens.get('expires_in', 3600)
    expires_at = int(time.time()) + expires_in
    
    # Сохранить пользователя
    db.save_user(
        telegram_user_id=telegram_user_id,
        spotify_user_id=spotify_user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at
    )
    
    logger.info(f"Пользователь авторизован: telegram_user_id={telegram_user_id}, spotify_user_id={spotify_user_id}")
    
    # Вернуть простую HTML страницу с сообщением
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Авторизация успешна</title>
        <meta charset="utf-8">
        <style>
            body {
                font-family: Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
                background: #f5f5f5;
            }
            .container {
                text-align: center;
                padding: 40px;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 { color: #1DB954; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>✓ Авторизация успешна!</h1>
            <p>Вы можете закрыть это окно и вернуться в Telegram.</p>
            <p>Используйте команду /make_playlist для создания плейлиста.</p>
        </div>
    </body>
    </html>
    """


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
