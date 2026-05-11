# Деплой на VPS

Инструкция по развёртыванию CatchTheWave на удалённом сервере с HTTPS.

## Требования к серверу

- Ubuntu 20.04+ (или Debian)
- Python 3.11+
- nginx
- Доменное имя (например через DuckDNS)

## 1. Подготовка сервера

```bash
apt update && apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
```

## 2. Загрузка проекта

```bash
mkdir -p /opt/catchthewave
# Скопировать файлы на сервер:
scp -r app/ requirements.txt Makefile .env user@SERVER:/opt/catchthewave/
```

Или через git:
```bash
cd /opt/catchthewave
git clone https://github.com/timpronchenko/get_your_wave.git .
cp .env.example .env
# Заполнить .env
```

## 3. Установка зависимостей

```bash
cd /opt/catchthewave
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 4. Настройка .env

```env
TELEGRAM_BOT_TOKEN=...
SPOTIFY_CLIENT_ID=...
SPOTIFY_REDIRECT_URI=https://YOUR_DOMAIN/callback
BASE_URL=https://YOUR_DOMAIN
DEEPSEEK_API_KEY=...
```

## 5. Настройка nginx

Создать `/etc/nginx/sites-available/catchthewave`:

```nginx
server {
    server_name YOUR_DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 80;
}
```

```bash
ln -s /etc/nginx/sites-available/catchthewave /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

## 6. SSL-сертификат (Let's Encrypt)

```bash
certbot --nginx -d YOUR_DOMAIN
```

Certbot автоматически настроит HTTPS и редирект с HTTP.

## 7. Systemd-сервис

Создать `/etc/systemd/system/catchthewave.service`:

```ini
[Unit]
Description=CatchTheWave Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/catchthewave
ExecStart=/opt/catchthewave/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable catchthewave
systemctl start catchthewave
```

## 8. Проверка

```bash
systemctl status catchthewave          # active (running)
curl http://127.0.0.1:8000/health      # {"status":"ok"}
curl https://YOUR_DOMAIN/health        # {"status":"ok"}
curl https://YOUR_DOMAIN/miniapp/      # HTML Mini App
```

## 9. Настройка BotFather

Для работы Mini App кнопки в Telegram:
1. Написать @BotFather
2. `/mybots` -> выбрать бота -> Bot Settings -> Configure Mini App
3. Указать URL: `https://YOUR_DOMAIN/miniapp/`

## Обновление

```bash
# С локальной машины:
scp -i ~/.ssh/KEY app/*.py app/ai/*.py app/spotify/*.py app/storage/*.py app/miniapp/* root@SERVER:/opt/catchthewave/app/

# На сервере:
systemctl restart catchthewave
```

## Мониторинг

```bash
# Логи сервиса
journalctl -u catchthewave -f

# Логи приложения
tail -f /opt/catchthewave/app.log

# Статус
systemctl status catchthewave
```

## Полезные команды

```bash
systemctl restart catchthewave   # перезапуск
systemctl stop catchthewave      # остановка
certbot renew                    # обновление SSL (автоматически по cron)
```
