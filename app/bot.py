"""Telegram-бот: обработчики команд и inline-кнопки.

Функции-хендлеры импортируются из main.py (единый процесс).
Можно также запустить бот отдельно: python -m app.bot  (но тогда
callback-сервер должен быть в том же процессе, иначе PKCE state не расшарится).
"""
import html
import logging
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.error import NetworkError, TimedOut, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings
from app.storage import db
from app.spotify import oauth, client
from app.ai.deepseek import generate_playlist

logger = logging.getLogger(__name__)

_MODE_KEY = "input_mode"
_MODE_AI = "ai_playlist"
_MODE_ADD_SONG = "add_song"

_PROMPT_MSG_KEY = "prompt_message_id"
_AI_PREVIEW_KEY = "ai_preview"
_TOP20_PREVIEW_KEY = "top20_preview"
_TRACK_PREVIEW_KEY = "track_preview"

_AI_PRESETS: dict[str, tuple[str, str]] = {
    "chill": (
        "🛋 Чилл",
        "Спокойная вечерняя музыка для отдыха и расслабления, лёгкие биты, мечтательная атмосфера.",
    ),
    "workout": (
        "🏋️ Тренировка",
        "Энергичная музыка для тренировки, мощные биты, драйв, электроника и хип-хоп.",
    ),
    "focus": (
        "🧠 Фокус",
        "Спокойная инструментальная музыка для концентрации и работы: lo-fi, пост-рок, эмбиент.",
    ),
    "party": (
        "🎉 Вечеринка",
        "Танцевальная музыка для вечеринки, популярные хиты, высокий темп, диско и хаус.",
    ),
    "ru_rap": (
        "🇷🇺 Русский рэп",
        "Современный русский рэп: значимые исполнители, мрачный и лиричный вайб, разнообразные продюсеры.",
    ),
    "retro_80": (
        "🕹 Ретро 80-х",
        "Хиты 80-х: synthwave, new wave, поп-рок, ретро-звучание.",
    ),
}


def _ai_preset_keyboard() -> InlineKeyboardMarkup:
    """Сетка быстрых пресетов для AI-меню (2 колонки)."""
    buttons: list[list[InlineKeyboardButton]] = []
    items = list(_AI_PRESETS.items())
    for i in range(0, len(items), 2):
        row = [
            InlineKeyboardButton(label, callback_data=f"preset:{pid}")
            for pid, (label, _) in items[i : i + 2]
        ]
        buttons.append(row)
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="menu:back")])
    return InlineKeyboardMarkup(buttons)


def _ai_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать плейлист", callback_data="ai:create")],
        [InlineKeyboardButton("🔁 Сгенерировать заново", callback_data="ai:regen")],
        [InlineKeyboardButton("❌ Отмена", callback_data="menu:back")],
    ])


def _format_preview(prompt: str, resolved: list[dict], total: int) -> str:
    """Отрисовать предпросмотр AI-плейлиста (HTML)."""
    safe_prompt = html.escape(prompt[:200])
    header = (
        "🤖 <b>Предварительный просмотр</b>\n"
        f"<b>Запрос:</b> <i>{safe_prompt}</i>\n"
        f"<b>Найдено в Spotify:</b> {len(resolved)} из {total}\n\n"
    )
    max_lines = 25
    shown = resolved[:max_lines]
    lines = [
        f"{i}. {html.escape(t['label'])}"
        for i, t in enumerate(shown, start=1)
    ]
    body = "\n".join(lines)
    if len(resolved) > max_lines:
        body += f"\n…и ещё {len(resolved) - max_lines}"
    footer = "\n\nСохранить этот плейлист в вашем Spotify?"
    return header + body + footer


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    connected = db.get_user(user_id) is not None

    buttons = [
        [InlineKeyboardButton("🤖 Создать AI-плейлист", callback_data="menu:ai")],
        [InlineKeyboardButton("🔥 Топ-20 Global", callback_data="menu:top20")],
        [InlineKeyboardButton("🎵 Добавить песню", callback_data="menu:add_song")],
        [
            InlineKeyboardButton("🗂 История", callback_data="menu:history"),
            InlineKeyboardButton("📊 Статус", callback_data="menu:status"),
        ],
    ]

    # Mini App button (Telegram требует HTTPS для WebApp)
    if settings.base_url.startswith("https://"):
        webapp_url = f"{settings.base_url}/miniapp/"
        buttons.append([
            InlineKeyboardButton(
                "🌐 Открыть Mini App",
                web_app=WebAppInfo(url=webapp_url),
            )
        ])

    if connected:
        buttons.append(
            [InlineKeyboardButton("🚪 Отключить Spotify", callback_data="menu:disconnect")]
        )
    else:
        buttons.append(
            [InlineKeyboardButton("🔗 Подключить Spotify", callback_data="menu:connect")]
        )

    return InlineKeyboardMarkup(buttons)


_BACK_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")]
])


async def _safe_send(bot, chat_id: int, text: str, **kwargs) -> int | None:
    """Send a message, swallowing Telegram network errors. Returns message_id or None."""
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return msg.message_id
    except (TimedOut, NetworkError) as exc:
        logger.warning("Telegram send failed (chat %s): %s", chat_id, exc)
        return None
    except TelegramError as exc:
        logger.error("Telegram send error (chat %s): %s", chat_id, exc)
        return None


async def _safe_edit(bot, chat_id: int, message_id: int, text: str, **kwargs) -> bool:
    """Edit a message, swallowing errors. Returns True on success."""
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, **kwargs)
        return True
    except TelegramError as exc:
        logger.warning("Telegram edit failed (chat %s, msg %s): %s", chat_id, message_id, exc)
        return False


