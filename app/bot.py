"""Telegram бот для работы со Spotify."""
import logging
import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.config import settings
from app.storage import db
from app.spotify import oauth, client

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user = update.effective_user
    logger.info(f"/start от пользователя {user.id}")
    
    db_user = db.get_user(user.id)
    status = "✅ Подключено" if db_user else "❌ Не подключено"
    
    text = f"""
🎵 <b>Spotify Playlist Bot</b>

Статус: {status}

<b>Команды:</b>
/connect - Подключить Spotify
/status - Проверить статус подключения
/make_playlist - Создать плейлист с топ-20 треками
/disconnect - Отключить Spotify

<i>Для начала используйте /connect</i>
    """
    
    await update.message.reply_text(text, parse_mode='HTML')


async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /connect."""
    user = update.effective_user
    logger.info(f"/connect от пользователя {user.id}")
    
    # Проверить, не подключен ли уже
    db_user = db.get_user(user.id)
    if db_user:
        await update.message.reply_text(
            "✅ Вы уже подключены к Spotify!\n"
            "Используйте /status для проверки или /disconnect для отключения."
        )
        return
    
    # Сгенерировать ссылку авторизации
    auth_url = oauth.get_authorization_url(user.id)
    
    text = f"""
🔗 <b>Подключение к Spotify</b>

Нажмите на ссылку ниже, чтобы авторизоваться:

<a href="{auth_url}">Авторизоваться в Spotify</a>

<b>⚠️ Важно:</b>
• Откройте ссылку на <b>этом же ноутбуке</b> (где запущен бот)
• После авторизации вы будете перенаправлены на 127.0.0.1:8000
• Ссылка действительна 10 минут
    """
    
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=False)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status."""
    user = update.effective_user
    logger.info(f"/status от пользователя {user.id}")
    
    db_user = db.get_user(user.id)
    
    if not db_user:
        await update.message.reply_text(
            "❌ <b>Не подключено</b>\n\n"
            "Используйте /connect для подключения к Spotify.",
            parse_mode='HTML'
        )
        return
    
    import time
    expires_at = db_user['token_expires_at']
    expires_in = expires_at - int(time.time())
    
    if expires_in > 0:
        expires_str = f"через {expires_in // 3600}ч {(expires_in % 3600) // 60}м"
        token_status = "✅ Активен"
    else:
        expires_str = "истёк"
        token_status = "❌ Истёк (будет обновлён автоматически)"
    
    updated_at = datetime.fromisoformat(db_user['updated_at'])
    
    text = f"""
✅ <b>Подключено</b>

<b>Spotify User ID:</b> {db_user['spotify_user_id']}
<b>Токен:</b> {token_status}
<b>Истекает:</b> {expires_str}
<b>Обновлено:</b> {updated_at.strftime('%Y-%m-%d %H:%M:%S')}
    """
    
    await update.message.reply_text(text, parse_mode='HTML')


async def make_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /make_playlist."""
    user = update.effective_user
    logger.info(f"/make_playlist от пользователя {user.id}")
    
    # Проверить подключение
    db_user = db.get_user(user.id)
    if not db_user:
        await update.message.reply_text(
            "❌ Вы не подключены к Spotify.\n"
            "Используйте /connect для подключения."
        )
        return
    
    # Отправить сообщение о начале работы
    msg = await update.message.reply_text("🔄 Создаю плейлист...")
    
    try:
        # Создать плейлист с топ-треками
        playlist_url = await client.make_playlist_with_top_tracks(user.id)
        
        if playlist_url:
            text = f"""
✅ <b>Плейлист создан!</b>

🎵 <b>Diploma MVP — Top 20</b>

<a href="{playlist_url}">Открыть в Spotify</a>

В плейлист добавлены первые 20 треков из Top 50 Global.
            """
            await msg.edit_text(text, parse_mode='HTML', disable_web_page_preview=False)
        else:
            await msg.edit_text(
                "❌ Не удалось создать плейлист.\n"
                "Проверьте подключение и попробуйте снова.\n"
                "Или используйте /disconnect и /connect для переподключения."
            )
    except Exception as e:
        logger.error(f"Ошибка при создании плейлиста: {e}", exc_info=True)
        await msg.edit_text(
            f"❌ Произошла ошибка: {str(e)}\n"
            "Попробуйте позже или переподключитесь через /disconnect и /connect."
        )


async def disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /disconnect."""
    user = update.effective_user
    logger.info(f"/disconnect от пользователя {user.id}")
    
    deleted = db.delete_user(user.id)
    
    if deleted:
        await update.message.reply_text(
            "✅ <b>Отключено</b>\n\n"
            "Ваши данные удалены. Используйте /connect для повторного подключения.",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            "ℹ️ Вы не были подключены к Spotify."
        )


def main():
    """Запуск Telegram бота."""
    # Инициализировать БД
    db.init_db()
    
    # Создать приложение
    application = Application.builder().token(settings.telegram_bot_token).build()
    
    # Зарегистрировать обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("connect", connect))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("make_playlist", make_playlist))
    application.add_handler(CommandHandler("disconnect", disconnect))
    
    logger.info("Telegram бот запущен")
    
    # Запустить polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
