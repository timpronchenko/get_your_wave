# Быстрый старт

## Чеклист перед запуском

1. **VPN** (для РФ) — без него Spotify API недоступен
2. **`.env`** в корне проекта со значениями:
   - `TELEGRAM_BOT_TOKEN` — от [@BotFather](https://t.me/BotFather)
   - `SPOTIFY_CLIENT_ID` — из [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - `DEEPSEEK_API_KEY` — с [platform.deepseek.com](https://platform.deepseek.com)
   - `SPOTIFY_REDIRECT_URI` и `BASE_URL` — зависят от окружения (см. ниже)
3. **Spotify Dashboard** — в Redirect URIs добавить URL из `SPOTIFY_REDIRECT_URI`
4. **BotFather** — для Mini App: Bot Settings -> Configure Mini App -> указать домен

## Локальная разработка

```bash
cd get_your_wave
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить значения
make run
```

В `.env`:
```env
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/callback
BASE_URL=http://127.0.0.1:8000
```

Проверка: `http://127.0.0.1:8000/health` -> `{"status":"ok"}`.

Mini App кнопка не появится (нужен HTTPS). Бот работает через inline-кнопки.

## Продакшен (VPS)

В `.env`:
```env
SPOTIFY_REDIRECT_URI=https://catchthewave.duckdns.org/callback
BASE_URL=https://catchthewave.duckdns.org
```

Запуск через systemd: `systemctl start catchthewave`.

Подробнее: [DEPLOY.md](DEPLOY.md).

## Демо

1. `/start` -> Подключить Spotify -> авторизоваться
2. **AI-плейлист** -> выбрать пресет или написать запрос -> предпросмотр -> создать
3. **Ещё...** -> Top-20 / Добавить песню / История
4. **Mini App** (при HTTPS) -> полный веб-интерфейс внутри Telegram
