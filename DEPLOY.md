# Инструкция по загрузке на GitHub

## Шаг 1: Инициализация Git репозитория

```bash
cd /Users/timmetim/Desktop/VKR
git init
```

## Шаг 2: Добавить файлы в staging

```bash
git add .
```

## Шаг 3: Создать первый коммит

```bash
git commit -m "Initial commit: Telegram → Spotify Playlist MVP"
```

## Шаг 4: Создать репозиторий на GitHub

1. Перейти на https://github.com
2. Нажать кнопку **"New"** (или **"+"** → **"New repository"**)
3. Заполнить:
   - **Repository name**: `telegram-spotify-playlist` (или любое другое имя)
   - **Description**: "MVP Telegram бот для создания Spotify плейлистов"
   - **Visibility**: Public или Private (на ваше усмотрение)
   - **НЕ** ставить галочки на "Add a README file", "Add .gitignore", "Choose a license" (всё уже есть)
4. Нажать **"Create repository"**

## Шаг 5: Подключить удалённый репозиторий и запушить

GitHub покажет инструкции, но вот команды:

```bash
# Заменить YOUR_USERNAME на ваш GitHub username
git remote add origin https://github.com/YOUR_USERNAME/telegram-spotify-playlist.git

# Переименовать ветку в main (если нужно)
git branch -M main

# Отправить код на GitHub
git push -u origin main
```

Если GitHub попросит авторизацию:
- Используйте **Personal Access Token** (не пароль)
- Создать токен: Settings → Developer settings → Personal access tokens → Tokens (classic)
- Права: `repo` (полный доступ к репозиториям)

## Альтернатива: через SSH

Если настроен SSH ключ:

```bash
git remote add origin git@github.com:YOUR_USERNAME/telegram-spotify-playlist.git
git branch -M main
git push -u origin main
```

## Проверка

После успешного push:
- Откройте репозиторий на GitHub
- Убедитесь, что все файлы загружены
- `.env` и `users.db` должны быть в `.gitignore` (не загружаются)

## Важно!

⚠️ **НЕ коммитьте `.env` файл!** Он уже в `.gitignore`, но проверьте:

```bash
git status
```

Если `.env` показывается как untracked (но не staged) — всё ок. Если он в staged — удалите:

```bash
git reset HEAD .env
```
