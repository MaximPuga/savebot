"""
Telegram Bot для скачивания видео/фото из соцсетей.
Использует yt-dlp и альтернативные API для поддержки множества платформ.
"""

# ==================== ИМПОРТЫ ====================
import asyncio
import json
import logging
import os
import random
import re
from urllib.parse import quote

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

import config

# yt-dlp импортируется локально в функциях чтобы ускорить старт

# ==================== КОНФИГУРАЦИЯ ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Константы
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB для Telegram
MIN_FILE_SIZE = 1024  # 1KB минимум
FILENAME_MAX_LEN = 80  # Макс. длина имени файла
TIMEOUT_DEFAULT = 90
TIMEOUT_INSTAGRAM = 180
TIMEOUT_TIKTOK = 120
TIMEOUT_PINTEREST = 120
TIMEOUT_FACEBOOK = 120

# Паттерны платформ
PLATFORM_PATTERNS = {
    "tiktok": ["tiktok.com", "vt.tiktok.com", "vm.tiktok.com", "m.tiktok.com"],
    "instagram": ["instagram.com"],
    "pinterest": ["pinterest.com"],
    "facebook": ["facebook.com", "fb.watch"],
    "youtube": ["youtube.com", "youtu.be"],
}

# Загрузка токена
def _read_token_file(path: str) -> str:
    """Читает токен из файла и возвращает пустую строку при ошибке."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def load_token() -> str:
    """Загружает токен бота из env, Docker secrets, файла или config."""
    env_candidates = (
        ("TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "").strip()),
        ("BOT_TOKEN", os.getenv("BOT_TOKEN", "").strip()),
        ("TOKEN", os.getenv("TOKEN", "").strip()),
    )
    for env_name, env_value in env_candidates:
        if env_value:
            logger.info("Токен загружен из env %s", env_name)
            return env_value

    file_candidates = []
    for env_name in ("TELEGRAM_BOT_TOKEN_FILE", "BOT_TOKEN_FILE"):
        env_path = os.getenv(env_name, "").strip()
        if env_path:
            file_candidates.append((env_name, env_path))

    file_candidates.extend(
        [
            ("token.txt", "token.txt"),
            ("docker-secret", "/run/secrets/telegram_bot_token"),
        ]
    )

    checked_files = []
    for source_name, path in file_candidates:
        if path in checked_files:
            continue
        checked_files.append(path)
        token_value = _read_token_file(path)
        if token_value:
            logger.warning("Токен загружен из %s", source_name)
            return token_value

    config_token = config.TELEGRAM_TOKEN.strip()
    if config_token:
        logger.warning("Токен загружен из config.py")
        return config_token

    checked_sources = [
        "env: TELEGRAM_BOT_TOKEN",
        "env: BOT_TOKEN",
        "env: TOKEN",
        "env files: TELEGRAM_BOT_TOKEN_FILE, BOT_TOKEN_FILE",
        "file: token.txt",
        "file: /run/secrets/telegram_bot_token",
        "config.py: TELEGRAM_TOKEN",
    ]
    raise ValueError(
        "Telegram bot token not found. "
        "Provide TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN_FILE, "
        f"or fill TELEGRAM_TOKEN in config.py. Checked sources: {', '.join(checked_sources)}."
    )


TOKEN = load_token()

# Инициализация бота
bot = Bot(token=TOKEN)
dp = Dispatcher()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def _mask_proxy(proxy: str) -> str:
    """Маскирует credentials в proxy URL."""
    if not proxy:
        return ""
    try:
        if "@" in proxy:
            scheme_and_auth, hostpart = proxy.split("@", 1)
            scheme = scheme_and_auth.split("://", 1)[0] if "://" in scheme_and_auth else "proxy"
            return f"{scheme}://***@{hostpart}"
        return proxy
    except Exception:
        return "(invalid proxy)"


def detect_platform(url: str) -> str | None:
    """Определяет платформу по URL."""
    url_lower = url.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return platform
    return None


def get_timeout(platform: str | None) -> int:
    """Возвращает таймаут для платформы."""
    timeouts = {
        "instagram": TIMEOUT_INSTAGRAM,
        "tiktok": TIMEOUT_TIKTOK,
        "pinterest": TIMEOUT_PINTEREST,
        "facebook": TIMEOUT_FACEBOOK,
    }
    return timeouts.get(platform, TIMEOUT_DEFAULT)


def get_proxy_config():
    """Получает конфигурацию прокси из env."""
    proxies_raw = os.getenv("YTDLP_PROXIES", "").strip()
    proxy_single = os.getenv("YTDLP_PROXY", "").strip() or os.getenv("PROXY_URL", "").strip()
    
    proxies = []
    if proxies_raw:
        proxies = [p.strip() for p in proxies_raw.split(",") if p.strip()]
    elif proxy_single:
        proxies = [proxy_single]
    
    return random.choice(proxies) if proxies else None


# ==================== API МЕТОДЫ СКАЧИВАНИЯ ====================
async def download_from_direct_url(url: str, format_type: str, platform: str) -> tuple[bool, str]:
    """Скачивает файл по прямой URL."""
    try:
        download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "telegram_bot")
        os.makedirs(download_dir, exist_ok=True)
        
        ext = ".mp4" if format_type == "mp4" else ".jpg"
        filename = f"{platform}_{hash(url) % 1000000}{ext}"
        file_path = os.path.join(download_dir, filename)
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                if response.status != 200:
                    return False, f"❌ Ошибка: статус {response.status}"
                
                with open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(1024):
                        f.write(chunk)
                
                if os.path.getsize(file_path) > MIN_FILE_SIZE:
                    return True, file_path
                else:
                    os.remove(file_path)
                    return False, "❌ Файл слишком маленький"
    except Exception as e:
        return False, f"❌ Ошибка: {str(e)}"


async def handle_redirect_url(
    redirect_url: str, format_type: str, platform: str,
    max_redirects: int = 3, current_depth: int = 0
) -> tuple[bool, str]:
    """Обрабатывает промежуточные редиректы."""
    if current_depth >= max_redirects:
        return False, f"❌ Слишком много редиректов для {platform}"
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(
                redirect_url,
                headers={'User-Agent': config.DESKTOP_USER_AGENT},
                allow_redirects=True
            ) as response:
                if response.status != 200:
                    return False, f"❌ Ошибка редиректа: статус {response.status}"
                
                final_url = str(response.url)
                
                # Прямой файл
                if any(ext in final_url for ext in ['.mp4', '.webm', '.jpg', '.jpeg', '.png']):
                    return await download_from_direct_url(final_url, format_type, platform)
                
                # Продолжение редиректа
                if "router.parklogic.com" in final_url or "download?url=" in final_url:
                    return await handle_redirect_url(final_url, format_type, platform, max_redirects, current_depth + 1)
                
                # Цикл редиректов
                if final_url == redirect_url:
                    return False, f"❌ Обнаружен цикл редиректов для {platform}"
                
                # Поиск URL в ответе
                content = await response.text()
                video_urls = re.findall(r'https?://[^\s"\'<>]+\.(?:mp4|webm|jpg|jpeg|png)', content)
                if video_urls:
                    return await download_from_direct_url(video_urls[0], format_type, platform)
                
                return False, "❌ Не удалось найти прямую ссылку"
    except Exception as e:
        return False, f"❌ Ошибка обработки редиректа: {str(e)}"


async def download_via_cobalt(url: str, format_type: str) -> tuple[bool, str]:
    """Скачивает через Cobalt API."""
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt.api.ghst.dev/api/json",
        "https://api.boxiv.xyz/api/json",
        "https://cobalt.sm6.zone/api/json",
    ]
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": config.DESKTOP_USER_AGENT,
    }
    
    payload = {
        "url": url,
        "videoQuality": "720",
        "downloadMode": "auto" if format_type == "mp4" else "photo",
        "filenameStyle": "pretty",
        "youtubeVideoCodec": "h264",
    }
    
    for api_url in cobalt_instances:
        try:
            logger.info(f"Trying Cobalt: {api_url}")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(api_url, headers=headers, json=payload) as response:
                    logger.info(f"Cobalt {api_url} status: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Cobalt response: {data}")
                        if data.get("url"):
                            return await download_from_direct_url(data["url"], format_type, "cobalt")
                        elif data.get("error"):
                            logger.warning(f"Cobalt error: {data.get('error')}")
                    else:
                        text = await response.text()
                        logger.warning(f"Cobalt {api_url} failed: {response.status} - {text[:200]}")
                    
                    # Пробуем упрощенный payload
                    if response.status in (400, 422):
                        async with session.post(api_url, headers=headers, json={"url": url}) as resp2:
                            logger.info(f"Cobalt simple payload status: {resp2.status}")
                            if resp2.status == 200:
                                data = await resp2.json()
                                if data.get("url"):
                                    return await download_from_direct_url(data["url"], format_type, "cobalt")
        except Exception as e:
            logger.warning(f"Cobalt {api_url} exception: {str(e)}")
            continue
    
    return False, "SERVER_UNAVAILABLE"


async def download_via_tikwm(url: str) -> tuple[bool, str]:
    """
    Скачивает TikTok через TikWM API и другие альтернативы.
    """
    # TikWM - самый надежный
    try:
        logger.info("Trying TikWM API")
        payload = {'url': url, 'count': 1, 'cursor': 0, 'web': 1}
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post("https://www.tikwm.com/api/", data=payload) as response:
                if response.status == 200:
                    res_json = await response.json()
                    if res_json.get('code') == 0:
                        data = res_json.get('data', {})
                        # Пробуем разные поля с видео
                        video_url = (data.get('hdplay') or 
                                    data.get('play') or 
                                    data.get('wmplay') or 
                                    data.get('video_0'))
                        
                        if video_url:
                            if video_url.startswith('/'):
                                video_url = "https://www.tikwm.com" + video_url
                            logger.info(f"TikWM found video: {video_url[:60]}...")
                            return await download_from_direct_url(video_url, "mp4", "tikwm")
                        
                        # Пробуем получить URL из других полей
                        if 'images' in data:
                            # Это может быть карусель фото
                            logger.info("TikWM: found image carousel, not video")
                else:
                    logger.warning(f"TikWM returned {response.status}")
    except Exception as e:
        logger.warning(f"TikWM error: {str(e)}")
    
    # SSSTik.io - другой надежный сервис
    try:
        logger.info("Trying SSSTik API")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            # Получаем токен
            async with session.get("https://ssstik.io/ru") as token_resp:
                if token_resp.status == 200:
                    html = await token_resp.text()
                    # Ищем токен в HTML
                    import re
                    token_match = re.search(r'name="_token" value="([^"]+)"', html)
                    if token_match:
                        token = token_match.group(1)
                        
                        # Делаем запрос на скачивание
                        payload = {
                            'id': url,
                            'locale': 'ru',
                            'tt': token
                        }
                        
                        async with session.post(
                            "https://ssstik.io/abc?url=dl",
                            data=payload,
                            headers={'User-Agent': config.DESKTOP_USER_AGENT}
                        ) as dl_resp:
                            if dl_resp.status == 200:
                                dl_html = await dl_resp.text()
                                # Ищем ссылку на видео
                                video_match = re.search(r'href="(https?://[^"]+\.mp4[^"]*)"', dl_html)
                                if video_match:
                                    video_url = video_match.group(1)
                                    logger.info(f"SSSTik found video URL")
                                    return await download_from_direct_url(video_url, "mp4", "ssstik")
    except Exception as e:
        logger.warning(f"SSSTik error: {str(e)}")
    
    # SnapTik - еще один вариант
    try:
        logger.info("Trying SnapTik API")
        api_url = f"https://snaptik.app/abc?url={url}"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(api_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                if response.status == 200:
                    text = await response.text()
                    # Ищем video URL
                    import re
                    video_match = re.search(r'data-video-url="([^"]+)"', text)
                    if video_match:
                        video_url = video_match.group(1)
                        logger.info(f"SnapTik found video URL")
                        return await download_from_direct_url(video_url, "mp4", "snaptik")
    except Exception as e:
        logger.warning(f"SnapTik error: {str(e)}")
    
    return False, "Все TikTok API не сработали"


async def download_via_instagram_api(url: str, format_type: str) -> tuple[bool, str]:
    """
    Специализированные методы для Instagram.
    Использует API и парсинг для получения медиа.
    """
    # DownloadGram API
    try:
        logger.info("Trying DownloadGram API")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            data = {"url": url, "action": "post"}
            async with session.post(
                "https://downloadgram.org/wp-json/aio-dl/data",
                data=data,
                headers={'User-Agent': config.DESKTOP_USER_AGENT, 'Referer': 'https://downloadgram.org/'}
            ) as response:
                if response.status == 200:
                    try:
                        result = await response.json()
                        if result and 'data' in result:
                            media_data = result['data']
                            if isinstance(media_data, list) and len(media_data) > 0:
                                # Берем первое медиа
                                first_media = media_data[0]
                                if 'url' in first_media:
                                    media_url = first_media['url']
                                    logger.info(f"DownloadGram found media")
                                    return await download_from_direct_url(media_url, format_type, "downloadgram")
                    except:
                        # Пробуем найти URL в тексте
                        text = await response.text()
                        import re
                        urls = re.findall(r'https?://[^\s"<>\']+\.(?:mp4|jpg|jpeg|png)', text)
                        if urls:
                            logger.info(f"DownloadGram found URL in text")
                            return await download_from_direct_url(urls[0], format_type, "downloadgram")
    except Exception as e:
        logger.warning(f"DownloadGram error: {str(e)}")
    
    # SnapInsta API
    try:
        logger.info("Trying SnapInsta API")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            data = {"url": url, "action": "post"}
            headers = {
                'User-Agent': config.DESKTOP_USER_AGENT,
                'Referer': 'https://snapinsta.app/',
                'X-Requested-With': 'XMLHttpRequest'
            }
            
            async with session.post(
                "https://snapinsta.app/action.php",
                data=data,
                headers=headers
            ) as response:
                if response.status == 200:
                    text = await response.text()
                    import re
                    
                    # Ищем ссылки на видео/фото
                    video_match = re.search(r'href="(https?://[^"]+\.mp4[^"]*)"', text, re.IGNORECASE)
                    if video_match:
                        video_url = video_match.group(1)
                        logger.info(f"SnapInsta found video")
                        return await download_from_direct_url(video_url, format_type, "snapinsta")
                    
                    # Ищем фото
                    photo_match = re.search(r'href="(https?://[^"]+\.(?:jpg|jpeg|png)[^"]*)"', text, re.IGNORECASE)
                    if photo_match and format_type == "jpg":
                        photo_url = photo_match.group(1)
                        logger.info(f"SnapInsta found photo")
                        return await download_from_direct_url(photo_url, format_type, "snapinsta")
    except Exception as e:
        logger.warning(f"SnapInsta error: {str(e)}")
    
    # ImgInn - для постов без API
    try:
        logger.info("Trying ImgInn redirect")
        # ImgInn позволяет смотреть посты без авторизации
        shortcode = None
        import re
        match = re.search(r'/p/([^/]+)', url)
        if match:
            shortcode = match.group(1)
            imginn_url = f"https://imginn.com/p/{shortcode}"
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(imginn_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                    if response.status == 200:
                        html = await response.text()
                        # Ищем видео
                        video_match = re.search(r'src="(https?://[^"]+instagram[^"]+\.mp4[^"]*)"', html)
                        if video_match:
                            video_url = video_match.group(1)
                            logger.info(f"ImgInn found video")
                            return await download_from_direct_url(video_url, format_type, "imginn")
                        
                        # Ищем фото
                        photo_match = re.search(r'src="(https?://[^"]+instagram[^"]+\.(?:jpg|jpeg)[^"]*)"', html)
                        if photo_match and format_type == "jpg":
                            photo_url = photo_match.group(1)
                            logger.info(f"ImgInn found photo")
                            return await download_from_direct_url(photo_url, format_type, "imginn")
    except Exception as e:
        logger.warning(f"ImgInn error: {str(e)}")
    
    return False, "Instagram API не сработали"


async def download_via_facebook_api(url: str, format_type: str) -> tuple[bool, str]:
    """
    Специализированные методы для Facebook.
    """
    logger.info(f"Trying Facebook APIs for: {url[:60]}...")
    
    # Пробуем разные Facebook downloader API
    fb_apis = [
        {"url": f"https://fdown.net/download.php?url={quote(url, safe='')}", "parser": "direct"},
        {"url": f"https://getfb.net/facebook-video-downloader?url={quote(url, safe='')}", "parser": "json"},
        {"url": f"https://fbdown.net/download?url={quote(url, safe='')}", "parser": "redirect"},
    ]
    
    for api in fb_apis:
        try:
            logger.info(f"Trying Facebook API: {api['url'][:50]}...")
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={'User-Agent': config.DESKTOP_USER_AGENT}
            ) as session:
                async with session.get(api['url'], allow_redirects=True) as response:
                    final_url = str(response.url)
                    
                    # Если редиректнуло на видео файл
                    if any(ext in final_url for ext in ['.mp4', '.webm']):
                        logger.info(f"Facebook API redirected to video")
                        return await download_from_direct_url(final_url, format_type, "facebook_direct")
                    
                    if response.status != 200:
                        continue
                    
                    content = await response.text()
                    
                    # Ищем ссылки на видео
                    video_patterns = [
                        r'href="(https?://[^"]+facebook[^"]*\.mp4[^"]*)"',
                        r'href="(https?://[^"]+video[^"]*\.mp4[^"]*)"',
                        r'src="(https?://[^"]+\.mp4[^"]*)"',
                        r'url["\']?\s*[:=]\s*["\'](https?://[^"\']+facebook[^"\']+)["\']',
                    ]
                    
                    for pattern in video_patterns:
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for match in matches:
                            if '.mp4' in match or 'video' in match.lower():
                                if not any(x in match.lower() for x in ['login', 'auth', 'error']):
                                    logger.info(f"Found Facebook video URL via pattern")
                                    return await download_from_direct_url(match, format_type, "facebook_api")
        except Exception as e:
            logger.warning(f"Facebook API error: {str(e)[:100]}")
            continue
    
    return False, "Facebook API не сработали"


async def download_via_youtube_api(url: str, format_type: str) -> tuple[bool, str]:
    """
    Специализированные методы для YouTube.
    Использует реальные API для скачивания видео.
    """
    import yt_dlp  # Для получения info и fallback
    
    # Извлекаем video ID
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:shorts\/)([0-9A-Za-z_-]{11})',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
    ]
    
    video_id = None
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            break
    
    if not video_id:
        return False, "Не удалось извлечь YouTube video ID"
    
    logger.info(f"YouTube video ID: {video_id}")
    
    # Пробуем прямые download API
    youtube_apis = [
        # RapidAPI - YTDownload (требует ключ, но пробуем публичные)
        f"https://yt.lemnoslife.com/videos?part=snippet&id={video_id}",
        # Invidious instances (работают без API ключа)
        f"https://iv.datura.network/api/v1/videos/{video_id}",
        f"https://vid.puffyan.us/api/v1/videos/{video_id}",
        f"https://iv.nboeck.de/api/v1/videos/{video_id}",
        f"https://iv.melmac.space/api/v1/videos/{video_id}",
        f"https://iv.nboeck.de/api/v1/videos/{video_id}",
        # Piped instances
        f"https://pipedapi.kavin.rocks/streams/{video_id}",
        f"https://api.piped.projectkreators.com/streams/{video_id}",
    ]
    
    headers = {
        'User-Agent': config.DESKTOP_USER_AGENT,
        'Accept': 'application/json',
    }
    
    for api_url in youtube_apis:
        try:
            logger.info(f"Trying YouTube API: {api_url}")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get(api_url, headers=headers) as response:
                    if response.status != 200:
                        logger.warning(f"API {api_url} returned {response.status}")
                        continue
                    
                    content_type = response.headers.get('Content-Type', '')
                    
                    # Если вернулся прямой файл (редко, но бывает)
                    if 'video/' in content_type or 'application/octet-stream' in content_type:
                        logger.info(f"API returned direct video file")
                        return await download_from_direct_url(api_url, format_type, "youtube_api")
                    
                    # JSON ответ
                    if 'json' in content_type:
                        data = await response.json()
                        
                        # Invidious формат
                        if 'formatStreams' in data or 'adaptiveFormats' in data:
                            formats = data.get('formatStreams', []) + data.get('adaptiveFormats', [])
                            for fmt in formats:
                                if 'url' in fmt and 'type' in fmt:
                                    if 'video' in fmt['type'] and 'mp4' in fmt['type']:
                                        logger.info(f"Found Invidious video URL")
                                        return await download_from_direct_url(fmt['url'], format_type, "youtube_invidious")
                        
                        # Piped формат  
                        if 'videoStreams' in data or 'audioStreams' in data:
                            streams = data.get('videoStreams', [])
                            if streams:
                                # Берем первый поток (обычно есть URL)
                                for stream in streams:
                                    if stream.get('url'):
                                        logger.info(f"Found Piped video URL")
                                        return await download_from_direct_url(stream['url'], format_type, "youtube_piped")
                        
                        # YT LemnosLife - только info, но можем построить URL
                        if 'items' in data:
                            logger.info(f"YT API confirmed video exists, trying fallback")
                            # Не дает прямой URL, но подтверждает что видео существует
                            # Вернемся к yt-dlp с этим знанием
                            
        except Exception as e:
            logger.warning(f"YouTube API {api_url} error: {str(e)}")
            continue
    
    # Fallback: пробуем yt-dlp с прокси (если доступен) и специальными опциями
    logger.info("All YouTube APIs failed, trying yt-dlp with special options")
    
    # Пробуем yt-dlp с другими клиентами
    try:
        download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "telegram_bot")
        os.makedirs(download_dir, exist_ok=True)
        
        # Пробуем разные клиенты YouTube
        clients = ['android', 'web', 'ios', 'mweb']
        
        for client in clients:
            try:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'outtmpl': os.path.join(download_dir, f"%(title).{FILENAME_MAX_LEN}s.%(ext)s"),
                    'format': 'best[protocol=https][ext=mp4]/best[ext=mp4]/best',
                    'socket_timeout': 30,
                    'retries': 2,
                    'extractor_args': {
                        'youtube': {
                            'player_client': [client],
                            'player_skip': ['webpage', 'config', 'js'] if client != 'web' else [],
                        }
                    },
                }
                
                def download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                        return ydl.prepare_filename(info)
                
                loop = asyncio.get_event_loop()
                file_path = await asyncio.wait_for(loop.run_in_executor(None, download), timeout=60)
                
                if os.path.exists(file_path) and os.path.getsize(file_path) > MIN_FILE_SIZE:
                    logger.info(f"yt-dlp with {client} client succeeded")
                    return True, file_path
                    
            except Exception as e:
                logger.warning(f"yt-dlp with {client} client failed: {str(e)[:100]}")
                continue
                
    except Exception as e:
        logger.error(f"yt-dlp fallback error: {str(e)}")
    
    return False, "Все YouTube методы не сработали"


async def download_via_alternative_api(url: str, format_type: str) -> tuple[bool, str]:
    """Скачивает через альтернативные API."""
    platform = detect_platform(url)
    
    api_lists = {
        "tiktok": config.TIKTOK_APIS,
        "instagram": config.INSTAGRAM_APIS,
        "pinterest": config.PINTEREST_APIS,
        "facebook": config.FACEBOOK_APIS,
    }
    
    apis = api_lists.get(platform, config.UNIVERSAL_APIS)
    if not platform:
        platform = "universal"
    
    # Черный список для фильтрации URL
    blacklist = [
        'twitter.com', 'facebook.com', 'instagram.com', 'youtube.com', 'googlevideo.com',
        'ssstwitter', 'snapinsta', 'savefrom', 'snaptik', 'musicaldown', 'ssstik',
        'fonts.googleapis', 'cdnjs', 'jquery', 'cloudflare', 'analytics',
    ]
    
    for api_url in apis:
        try:
            encoded_url = quote(url, safe='')
            full_url = api_url + encoded_url
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(full_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                    content_bytes = await response.read()
                    content_type = response.headers.get('Content-Type', '').lower()
                    
                    # Проверка на прямой файл
                    is_file = 'video/' in content_type or 'image/' in content_type
                    if not is_file and len(content_bytes) > 32:
                        header = content_bytes[:32]
                        if (b'ftyp' in header) or header.startswith(b'\xff\xd8') or \
                           header.startswith(b'\x89PNG') or header.startswith(b'\x1a\x45\xdf\xa3'):
                            is_file = True
                    
                    if is_file:
                        download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "telegram_bot")
                        os.makedirs(download_dir, exist_ok=True)
                        ext = '.mp4' if 'video' in content_type else '.jpg'
                        filename = f"{platform}_direct_{hash(url)%1000000}{ext}"
                        file_path = os.path.join(download_dir, filename)
                        with open(file_path, 'wb') as f:
                            f.write(content_bytes)
                        return True, file_path
                    
                    if response.status != 200:
                        continue
                    
                    # Декодируем текст
                    try:
                        content = content_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        content = content_bytes.decode('latin-1', errors='ignore')
                    
                    # Извлечение URL
                    found_urls = []
                    raw_urls = re.findall(
                        r'href=["\'](https?://[^"\']+)["\']|src=["\'](https?://[^"\']+)["\']',
                        content, re.IGNORECASE
                    )
                    
                    for matches in raw_urls:
                        match = next((m for m in matches if m), None)
                        if not match:
                            continue
                        
                        match_lower = match.lower().split('#')[0]
                        
                        # Фильтрация
                        if any(x in match_lower for x in blacklist):
                            continue
                        if any(match_lower.endswith(ext) for ext in ['.html', '.php', '.css', '.js']):
                            continue
                        
                        if 'tiktok' in match_lower or 'video' in match_lower or 'cdn' in match_lower:
                            found_urls.append(match)
                    
                    if found_urls:
                        direct_video = [u for u in found_urls if any(ext in u.lower() for ext in ['.mp4', '.webm'])]
                        download_url = direct_video[0] if direct_video else found_urls[0]
                        
                        if "router.parklogic.com" in download_url or "download?url=" in download_url:
                            return await handle_redirect_url(download_url, format_type, platform)
                        
                        return await download_from_direct_url(download_url, format_type, platform)
        except Exception:
            continue
    
    return False, f"❌ Не удалось скачать {platform} через альтернативные API"


# ==================== ОСНОВНАЯ ФУНКЦИЯ СКАЧИВАНИЯ ====================
async def download_content(url: str, format_type: str) -> tuple[bool, str]:
    """Основная функция скачивания с yt-dlp и fallback на API."""
    import yt_dlp  # Ленивый импорт
    
    original_url = url
    platform = detect_platform(url)
    selected_proxy = get_proxy_config()
    
    if selected_proxy:
        logger.info("Proxy: %s", _mask_proxy(selected_proxy))
    
    # Для YouTube сначала пробуем Cobalt API (более надежный чем yt-dlp на datacenter IP)
    if platform == "youtube":
        logger.info("YouTube detected, trying Cobalt API first")
        cobalt_success, cobalt_result = await download_via_cobalt(original_url, format_type)
        if cobalt_success:
            return True, cobalt_result
        logger.info("Cobalt failed for YouTube, falling back to yt-dlp")
    
    # Базовые опции yt-dlp
    download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "telegram_bot")
    os.makedirs(download_dir, exist_ok=True)
    
    ydl_opts = {
        'quiet': False,
        'no_warnings': False,
        'outtmpl': os.path.join(download_dir, f"%(title).{FILENAME_MAX_LEN}s.%(ext)s"),
        'socket_timeout': 120,
        'noplaylist': True,
        'geo_bypass': True,
        'no_color': False,
        'extractor_retries': 15,
        'fragment_retries': 15,
        'retries': 15,
        'file_access_retries': 10,
        'fragment_timeout': 180,
        'http_chunk_size': 1048576,
        'ignoreerrors': False,
        'no_check_certificate': True,
        'prefer_free_formats': True,
        'add_header': [
            'Accept-Language: en-US,en;q=0.9',
            'Sec-Ch-Ua: "Not_A Brand";v="8", "Chromium";v="120"',
            'Sec-Ch-Ua-Mobile: ?0',
            'Sec-Ch-Ua-Platform: "Windows"',
        ],
    }
    
    if selected_proxy:
        ydl_opts['proxy'] = selected_proxy
    
    # Платформенно-специфичные опции
    if platform == "tiktok":
        ydl_opts.update({
            'extractor_args': {
                'tiktok': {
                    'api_hostname': 'api16-normal-c-useast1a.tiktokv.com',
                    'enable_headers': True,
                    'app_name': 'musical_ly',
                    'device_id': '7234567890123456789',
                }
            },
            'format': 'best[filesize<50M][ext=mp4]/worst[ext=mp4]',
            'http_headers': {
                'User-Agent': config.MOBILE_USER_AGENT,
                'Referer': 'https://www.tiktok.com/',
            },
            'socket_timeout': 60,
            'retries': 3,
        })
    
    elif platform == "instagram":
        ydl_opts.update({
            'extractor_args': {'instagram': {'include_ads': False, 'enable_headers': True}},
            'format': 'best[filesize<50M][ext=mp4]/worst[ext=mp4]',
            'http_headers': {
                'User-Agent': config.DESKTOP_USER_AGENT,
                'Referer': 'https://www.instagram.com/',
            },
        })
    
    elif platform == "pinterest":
        ydl_opts.update({
            'format': 'best[ext=jpg]/best[ext=jpeg]/best[ext=png]/best',
            'http_headers': {
                'User-Agent': config.DESKTOP_USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            },
            'socket_timeout': 90,
        })
    
    elif platform == "facebook":
        ydl_opts.update({
            'format': 'best[filesize<50M][ext=mp4]/worst[ext=mp4]',
            'http_headers': {
                'User-Agent': config.DESKTOP_USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,video/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            },
            'socket_timeout': 90,
        })
    
    elif platform == "youtube":
        # Используем формат без HLS/m3u8 чтобы избежать 403 на фрагментах
        ydl_opts.update({
            'format': 'worst[protocol=https][ext=mp4]/worst[protocol=https]/worst[ext=mp4]/worst',
            'socket_timeout': 60,
            'retries': 3,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'player_skip': ['webpage', 'config', 'js'],
                }
            },
        })
    
    # Формат
    if format_type == "mp4":
        ydl_opts['format'] = ydl_opts.get('format', 'best[filesize<50M][ext=mp4]/worst[ext=mp4]')
    elif format_type == "jpg":
        ydl_opts.update({
            'writethumbnail': True,
            'write_all_thumbnails': True,
            'skip_download': False,
            'format': 'best[ext=jpg]/best[ext=jpeg]/best[ext=png]/best',
            'postprocessors': [],
        })
    
    # Скачивание
    def download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    
    try:
        timeout = get_timeout(platform)
        loop = asyncio.get_event_loop()
        file_path = await asyncio.wait_for(loop.run_in_executor(None, download), timeout=timeout)
        
        # Проверка файла
        if os.path.exists(file_path) and os.path.getsize(file_path) > MIN_FILE_SIZE:
            return True, file_path
        
        # Fallback для YouTube
        if platform == "youtube":
            logger.info("YouTube file empty, trying alternative APIs")
            yt_success, yt_result = await download_via_youtube_api(original_url, format_type)
            if yt_success:
                return True, yt_result
            
            cobalt_success, cobalt_result = await download_via_cobalt(original_url, format_type)
            if cobalt_success:
                return True, cobalt_result
        
        return False, "❌ Файл не был скачан или пуст"
    
    except asyncio.TimeoutError:
        return False, "❌ Превышено время ожидания"
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка yt-dlp: {error_msg}")
        
        # Fallback на альтернативные API
        error_triggers = [
            "rate-limit", "login required", "403", "sigi state",
            "unable to extract", "file is empty", "fragment", "forbidden"
        ]
        
        if platform or any(err in error_msg.lower() for err in error_triggers):
            # YouTube fallback
            if platform == "youtube" or any(err in error_msg.lower() for err in ["403", "forbidden"]):
                yt_success, yt_result = await download_via_youtube_api(original_url, format_type)
                if yt_success:
                    return True, yt_result
            
            # Facebook fallback
            if platform == "facebook":
                fb_success, fb_result = await download_via_facebook_api(original_url, format_type)
                if fb_success:
                    return True, fb_result
            
            # TikTok fallback
            if platform == "tiktok":
                tikwm_success, tikwm_result = await download_via_tikwm(original_url)
                if tikwm_success:
                    return True, tikwm_result
            
            # Instagram fallback
            if platform == "instagram":
                insta_success, insta_result = await download_via_instagram_api(original_url, format_type)
                if insta_success:
                    return True, insta_result
            
            # YouTube fallback (если еще не пробовали)
            if platform == "youtube":
                yt_success, yt_result = await download_via_youtube_api(original_url, format_type)
                if yt_success:
                    return True, yt_result
            
            # Общий fallback
            cobalt_success, cobalt_result = await download_via_cobalt(original_url, format_type)
            if cobalt_success:
                return True, cobalt_result
            
            return await download_via_alternative_api(original_url, format_type)
        
        # Стандартные ошибки
        if "No video formats found" in error_msg:
            return False, "❌ На этой странице нет видео/фото"
        
        clean_error = re.sub(r'\x1b\[[0-9;]*m', '', error_msg)
        return False, f"❌ Ошибка: {clean_error[:200]}"


# ==================== TELEGRAM HANDLERS ====================
class SaveContent(StatesGroup):
    waiting_for_link = State()


async def send_file(message: types.Message, file_path: str, format_type: str):
    """Отправляет файл пользователю и удаляет его."""
    try:
        file_size = os.path.getsize(file_path)
        
        if file_size > MAX_FILE_SIZE:
            await message.answer(
                f"❌ Файл слишком большой ({file_size/1024/1024:.1f}MB). Максимум: 50MB"
            )
            return
        
        if format_type == "mp4":
            await message.answer_video(
                video=FSInputFile(file_path),
                caption="✅ Видео успешно скачано!"
            )
        elif format_type == "jpg":
            await message.answer_photo(
                photo=FSInputFile(file_path),
                caption="✅ Фото успешно скачано!"
            )
        else:
            await message.answer_document(
                document=FSInputFile(file_path),
                caption="✅ Файл успешно скачан!"
            )
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {str(e)}")
    finally:
        try:
            os.remove(file_path)
        except:
            pass


HELP_TEXT = """🤖 **Справка по боту**

