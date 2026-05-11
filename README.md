# CatchTheWave — Telegram Spotify Playlist Bot

Telegram-бот + Mini App для создания плейлистов в Spotify
по текстовому запросу на естественном языке через DeepSeek AI.

Дипломный проект (ВКР), развёрнутый на VPS с HTTPS.

## Возможности

- **AI-плейлисты** — описание на естественном языке -> DeepSeek AI -> параллельный поиск треков в Spotify -> предпросмотр -> сохранение
- **Быстрые пресеты** — Чилл, Тренировка, Фокус, Вечеринка, Русский рэп, Ретро 80-х
- **Top-20 Global** — плейлист из глобального чарта Spotify одной кнопкой
- **Поиск и добавление** — по названию, ссылке или URI; можно добавить в существующий плейлист
- **Удаление плейлистов** — unfollow из Spotify + удаление из истории
- **История** — последние плейлисты с повтором AI-генерации
- **Telegram Mini App** — полноценный веб-интерфейс внутри Telegram (HTTPS)
- **OAuth 2.0 PKCE** — авторизация без client secret, поддержка мобильной авторизации через вставку URL
- **SQLite с WAL** — конкурентный доступ без блокировок

## Архитектура

```
Telegram Bot (polling)
        |
   FastAPI (uvicorn :8000)
   ├── /callback         — OAuth redirect от Spotify
   ├── /api/*            — REST API для Mini App (auth через Depends + HMAC)
   ├── /miniapp/         — статика Mini App (HTML/CSS/JS)
   └── /health           — healthcheck
        |
   nginx (443/80) + Let's Encrypt
        |
   catchthewave.duckdns.org
```

**Внешние API:** Spotify Web API, DeepSeek Chat API, Telegram Bot API.

## Требования

- Python 3.11+
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- Spotify App ([developer.spotify.com/dashboard](https://developer.spotify.com/dashboard))
- DeepSeek API Key ([platform.deepseek.com](https://platform.deepseek.com))
- VPN (для пользователей из РФ — Spotify заблокирован)

## Установка

```bash
git clone https://github.com/timpronchenko/get_your_wave.git
cd get_your_wave
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполнить .env своими значениями
```

## Настройка .env

```env
TELEGRAM_BOT_TOKEN=...          # от @BotFather
SPOTIFY_CLIENT_ID=...           # из Spotify Developer Dashboard
SPOTIFY_REDIRECT_URI=https://catchthewave.duckdns.org/callback
BASE_URL=https://catchthewave.duckdns.org
DEEPSEEK_API_KEY=...            # с platform.deepseek.com
```

Для локальной разработки:
```env
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/callback
BASE_URL=http://127.0.0.1:8000
```

## Настройка Spotify App

1. Создать приложение на [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. В **Redirect URIs** добавить URL из `.env` (например `https://catchthewave.duckdns.org/callback`)
3. Скопировать **Client ID** в `.env`. Client Secret **не нужен** (используется PKCE).

## Запуск

```bash
source .venv/bin/activate
make run
# или: uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Проверка: `curl http://127.0.0.1:8000/health` -> `{"status":"ok"}`.

## Использование

### Telegram Bot

1. `/start` — главное меню со статусом подключения
2. **Подключить Spotify** — OAuth через inline-кнопку
3. **AI-плейлист** — пресет или свободный текст -> предпросмотр -> создание
4. **Ещё...** — подменю с Top-20, поиском, историей, статусом
5. **Mini App** — кнопка появляется при HTTPS (`BASE_URL=https://...`)

### Mini App (веб-интерфейс)

Вкладки: Home, AI, History, Account. Поддержка поиска треков, добавления в существующие плейлисты, удаления.

### Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/connect` | Ссылка для авторизации Spotify |
| `/status` | Статус подключения и токена |
| `/make_playlist` | Top-20 Global плейлист |
| `/add_song` | Добавить трек (поиск / ссылка / URI) |
| `/history` | Последние 5 плейлистов |
| `/disconnect` | Отключить Spotify, удалить токены |

## Структура проекта

```
app/
├── main.py              # FastAPI + Telegram-бот, Mini App API (Depends)
├── bot.py               # Хендлеры, inline-кнопки, AI-флоу с предпросмотром
├── config.py            # Pydantic Settings (.env)
├── logging_config.py    # Логирование: консоль + ротация файла
├── webapp_auth.py       # Валидация Telegram initData (HMAC-SHA256)
├── prompt.txt           # Системный промпт для DeepSeek
├── ai/
│   └── deepseek.py      # DeepSeek API, JSON/markdown парсинг ответа
├── spotify/
│   ├── oauth.py         # OAuth 2.0 PKCE, state + TTL
│   └── client.py        # Spotify Web API (параллельный поиск через asyncio.gather)
├── storage/
│   └── db.py            # SQLite WAL: users, playlists, индексы
└── miniapp/
    ├── index.html       # Telegram Mini App UI
    ├── style.css        # Стили (Telegram theme variables)
    ├── app.js           # Логика, навигация, debounce
    └── api.js           # Fetch-обёртка с timeout (AbortController)
```

## Troubleshooting

| Проблема | Решение |
|----------|---------|
| Таймауты запросов к Spotify | Включить VPN (РФ) |
| `redirect_uri_mismatch` | Проверить URI в Spotify Dashboard и `.env` (должны совпадать) |
| `state expired` | State живёт 10 мин — повторить `/connect` |
| `401 Unauthorized` | Токен обновляется автоматически; если нет — `/disconnect` + `/connect` |
| Mini App белый экран | Прописать домен в BotFather -> Configure Mini App |
| `database is locked` | Включён WAL mode; если повторяется — перезапустить сервис |
| Кнопка Mini App не появляется | `BASE_URL` должен начинаться с `https://` |

## Технические решения

- **Параллельный поиск** — `asyncio.gather` для 20 треков одновременно (~1с вместо ~10с)
- **FastAPI Depends()** — DI для авторизации во всех API-эндпоинтах
- **PKCE без client secret** — безопасная OAuth авторизация для публичных клиентов
- **HMAC-SHA256 валидация** — проверка подлинности Telegram Mini App запросов
- **Lazy cleanup** — протухшие PKCE state автоматически чистятся
- **SQLite WAL** — журнал предзаписи для конкурентного доступа
- **AbortController** — 30с timeout на все fetch-запросы в Mini App

## Лицензия

Проект создан для дипломной работы (ВКР). МГТУ им. Баумана, кафедра ИУ5.
