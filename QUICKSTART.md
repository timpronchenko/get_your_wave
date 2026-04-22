# Быстрый старт

## Чеклист перед первым запуском

0. **🌐 Включите VPN** (для пользователей из РФ — обязательно).
   Без VPN недоступны `accounts.spotify.com` и `api.spotify.com`.

1. **Файл `.env`** в корне проекта — отредактируйте и подставьте свои значения:
   - `TELEGRAM_BOT_TOKEN` — от [@BotFather](https://t.me/BotFather)
   - `SPOTIFY_CLIENT_ID` — из [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - `DEEPSEEK_API_KEY` — с [platform.deepseek.com](https://platform.deepseek.com)

2. **Spotify Dashboard** — в настройках приложения в **Redirect URIs** добавьте **точно** эту строку (без `localhost`):

   `http://127.0.0.1:8000/callback`

   Сохраните изменения.

3. **Зависимости** (один раз):

   ```bash
   cd /path/to/VKR
   python3 -m venv .venv
   source .venv/bin/activate   # macOS/Linux
   pip install -r requirements.txt
   ```

   Или: `make venv && make install`

## Запуск (один терминал)

```bash
source .venv/bin/activate
make run
# или: uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Проверка: откройте в браузере `http://127.0.0.1:8000/health` — ответ `{"status":"ok"}`.

## Демо в Telegram

Откройте бота **на этом же компьютере** (например Telegram Desktop), иначе редирект на `127.0.0.1` не сработает.

1. `/start` → «🔗 Подключить Spotify» → открыть ссылку → разрешить.
2. **🤖 AI-плейлист:** «Создать AI-плейлист» → выбрать пресет
   (🛋 Чилл, 🏋️ Тренировка, 🧠 Фокус, 🎉 Вечеринка, 🇷🇺 Русский рэп,
   🕹 Ретро 80-х) **или** написать свой запрос в чат →
   увидеть **предпросмотр** → ✅ Создать / 🔁 Заново / ❌ Отмена.
3. **🔥 Топ-20 Global** — кнопка или `/make_playlist`.
4. **🎵 Добавить песню** — кнопка или `/add_song исполнитель название`
   (или ссылка `https://open.spotify.com/track/…`).
5. **🗂 История** — кнопка или `/history` (5 последних плейлистов
   с прямыми ссылками).

Подробнее: [README.md](README.md).