async def _safe_delete(bot, chat_id: int, message_id: int):
    """Delete a message, ignoring errors."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass




# ─── /start ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(_MODE_KEY, None)
    context.user_data.pop(_PROMPT_MSG_KEY, None)
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info("/start от %s", user.id)

    db_user = db.get_user(user.id)
    status_text = "✅ Подключено" if db_user else "❌ Не подключено"

    await _safe_send(
        context.bot, chat_id,
        f"🎵 <b>Spotify Playlist Bot</b>\n\n"
        f"Статус: {status_text}\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=_main_menu_keyboard(user.id),
    )


# ─── Callback-кнопки главного меню ───────────────────────────────────────────

async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    chat_id = query.message.chat_id
    data = query.data or ""
    action = data.removeprefix("menu:")

    context.user_data.pop(_MODE_KEY, None)

    if action == "back":
        context.user_data.pop(_AI_PREVIEW_KEY, None)
        db_user = db.get_user(user.id)
        status_text = "✅ Подключено" if db_user else "❌ Не подключено"
        try:
            await query.edit_message_text(
                f"🎵 <b>Spotify Playlist Bot</b>\n\n"
                f"Статус: {status_text}\n\n"
                "Выберите действие:",
                parse_mode="HTML",
                reply_markup=_main_menu_keyboard(user.id),
            )
        except TelegramError:
            pass
        return

    # ── Connect ──────────────────────────────────────────────────────────
    if action == "connect":
        if db.get_user(user.id):
            try:
                await query.edit_message_text("✅ Вы уже подключены!", reply_markup=_BACK_KB)
            except TelegramError:
                pass
            return

        auth_url = oauth.get_authorization_url(user.id)

        try:
            await query.edit_message_text(
                "🔗 <b>Подключение к Spotify</b>\n\n"
                f'<a href="{auth_url}">Авторизоваться в Spotify</a>\n\n'
                "<b>Инструкция:</b>\n"
                "1. Откройте ссылку выше\n"
                "2. Войдите в Spotify и разрешите доступ\n"
                "3. <b>С компьютера</b> — бот автоматически пришлёт подтверждение\n"
                "4. <b>С телефона</b> — после авторизации скопируйте URL из адресной строки "
                "и вставьте его в этот чат\n\n"
                "⏳ Ссылка действительна 10 минут",
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Проверить подключение", callback_data="menu:status")],
                    [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
                ]),
            )
        except TelegramError:
            pass
        return

    # ── Disconnect ───────────────────────────────────────────────────────
    if action == "disconnect":
        if db.delete_user(user.id):
            text = "✅ <b>Отключено.</b> Данные удалены."
        else:
            text = "ℹ️ Вы и так не были подключены."
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=_BACK_KB)
        except TelegramError:
            pass
        return

    # ── Status ───────────────────────────────────────────────────────────
    if action == "status":
        db_user = db.get_user(user.id)
        if not db_user:
            try:
                await query.edit_message_text(
                    "❌ <b>Не подключено</b>\nПодключите Spotify через меню.",
                    parse_mode="HTML", reply_markup=_BACK_KB,
                )
            except TelegramError:
                pass
            return

        expires_at = db_user["token_expires_at"]
        remaining = expires_at - int(time.time())

        if remaining > 0:
            token_line = f"✅ Активен (ещё {remaining // 60} мин)"
        else:
            token_line = "⏳ Истёк (обновится автоматически)"

        updated = datetime.fromisoformat(db_user["updated_at"])
        try:
            await query.edit_message_text(
                "✅ <b>Подключено</b>\n\n"
                f"<b>Spotify ID:</b> {db_user['spotify_user_id']}\n"
                f"<b>Токен:</b> {token_line}\n"
                f"<b>Обновлено:</b> {updated:%Y-%m-%d %H:%M:%S}",
                parse_mode="HTML", reply_markup=_BACK_KB,
            )
        except TelegramError:
            pass
        return

    # ── History ──────────────────────────────────────────────────────────
    if action == "history":
        items = db.list_playlists(user.id, limit=5)
        if not items:
            try:
                await query.edit_message_text(
                    "🗂 <b>История плейлистов пуста.</b>\n\n"
                    "Создайте свой первый плейлист — и он появится здесь.",
                    parse_mode="HTML", reply_markup=_BACK_KB,
                )
            except TelegramError:
                pass
            return

        lines = ["🗂 <b>Последние плейлисты</b>\n"]
        buttons: list[list[InlineKeyboardButton]] = []
        for it in items:
            name = html.escape(it["name"] or "Без названия")
            created = (it["created_at"] or "")[:19].replace("T", " ")
            count = it["tracks_count"]
            count_str = f" • {count} тр." if count else ""
            url = it["url"]
            prompt = it.get("prompt")
            if url:
                lines.append(f'• <a href="{url}">{name}</a>\n  <i>{created}{count_str}</i>')
            else:
                lines.append(f"• {name}\n  <i>{created}{count_str}</i>")
            if prompt and it.get("source") == "ai":
                safe_prompt = html.escape(prompt[:80])
                lines.append(f'  <b>Запрос:</b> <i>{safe_prompt}</i>')
                buttons.append([
                    InlineKeyboardButton(
                        f"🔁 Повторить: {prompt[:30]}{'…' if len(prompt) > 30 else ''}",
                        callback_data=f"hist:{it['id']}",
                    )
                ])
        buttons.append([InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")])
        try:
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except TelegramError:
            pass
        return

    # ── Top-20 ───────────────────────────────────────────────────────────
    if action == "top20":
        if not db.get_user(user.id):
            try:
                await query.edit_message_text(
                    "❌ Сначала подключите Spotify.", reply_markup=_BACK_KB,
                )
            except TelegramError:
                pass
            return

        try:
            await query.edit_message_text("🔄 Загружаю треки из глобального чарта…")
        except TelegramError:
            pass

        try:
            access_token = await client.ensure_valid_token(user.id)
            if not access_token:
                await query.edit_message_text(
                    "❌ Нет доступа к аккаунту (переподключитесь).", reply_markup=_BACK_KB,
                )
                return
            tracks = await client.get_top_tracks(access_token, limit=20)
            if not tracks:
                await query.edit_message_text(
                    "❌ Не удалось загрузить треки.", reply_markup=_BACK_KB,
                )
                return

            context.user_data[_TOP20_PREVIEW_KEY] = tracks
            lines = [
                "🔥 <b>Предварительный просмотр — Top 20 Global</b>\n"
                f"<b>Треков:</b> {len(tracks)}\n"
            ]
            for i, t in enumerate(tracks, start=1):
                lines.append(f"{i}. {html.escape(t['artists'])} — {html.escape(t['name'])}")
            lines.append("\nСохранить этот плейлист в вашем Spotify?")
            preview_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Создать плейлист", callback_data="top20:create")],
                [InlineKeyboardButton("❌ Отмена", callback_data="menu:back")],
            ])
            await query.edit_message_text(
                "\n".join(lines), parse_mode="HTML",
                disable_web_page_preview=True, reply_markup=preview_kb,
            )
        except Exception as exc:
            logger.error("top20 preview error: %s", exc, exc_info=True)
            try:
                await query.edit_message_text(f"❌ Ошибка: {exc}", reply_markup=_BACK_KB)
            except TelegramError:
                pass
        return

    # ── AI playlist ──────────────────────────────────────────────────────
    if action == "ai":
        if not db.get_user(user.id):
            try:
                await query.edit_message_text(
                    "❌ Сначала подключите Spotify.", reply_markup=_BACK_KB,
                )
            except TelegramError:
                pass
            return

        context.user_data[_MODE_KEY] = _MODE_AI
        try:
            await query.edit_message_text(
                "🤖 <b>AI-плейлист</b>\n\n"
                "Выберите готовый <b>пресет настроения/жанра</b> ниже "
                "или опишите плейлист своими словами одним сообщением.\n\n"
                "<b>Примеры запроса:</b>\n"
                "• <i>Спокойная музыка для вечера</i>\n"
                "• <i>Энергичные треки для тренировки</i>\n"
                "• <i>Что-то похожее на The Weeknd и Daft Punk</i>",
                parse_mode="HTML",
                reply_markup=_ai_preset_keyboard(),
            )
            context.user_data[_PROMPT_MSG_KEY] = query.message.message_id
        except TelegramError:
            pass
        return

    # ── Add song ─────────────────────────────────────────────────────────
    if action == "add_song":
        if not db.get_user(user.id):
            try:
                await query.edit_message_text(
                    "❌ Сначала подключите Spotify.", reply_markup=_BACK_KB,
                )
            except TelegramError:
                pass
            return

        context.user_data[_MODE_KEY] = _MODE_ADD_SONG
        try:
            await query.edit_message_text(
                "🎵 <b>Добавить песню</b>\n\n"
                "Отправьте одним из способов:\n"
                "• <b>Название и исполнитель:</b> <i>The Weeknd Blinding Lights</i>\n"
                "• <b>Ссылка:</b> <i>https://open.spotify.com/track/...</i>\n"
                "• <b>URI:</b> <i>spotify:track:...</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="menu:back")]
                ]),
            )
            context.user_data[_PROMPT_MSG_KEY] = query.message.message_id
        except TelegramError:
            pass
        return


# ─── Обработка вставленного callback URL ─────────────────────────────────────

async def _handle_pasted_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обработать вставленный URL с OAuth callback (авторизация с телефона без ngrok)."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    try:
        parsed = urlparse(text.strip())
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
    except Exception:
        code = state = None

    if not code or not state:
        return

    # Удаляем сообщение с auth code
    await _safe_delete(context.bot, chat_id, update.message.message_id)

    status_msg_id = await _safe_send(context.bot, chat_id, "🔄 Обрабатываю авторизацию…")

    success, tg_user_id, spotify_user_id, error = await oauth.process_oauth_callback(code, state)

    if not success:
        if status_msg_id:
            await _safe_edit(
                context.bot, chat_id, status_msg_id,
                f"❌ Ошибка авторизации: {error}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Попробовать снова", callback_data="menu:connect")],
                    [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
                ]),
            )
        return

    if tg_user_id != user.id:
        if status_msg_id:
            await _safe_edit(
                context.bot, chat_id, status_msg_id,
                "❌ Эта ссылка была создана для другого пользователя.",
                reply_markup=_BACK_KB,
            )
        return

    if status_msg_id:
        await _safe_edit(
            context.bot, chat_id, status_msg_id,
            "✅ <b>Spotify подключён!</b>\n\n"
            f"Spotify ID: <code>{spotify_user_id}</code>\n\n"
            "Теперь вы можете создавать плейлисты!",
            parse_mode="HTML",
            reply_markup=_main_menu_keyboard(user.id),
        )


# ─── Обработка текстовых сообщений (AI / Add Song) ──────────────────────────

async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    mode = context.user_data.get(_MODE_KEY)
    text = (update.message.text or "").strip()
    user_msg_id = update.message.message_id

    if not text:
        return

    # Проверяем, не является ли текст вставленным callback URL
    if "code=" in text and "state=" in text:
        await _handle_pasted_callback(update, context, text)
        return

    prompt_msg_id = context.user_data.pop(_PROMPT_MSG_KEY, None)

    if mode == _MODE_AI:
        context.user_data.pop(_MODE_KEY, None)
        await _safe_delete(context.bot, chat_id, user_msg_id)
        await _handle_ai_playlist(
            context.bot, chat_id, user.id, text, prompt_msg_id,
            user_data=context.user_data,
        )
        return

    if mode == _MODE_ADD_SONG:
        context.user_data.pop(_MODE_KEY, None)
        await _safe_delete(context.bot, chat_id, user_msg_id)
        await _handle_add_song_text(context.bot, chat_id, user.id, text, prompt_msg_id, user_data=context.user_data)
        return

    if db.get_user(user.id):
        await _safe_delete(context.bot, chat_id, user_msg_id)
        await _handle_ai_playlist(
            context.bot, chat_id, user.id, text, prompt_msg_id,
            user_data=context.user_data,
        )
    else:
        await _safe_send(
            context.bot, chat_id,
            "Подключите Spotify, чтобы начать. Нажмите /start",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Подключить Spotify", callback_data="menu:connect")]
            ]),
        )


async def _handle_ai_playlist(
    bot, chat_id: int, user_id: int, text: str, prompt_msg_id: int | None,
    *, user_data: dict | None = None,
):
    """Двухэтапный AI-флоу: запрос → DeepSeek → поиск → ПРЕДПРОСМОТР.

    Создание плейлиста делается уже после подтверждения (callback `ai:create`).
    """
    if not db.get_user(user_id):
        await _safe_send(bot, chat_id, "❌ Сначала подключите Spotify.", reply_markup=_BACK_KB)
        return

    if prompt_msg_id:
        ok = await _safe_edit(bot, chat_id, prompt_msg_id, "🤖 Подбираю треки, подождите…")
        status_msg_id = prompt_msg_id if ok else None
    else:
        status_msg_id = None
    if status_msg_id is None:
        status_msg_id = await _safe_send(bot, chat_id, "🤖 Подбираю треки, подождите…")

    retry_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Попробовать снова", callback_data="menu:ai")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
    ])

    try:
        ai_tracks = await generate_playlist(text)
    except Exception as exc:
        logger.error("DeepSeek error: %s", exc, exc_info=True)
        msg = (
            "❌ Ошибка при обращении к ИИ. Если вы в России — проверьте, "
            "включён ли VPN, и попробуйте ещё раз."
        )
        if status_msg_id:
            await _safe_edit(bot, chat_id, status_msg_id, msg, reply_markup=retry_kb)
        else:
            await _safe_send(bot, chat_id, msg, reply_markup=retry_kb)
        return

    if not ai_tracks:
        msg = "❌ ИИ не смог подобрать треки. Попробуйте переформулировать запрос."
        if status_msg_id:
            await _safe_edit(bot, chat_id, status_msg_id, msg, reply_markup=retry_kb)
        else:
            await _safe_send(bot, chat_id, msg, reply_markup=retry_kb)
        return

    if status_msg_id:
        await _safe_edit(
            bot, chat_id, status_msg_id,
            f"🔄 ИИ предложил {len(ai_tracks)} треков. Ищу их в Spotify…",
        )

    try:
        resolved, total, err = await client.resolve_ai_tracks_to_uris(user_id, ai_tracks)
    except Exception as exc:
        logger.error("resolve_ai_tracks_to_uris error: %s", exc, exc_info=True)
        msg = (
            "❌ Ошибка при поиске треков в Spotify. "
            "Если вы в России — убедитесь, что включён VPN."
        )
        if status_msg_id:
            await _safe_edit(bot, chat_id, status_msg_id, msg, reply_markup=retry_kb)
        else:
            await _safe_send(bot, chat_id, msg, reply_markup=retry_kb)
        return

    if err:
        if status_msg_id:
            await _safe_edit(bot, chat_id, status_msg_id, f"❌ {err}", reply_markup=retry_kb)
        else:
            await _safe_send(bot, chat_id, f"❌ {err}", reply_markup=retry_kb)
        return

    if not resolved:
        msg = (
            "❌ Не удалось найти ни одного предложенного трека на Spotify. "
            "Попробуйте уточнить запрос."
        )
        if status_msg_id:
            await _safe_edit(bot, chat_id, status_msg_id, msg, reply_markup=retry_kb)
        else:
            await _safe_send(bot, chat_id, msg, reply_markup=retry_kb)
        return

    if user_data is not None:
        user_data[_AI_PREVIEW_KEY] = {
            "prompt": text,
            "resolved": resolved,
            "total": total,
        }

    preview_text = _format_preview(text, resolved, total)
    kwargs = dict(
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=_ai_preview_keyboard(),
    )
    if status_msg_id:
        ok = await _safe_edit(bot, chat_id, status_msg_id, preview_text, **kwargs)
        if not ok:
            await _safe_send(bot, chat_id, preview_text, **kwargs)
    else:
        await _safe_send(bot, chat_id, preview_text, **kwargs)


# ─── Пресеты жанра / настроения ──────────────────────────────────────────────

async def on_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    data = query.data or ""
    pid = data.removeprefix("preset:")

    preset = _AI_PRESETS.get(pid)
    if not preset:
        return

    if not db.get_user(user.id):
        try:
            await query.edit_message_text(
                "❌ Сначала подключите Spotify.", reply_markup=_BACK_KB,
            )
        except TelegramError:
            pass
        return

    _, prompt_text = preset
    context.user_data.pop(_MODE_KEY, None)
    context.user_data.pop(_PROMPT_MSG_KEY, None)

    await _handle_ai_playlist(
        context.bot, chat_id, user.id, prompt_text, msg_id,
        user_data=context.user_data,
    )


# ─── Действия предпросмотра AI-плейлиста (ai:create / ai:regen) ─────────────

async def on_ai_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    action = (query.data or "").removeprefix("ai:")

    preview = context.user_data.get(_AI_PREVIEW_KEY)
    if not preview:
        try:
            await query.edit_message_text(
                "ℹ️ Сессия предпросмотра завершена. Создайте новый плейлист.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🤖 Создать AI-плейлист", callback_data="menu:ai")],
                    [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
                ]),
            )
        except TelegramError:
            pass
        return

    prompt_text = preview["prompt"]

    if action == "regen":
        await _handle_ai_playlist(
            context.bot, chat_id, user.id, prompt_text, msg_id,
            user_data=context.user_data,
        )
        return

    if action == "create":
        resolved = preview["resolved"]
        total = preview["total"]
        uris = [t["uri"] for t in resolved]
        playlist_name = (
            f"AI: {prompt_text[:40]}" if len(prompt_text) <= 40
            else f"AI: {prompt_text[:37]}…"
        )

        try:
            await query.edit_message_text("🔄 Создаю плейлист в Spotify…")
        except TelegramError:
            pass

        try:
            url, _pid, err = await client.create_playlist_from_uris(
                user.id, uris, playlist_name,
                source="ai", prompt=prompt_text,
            )
        except Exception as exc:
            logger.error("create_playlist_from_uris error: %s", exc, exc_info=True)
            await _safe_edit(
                context.bot, chat_id, msg_id,
                f"❌ Ошибка: {exc}", reply_markup=_BACK_KB,
            )
            return

        if err or not url:
            await _safe_edit(
                context.bot, chat_id, msg_id,
                f"❌ {err or 'Не удалось создать плейлист.'}",
                reply_markup=_BACK_KB,
            )
            return

        context.user_data.pop(_AI_PREVIEW_KEY, None)
        safe_name = html.escape(playlist_name)
        result_text = (
            "✅ <b>Плейлист создан!</b>\n\n"
            f"🤖 <b>{safe_name}</b>\n"
            f"Добавлено треков: {len(uris)} (из {total} предложенных)\n\n"
            f'<a href="{url}">Открыть в Spotify</a>'
        )
        result_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 Ещё один AI-плейлист", callback_data="menu:ai")],
            [InlineKeyboardButton("🗂 История", callback_data="menu:history")],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
        ])
        ok = await _safe_edit(
            context.bot, chat_id, msg_id, result_text,
            parse_mode="HTML", disable_web_page_preview=False, reply_markup=result_kb,
        )
        if not ok:
            await _safe_send(
                context.bot, chat_id, result_text,
                parse_mode="HTML", disable_web_page_preview=False, reply_markup=result_kb,
            )
        return


async def _handle_add_song_text(bot, chat_id: int, user_id: int, text: str, prompt_msg_id: int | None, *, user_data: dict | None = None):
    """Добавить песню: по ссылке/URI — превью, по тексту — показываем выбор."""
    if not db.get_user(user_id):
        await _safe_send(bot, chat_id, "❌ Сначала подключите Spotify.", reply_markup=_BACK_KB)
        return

    uri = client.parse_track_uri_from_text(text)
    if uri:
        if prompt_msg_id:
            ok = await _safe_edit(bot, chat_id, prompt_msg_id, "🔎 Ищу трек…")
            status_msg_id = prompt_msg_id if ok else None
        else:
            status_msg_id = await _safe_send(bot, chat_id, "🔎 Ищу трек…")

        try:
            access_token = await client.ensure_valid_token(user_id)
            if not access_token:
                msg = "❌ Нет доступа к аккаунту (переподключитесь)."
                if status_msg_id:
                    await _safe_edit(bot, chat_id, status_msg_id, msg, reply_markup=_BACK_KB)
                else:
                    await _safe_send(bot, chat_id, msg, reply_markup=_BACK_KB)
                return

            track_id = uri.rsplit(":", 1)[-1]
            track_info = await client.get_track_by_id(access_token, track_id)
            if not track_info:
                msg = "❌ Трек не найден."
                if status_msg_id:
                    await _safe_edit(bot, chat_id, status_msg_id, msg, reply_markup=_BACK_KB)
                else:
                    await _safe_send(bot, chat_id, msg, reply_markup=_BACK_KB)
                return

            label = f"{track_info.get('artists', 'Unknown')} — {track_info.get('name', 'Unknown')}"
            if user_data is not None:
                user_data[_TRACK_PREVIEW_KEY] = {"uri": uri, "label": label}

            preview_text = (
                "🎵 <b>Предварительный просмотр</b>\n\n"
                f"1. {html.escape(label)}\n\n"
                "Создать плейлист с этим треком?"
            )
            preview_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Создать плейлист", callback_data="track:create")],
                [InlineKeyboardButton("❌ Отмена", callback_data="menu:back")],
            ])
            if status_msg_id:
                await _safe_edit(bot, chat_id, status_msg_id, preview_text,
                                 parse_mode="HTML", reply_markup=preview_kb)
            else:
                await _safe_send(bot, chat_id, preview_text,
                                 parse_mode="HTML", reply_markup=preview_kb)
        except Exception as exc:
            logger.error("add_song preview error: %s", exc, exc_info=True)
            if status_msg_id:
                await _safe_edit(bot, chat_id, status_msg_id, f"❌ Ошибка: {exc}", reply_markup=_BACK_KB)
            else:
                await _safe_send(bot, chat_id, f"❌ Ошибка: {exc}", reply_markup=_BACK_KB)
        return

    access_token = await client.ensure_valid_token(user_id)
    if not access_token:
        if prompt_msg_id:
            await _safe_edit(
                bot, chat_id, prompt_msg_id,
                "❌ Нет доступа к аккаунту (переподключитесь через меню).",
                reply_markup=_BACK_KB,
            )
        else:
            await _safe_send(
                bot, chat_id,
                "❌ Нет доступа к аккаунту (переподключитесь через меню).",
                reply_markup=_BACK_KB,
            )
        return

    tracks = await client.search_tracks(access_token, text, limit=5)
    if not tracks:
        retry_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎵 Попробовать снова", callback_data="menu:add_song")],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
        ])
        if prompt_msg_id:
            await _safe_edit(
                bot, chat_id, prompt_msg_id,
                "❌ Трек не найден. Уточните запрос или пришлите ссылку.",
                reply_markup=retry_kb,
            )
        else:
            await _safe_send(
                bot, chat_id,
                "❌ Трек не найден. Уточните запрос или пришлите ссылку.",
                reply_markup=retry_kb,
            )
        return

    rows = []
    for t in tracks:
        tid = t["uri"].rsplit(":", 1)[-1]
        label = f"{t['artists']} — {t['name']}"
        if len(label) > 64:
            label = label[:61] + "…"
        rows.append([InlineKeyboardButton(label, callback_data=f"t:{tid}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="menu:back")])

    if prompt_msg_id:
        await _safe_edit(
            bot, chat_id, prompt_msg_id,
            "🎵 <b>Выберите трек:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    else:
        await _safe_send(
            bot, chat_id,
            "🎵 <b>Выберите трек:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )


# ─── Выбор трека (inline кнопка t:...) ──────────────────────────────────────

async def on_track_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = query.from_user
    if not db.get_user(user.id):
        await query.answer()
        try:
            await query.edit_message_text(
                "❌ Сначала подключите Spotify.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Подключить", callback_data="menu:connect")]
                ]),
            )
        except TelegramError:
            pass
        return

    await query.answer()

    data = query.data or ""
    if not (data.startswith("t:") and len(data) == 24):
        return
    track_id = data[2:]
    uri = f"spotify:track:{track_id}"

    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    try:
        access_token = await client.ensure_valid_token(user.id)
        track_info = await client.get_track_by_id(access_token, track_id) if access_token else None
        label = (
            f"{track_info.get('artists', 'Unknown')} — {track_info.get('name', 'Unknown')}"
            if track_info else track_id
        )
    except Exception:
        label = track_id

    context.user_data[_TRACK_PREVIEW_KEY] = {"uri": uri, "label": label}
    preview_text = (
        "🎵 <b>Предварительный просмотр</b>\n\n"
        f"1. {html.escape(label)}\n\n"
        "Создать плейлист с этим треком?"
    )
    preview_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать плейлист", callback_data="track:create")],
        [InlineKeyboardButton("❌ Отмена", callback_data="menu:back")],
    ])
    try:
        await query.edit_message_text(preview_text, parse_mode="HTML", reply_markup=preview_kb)
    except TelegramError:
        await _safe_send(context.bot, chat_id, preview_text, parse_mode="HTML", reply_markup=preview_kb)


# ─── Подтверждение трека (track:create) ────────────────────────────────────

async def on_track_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    preview = context.user_data.pop(_TRACK_PREVIEW_KEY, None)
    if not preview:
        try:
            await query.edit_message_text(
                "ℹ️ Сессия предпросмотра завершена.", reply_markup=_BACK_KB,
            )
        except TelegramError:
            pass
        return

    try:
        await query.edit_message_text("🔄 Создаю плейлист…")
    except TelegramError:
        pass

    try:
        playlist_url, track_label, err = await client.make_playlist_with_custom_track(
            user.id, preview["uri"],
        )
        if err:
            await _safe_edit(context.bot, chat_id, msg_id, f"❌ {err}", reply_markup=_BACK_KB)
            return
        safe_label = html.escape(track_label or preview.get("label", "трек"))
        result_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎵 Добавить ещё", callback_data="menu:add_song")],
            [InlineKeyboardButton("🗂 История", callback_data="menu:history")],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
        ])
        await _safe_edit(
            context.bot, chat_id, msg_id,
            "✅ <b>Плейлист создан</b>\n\n"
            f"🎵 {safe_label}\n\n"
            f'<a href="{playlist_url}">Открыть в Spotify</a>',
            parse_mode="HTML", disable_web_page_preview=False, reply_markup=result_kb,
        )
    except Exception as exc:
        logger.error("track create error: %s", exc, exc_info=True)
        await _safe_edit(context.bot, chat_id, msg_id, f"❌ Ошибка: {exc}", reply_markup=_BACK_KB)


# ─── Подтверждение Top-20 (top20:create) ───────────────────────────────────

async def on_top20_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    tracks = context.user_data.pop(_TOP20_PREVIEW_KEY, None)
    if not tracks:
        try:
            await query.edit_message_text(
                "ℹ️ Сессия предпросмотра завершена.", reply_markup=_BACK_KB,
            )
        except TelegramError:
            pass
        return

    try:
        await query.edit_message_text("🔄 Создаю плейлист в Spotify…")
    except TelegramError:
        pass

    try:
        uris = [t["uri"] for t in tracks]
        access_token = await client.ensure_valid_token(user.id)
        if not access_token:
            await _safe_edit(context.bot, chat_id, msg_id,
                             "❌ Нет доступа к аккаунту.", reply_markup=_BACK_KB)
            return

        db_user = db.get_user(user.id)
        playlist = await client.create_playlist(
            access_token, db_user["spotify_user_id"], "CatchTheWave — Top 20",
        )
        if not playlist:
            await _safe_edit(context.bot, chat_id, msg_id,
                             "❌ Не удалось создать плейлист.", reply_markup=_BACK_KB)
            return

        await client.add_tracks_to_playlist(access_token, playlist["id"], uris)
        url = playlist.get("external_urls", {}).get("spotify", "")
        db.add_playlist(
            user.id, "CatchTheWave — Top 20", "top20",
            spotify_playlist_id=playlist["id"], url=url, tracks_count=len(uris),
        )
        result_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔥 Ещё раз Top-20", callback_data="menu:top20")],
            [InlineKeyboardButton("🗂 История", callback_data="menu:history")],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
        ])
        await _safe_edit(
            context.bot, chat_id, msg_id,
            "✅ <b>Плейлист создан!</b>\n\n"
            "🎵 <b>CatchTheWave — Top 20</b>\n\n"
            f'<a href="{url}">Открыть в Spotify</a>',
            parse_mode="HTML", disable_web_page_preview=False, reply_markup=result_kb,
        )
    except Exception as exc:
        logger.error("top20 create error: %s", exc, exc_info=True)
        await _safe_edit(context.bot, chat_id, msg_id,
                         f"❌ Ошибка: {exc}", reply_markup=_BACK_KB)


# ─── Повтор генерации из истории (hist:<playlist_id>) ──────────────────────

async def on_history_regen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    data = query.data or ""
    playlist_id_str = data.removeprefix("hist:")

    try:
        playlist_id = int(playlist_id_str)
    except ValueError:
        return

    playlist = db.get_playlist(playlist_id)
    if not playlist or not playlist.get("prompt"):
        try:
            await query.edit_message_text(
                "❌ Запрос не найден.", reply_markup=_BACK_KB,
            )
        except TelegramError:
            pass
        return

    if not db.get_user(user.id):
        try:
            await query.edit_message_text(
                "❌ Сначала подключите Spotify.", reply_markup=_BACK_KB,
            )
        except TelegramError:
            pass
        return

    await _handle_ai_playlist(
        context.bot, chat_id, user.id, playlist["prompt"], msg_id,
        user_data=context.user_data,
    )


# ─── Slash-команды (обратная совместимость) ──────────────────────────────────

async def cmd_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info("/connect от %s", user.id)

    if db.get_user(user.id):
        await _safe_send(context.bot, chat_id, "✅ Вы уже подключены!", reply_markup=_BACK_KB)
        return

    auth_url = oauth.get_authorization_url(user.id)

    await _safe_send(
        context.bot, chat_id,
        "🔗 <b>Подключение к Spotify</b>\n\n"
        f'<a href="{auth_url}">Авторизоваться в Spotify</a>\n\n'
        "<b>Инструкция:</b>\n"
        "1. Откройте ссылку выше\n"
        "2. Войдите в Spotify и разрешите доступ\n"
        "3. <b>С компьютера</b> — бот автоматически пришлёт подтверждение\n"
        "4. <b>С телефона</b> — после авторизации скопируйте URL из адресной строки "
        "и вставьте его в этот чат\n\n"
        "⏳ Ссылка действительна 10 минут",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Проверить подключение", callback_data="menu:status")],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
        ]),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info("/status от %s", user.id)

    db_user = db.get_user(user.id)
    if not db_user:
        await _safe_send(
            context.bot, chat_id,
            "❌ <b>Не подключено</b>\nИспользуйте кнопку ниже.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Подключить Spotify", callback_data="menu:connect")],
                [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
            ]),
        )
        return

    expires_at = db_user["token_expires_at"]
    remaining = expires_at - int(time.time())

    if remaining > 0:
        token_line = f"✅ Активен (ещё {remaining // 60} мин)"
    else:
        token_line = "⏳ Истёк (обновится автоматически)"

    updated = datetime.fromisoformat(db_user["updated_at"])

    await _safe_send(
        context.bot, chat_id,
        "✅ <b>Подключено</b>\n\n"
        f"<b>Spotify ID:</b> {db_user['spotify_user_id']}\n"
        f"<b>Токен:</b> {token_line}\n"
        f"<b>Обновлено:</b> {updated:%Y-%m-%d %H:%M:%S}",
        parse_mode="HTML",
        reply_markup=_BACK_KB,
    )


async def cmd_make_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info("/make_playlist от %s", user.id)

    if not db.get_user(user.id):
        await _safe_send(
            context.bot, chat_id,
            "❌ Сначала подключитесь.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Подключить Spotify", callback_data="menu:connect")]
            ]),
        )
        return

    status_msg_id = await _safe_send(context.bot, chat_id, "🔄 Создаю плейлист…")
    try:
        playlist_url = await client.make_playlist_with_top_tracks(user.id)
        if playlist_url:
            if status_msg_id:
                await _safe_edit(
                    context.bot, chat_id, status_msg_id,
                    "✅ <b>Плейлист создан!</b>\n\n"
                    "🎵 <b>Diploma MVP — Top 20</b>\n\n"
                    f'<a href="{playlist_url}">Открыть в Spotify</a>',
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                    reply_markup=_BACK_KB,
                )
            else:
                await _safe_send(
                    context.bot, chat_id,
                    "✅ <b>Плейлист создан!</b>\n\n"
                    "🎵 <b>Diploma MVP — Top 20</b>\n\n"
                    f'<a href="{playlist_url}">Открыть в Spotify</a>',
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                    reply_markup=_BACK_KB,
                )
        else:
            if status_msg_id:
                await _safe_edit(
                    context.bot, chat_id, status_msg_id,
                    "❌ Не удалось создать плейлист.\nПопробуйте переподключиться.",
                    reply_markup=_BACK_KB,
                )
            else:
                await _safe_send(
                    context.bot, chat_id,
                    "❌ Не удалось создать плейлист.\nПопробуйте переподключиться.",
                    reply_markup=_BACK_KB,
                )
    except Exception as exc:
        logger.error("make_playlist error: %s", exc, exc_info=True)
        if status_msg_id:
            await _safe_edit(context.bot, chat_id, status_msg_id, f"❌ Ошибка: {exc}")
        else:
            await _safe_send(context.bot, chat_id, f"❌ Ошибка: {exc}")


async def cmd_add_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info("/add_song от %s args=%s", user.id, context.args)

    if not db.get_user(user.id):
        await _safe_send(
            context.bot, chat_id,
            "❌ Сначала подключитесь.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Подключить Spotify", callback_data="menu:connect")]
            ]),
        )
        return

    text = " ".join(context.args).strip()
    if not text:
        context.user_data[_MODE_KEY] = _MODE_ADD_SONG
        msg_id = await _safe_send(
            context.bot, chat_id,
            "🎵 <b>Добавить песню</b>\n\n"
            "Отправьте название, ссылку или URI:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="menu:back")]
            ]),
        )
        if msg_id:
            context.user_data[_PROMPT_MSG_KEY] = msg_id
        return

    await _handle_add_song_text(context.bot, chat_id, user.id, text, None)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info("/history от %s", user.id)

    items = db.list_playlists(user.id, limit=5)
    if not items:
        await _safe_send(
            context.bot, chat_id,
            "🗂 <b>История плейлистов пуста.</b>\n\n"
            "Создайте свой первый плейлист — и он появится здесь.",
            parse_mode="HTML", reply_markup=_BACK_KB,
        )
        return

    lines = ["🗂 <b>Последние плейлисты</b>\n"]
    buttons: list[list[InlineKeyboardButton]] = []
    for it in items:
        name = html.escape(it["name"] or "Без названия")
        created = (it["created_at"] or "")[:19].replace("T", " ")
        count = it["tracks_count"]
        count_str = f" • {count} тр." if count else ""
        url = it["url"]
        prompt = it.get("prompt")
        if url:
            lines.append(f'• <a href="{url}">{name}</a>\n  <i>{created}{count_str}</i>')
        else:
            lines.append(f"• {name}\n  <i>{created}{count_str}</i>")
        if prompt and it.get("source") == "ai":
            safe_prompt = html.escape(prompt[:80])
            lines.append(f'  <b>Запрос:</b> <i>{safe_prompt}</i>')
            buttons.append([
                InlineKeyboardButton(
                    f"🔁 Повторить: {prompt[:30]}{'…' if len(prompt) > 30 else ''}",
                    callback_data=f"hist:{it['id']}",
                )
            ])
    buttons.append([InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")])

    await _safe_send(
        context.bot, chat_id, "\n".join(lines),
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info("/disconnect от %s", user.id)

    if db.delete_user(user.id):
        await _safe_send(
            context.bot, chat_id,
            "✅ <b>Отключено.</b> Данные удалены.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Подключить снова", callback_data="menu:connect")],
                [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:back")],
            ]),
        )
    else:
        await _safe_send(
            context.bot, chat_id,
            "ℹ️ Вы и так не были подключены.",
            reply_markup=_BACK_KB,
        )


# ─── Standalone fallback ─────────────────────────────────────────────────────

def main():
    """Запуск бота отдельно (без callback-сервера — только для отладки)."""
    from app.logging_config import setup_logging
    setup_logging()
    db.init_db()
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(20)
        .build()
    )
    _register_handlers(application)
    logger.info("Telegram бот запущен (standalone, без callback-сервера)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


def _register_handlers(application: Application):
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("connect", cmd_connect))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("make_playlist", cmd_make_playlist))
    application.add_handler(CommandHandler("add_song", cmd_add_song))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CommandHandler("disconnect", cmd_disconnect))
    application.add_handler(
        CallbackQueryHandler(on_menu_callback, pattern=r"^menu:")
    )
    application.add_handler(
        CallbackQueryHandler(on_preset, pattern=r"^preset:")
    )
    application.add_handler(
        CallbackQueryHandler(on_ai_action, pattern=r"^ai:(create|regen)$")
    )
    application.add_handler(
        CallbackQueryHandler(on_top20_action, pattern=r"^top20:create$")
    )
    application.add_handler(
        CallbackQueryHandler(on_track_create, pattern=r"^track:create$")
    )
    application.add_handler(
        CallbackQueryHandler(on_track_pick, pattern=r"^t:[0-9A-Za-z]{22}$")
    )
    application.add_handler(
        CallbackQueryHandler(on_history_regen, pattern=r"^hist:\d+$")
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message)
    )


if __name__ == "__main__":
    main()