Скачивайте видео и фото с популярных платформ!

**Поддерживаемые:**
• Instagram (фото, рилсы) ⚠️ Проблемы
• TikTok (видео) ⚠️ Часто недоступен  
• YouTube (видео) ✅ Отлично
• Pinterest (фото) ⚠️ Заблокирован
• Facebook (видео, фото) ⚠️ Авторизация
• Twitter (видео, фото) ✅ Стабильно
• VK (видео, фото) ✅ Надежно

**Рекомендуем:** YouTube, VK, Twitter

**Как использовать:**
1. Нажмите "📥 Сохранить контент"
2. Отправьте ссылку
3. Выберите формат
4. Готово! 🎉

Макс. размер файла: 50MB"""


@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    """Приветствие."""
    await state.clear()
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📥 Сохранить контент")],
            [KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я бот для скачивания видео и фото из социальных сетей.\n\n"
        "✅ YouTube, Instagram, TikTok, Facebook, Pinterest, Twitter, VK\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )


@dp.message(lambda m: m.text == "📥 Сохранить контент")
async def save_content_start(message: types.Message, state: FSMContext):
    """Начало сохранения."""
    await state.set_state(SaveContent.waiting_for_link)
    await message.answer(
        "📎 Отправьте ссылку на видео или фото.\n\n"
        "Поддерживаются: YouTube, Instagram, TikTok, Facebook, Pinterest, Twitter, VK"
    )


@dp.message(SaveContent.waiting_for_link)
async def process_link(message: types.Message, state: FSMContext):
    """Обработка ссылки."""
    if not message.text:
        await message.answer("❌ Отправьте текстовое сообщение с ссылкой")
        return
    
    url = message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.answer("❌ Отправьте корректную ссылку (http:// или https://)")
        return
    
    await state.update_data(link=url)
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📹 Видео (MP4)", callback_data="format_mp4")],
            [InlineKeyboardButton(text="🖼️ Фото (JPG)", callback_data="format_jpg")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
        ]
    )
    
    await message.answer(
        f"✅ Ссылка получена!\n\nВыберите формат:\n<code>{url}</code>",
        reply_markup=kb,
        parse_mode="html"
    )


@dp.message(lambda m: m.text == "ℹ️ Помощь")
async def help_handler(message: types.Message):
    """Показывает справку."""
    await message.answer(HELP_TEXT, parse_mode="markdown")


@dp.message(lambda m: m.text == "⚙️ Настройки")
async def settings_handler(message: types.Message):
    """Показывает настройки."""
    await message.answer(
        "⚙️ **Настройки**\n\n"
        "🟢 Статус: Активен\n"
        "📊 Версия: 2.0\n"
        "⏰ Работает 24/7",
        parse_mode="markdown"
    )


@dp.callback_query()
async def callback_handler(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик callback кнопок."""
    data = callback.data
    
    if data == "cancel":
        await callback.message.delete()
        await state.clear()
        await callback.answer("❌ Отменено")
        return
    
    if data in ("format_mp4", "format_jpg"):
        format_type = "mp4" if data == "format_mp4" else "jpg"
        label = "видео" if format_type == "mp4" else "фото"
        
        try:
            processing_msg = await callback.message.edit_text(
                f"⏳ Скачиваю {label}...\n\nЭто может занять до 2 минут"
            )
        except Exception:
            processing_msg = await callback.message.answer(
                f"⏳ Скачиваю {label}..."
            )
        
        await callback.answer(f"📥 Загрузка {label} началась!")
        
        state_data = await state.get_data()
        url = state_data.get("link")
        
        if url:
            success, result = await download_content(url, format_type)
            
            if success:
                await send_file(processing_msg, result, format_type)
            else:
                await processing_msg.edit_text(f"❌ Ошибка:\n{result}")
        
        await state.clear()
        return
    
    await callback.answer()


@dp.message(Command("status"))
async def status_handler(message: types.Message):
    """Статус бота."""
    await message.answer(
        "✅ **Статус:** Бот активен и работает!",
        parse_mode="markdown"
    )


# ==================== ЗАПУСК ====================
async def main():
    """Запуск бота."""
    logger.info("Бот запущен!")
    
    # Debug: показываем env переменные (маскированные)
    env_debug = {
        k: ("***" if any(x in k for x in ["TOKEN", "PASS", "SECRET", "KEY"]) else v)
        for k, v in os.environ.items()
        if "PROXY" in k or "TOKEN" in k
    }
    logger.info("Environment: %s", env_debug)
    
    proxy = get_proxy_config()
    if proxy:
        logger.info("Proxy configured: %s", _mask_proxy(proxy))
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
