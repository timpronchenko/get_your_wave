"""Клиент DeepSeek API для генерации плейлистов."""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-pro"

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompt.txt"
_SYSTEM_PROMPT: str = _PROMPT_PATH.read_text(encoding="utf-8").strip()

# Подпись к запросу пользователя: усиливает формат (требование DeepSeek JSON mode — слово «json» в промпте)
_USER_SUFFIX = (
    "\n\n[Техническое задание для ответа] Верни строго один JSON-объект с полем tracks "
    '(массив объектов {"title","artist"}). Только json, без markdown и без текста до/после.'
)

# **Artist - Title** или **Title — Artist** (часто в ответах-«статьях»)
_RE_BOLD_TRACK = re.compile(
    r"\*\*\s*([^*]+?)\s*[-–—]\s*([^*]+?)\s*\*\*",
    re.UNICODE,
)


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3].rstrip()
    return t.strip()


def _normalize_parsed_list(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict) and "title" in item and "artist" in item:
            out.append({"title": str(item["title"]), "artist": str(item["artist"])})
    return out


def _parse_json_to_tracks(content: str) -> Optional[List[Dict[str, str]]]:
    """Распарсить JSON-объект / массив из строки. None — не удалось; [] — пустой допустимый JSON."""
    content = _strip_code_fence(content)
    data: Any
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        s, e = content.find("{"), content.rfind("}")
        if s != -1 and e > s:
            try:
                data = json.loads(content[s : e + 1])
            except json.JSONDecodeError:
                data = None
        else:
            data = None
        if data is None:
            s, e = content.find("["), content.rfind("]")
            if s != -1 and e > s:
                try:
                    data = json.loads(content[s : e + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None

    if isinstance(data, dict):
        for key in ("tracks", "playlist", "items", "songs"):
            if key in data and isinstance(data[key], list):
                return _normalize_parsed_list(data[key])
        return None
    if isinstance(data, list):
        return _normalize_parsed_list(data)
    return None


def _parse_markdown_fallback(content: str) -> List[Dict[str, str]]:
    """Извлечь пары artist/title из вида **Artist - Title** (если модель снова выдала статью)."""
    seen: set[tuple[str, str]] = set()
    out: List[Dict[str, str]] = []
    for m in _RE_BOLD_TRACK.finditer(content):
        left, right = m.group(1).strip(), m.group(2).strip()
        # Убрать хвосты вроде (3:22) если попали в title
        right = re.sub(r"\s*\([^)]*\)\s*$", "", right).strip()
        left = re.sub(r"\s*\([^)]*\)\s*$", "", left).strip()
        if len(left) < 2 or len(right) < 1:
            continue
        pair = (left, right)
        if pair in seen:
            continue
        seen.add(pair)
        # Эвристика: исполнитель обычно короче или с известными паттернами; чаще «Artist - Title»
        out.append({"artist": left, "title": right})
    logger.warning(
        "Использован запасной разбор markdown (найдено %d треков по шаблону **... - ...**)",
        len(out),
    )
    return out


async def generate_playlist(user_message: str) -> List[Dict[str, str]]:
    """Отправить запрос в DeepSeek и вернуть список треков.

    Возвращает список вида [{"title": "...", "artist": "..."}, ...].
    При ошибке возвращает пустой список.
    """
    if not settings.deepseek_api_key:
        logger.error("DEEPSEEK_API_KEY не задан")
        return []

    user_content = (user_message or "").strip() + _USER_SUFFIX

    payload: Dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.35,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }

    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            if resp.status_code == 400:
                logger.warning(
                    "DeepSeek отклонил запрос с response_format=json_object, повтор без него: %s",
                    resp.text[:300],
                )
                payload_retry = {k: v for k, v in payload.items() if k != "response_format"}
                resp = await client.post(DEEPSEEK_API_URL, json=payload_retry, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "DeepSeek HTTP error: %s — %s",
                e.response.status_code,
                e.response.text[:500],
            )
            return []
        except Exception as e:
            logger.error("DeepSeek request error: %s", e)
            return []

    try:
        data = resp.json()
        choice = data["choices"][0]
        content = (choice.get("message") or {}).get("content") or ""
        content = content.strip()
        finish = choice.get("finish_reason")
        if finish == "length":
            logger.warning("DeepSeek: ответ обрезан по max_tokens (finish_reason=length)")
    except (KeyError, IndexError, TypeError) as e:
        logger.error("Неожиданный формат ответа DeepSeek: %s", e)
        return []

    if not content:
        logger.error("DeepSeek вернул пустой content")
        return []

    logger.info("DeepSeek raw response:\n%s", content)

    parsed = _parse_json_to_tracks(content)
    if parsed is not None and len(parsed) > 0:
        logger.info("DeepSeek вернул %d треков (JSON)", len(parsed))
        logger.debug(
            "Parsed tracks: %s",
            json.dumps(parsed, ensure_ascii=False, indent=2),
        )
        return parsed

    if parsed is not None and len(parsed) == 0:
        logger.warning("JSON распарсился, но tracks пустой — пробуем markdown-fallback")

    fallback = _parse_markdown_fallback(content)
    if fallback:
        logger.info("Итого треков после fallback: %d", len(fallback))
        logger.debug(
            "Parsed tracks (fallback): %s",
            json.dumps(fallback, ensure_ascii=False, indent=2),
        )
        return fallback

    logger.error("Не удалось извлечь треки: ни JSON, ни markdown-шаблоны")
    return []
