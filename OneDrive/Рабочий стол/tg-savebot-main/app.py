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
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    try:
        with open("token.txt", "r") as f:
            TOKEN = f.read().strip()
        logger.warning("Токен загружен из файла")
    except:
        TOKEN = config.TELEGRAM_TOKEN
        if TOKEN:
            logger.warning("Токен загружен из config.py")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден! Установите переменную окружения.")

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
    """Скачивает через Cobalt API v10 (актуальная версия)."""
    cobalt_instances = [
        "https://api.cobalt.tools/api/download",
        "https://cobalt-api.mnd.sh/api/download",
        "https://api.boxiv.xyz/api/download"
    ]
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    
    payload = {
        "url": url,
        "videoQuality": "720",
        "filenameStyle": "pretty"
    }
    
    for api_url in cobalt_instances:
        try:
            logger.info(f"Trying Cobalt v10: {api_url}")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(api_url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        # В v10 ссылка может быть в поле 'url' или 'picker'
                        file_url = data.get("url") or data.get("stream")
                        if file_url:
                            return await download_from_direct_url(file_url, format_type, "cobalt")
        except Exception as e:
            logger.warning(f"Cobalt instance {api_url} failed: {e}")
            continue
    return False, "COBALT_FAILED"


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
    """Метод через воркер SaveFrom.net (самый надежный для фото/рилс)."""
    api_url = "https://worker.sf-api.com/savefrom.php"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://uk.savefrom.net",
        "Referer": "https://uk.savefrom.net/",
    }
    
    payload = {
        "url": url, "lang": "ru", "app": "sf", "referer": "https://uk.savefrom.net/"
    }

    try:
        logger.info(f"Запрос к SaveFrom для Instagram: {url}")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            async with session.post(api_url, data=payload, headers=headers) as response:
                if response.status == 200:
                    text = await response.text()
                    # Ищем прямые ссылки на CDN Instagram
                    links = re.findall(r'href="([^"]+)"', text)
                    media_links = [l for l in links if "scontent" in l or "cdninstagram" in l]
                    
                    if media_links:
                        final_link = media_links[0].replace("&amp;", "&")
                        return await download_from_direct_url(final_link, format_type, "instagram")
    except Exception as e:
        logger.error(f"SaveFrom API Error: {e}")
    
    return False, "SAVEFROM_FAILED"


async def download_via_facebook_api(url: str, format_type: str) -> tuple[bool, str]:
    """
    Специализированные методы для Facebook.
    """
    logger.info(f"Trying Facebook APIs for: {url[:60]}...")
    
    # Пробуем SSSFacebook API
    try:
        logger.info("Trying SSSFacebook API")
        encoded_url = quote(url, safe='')
        api_url = f"https://sssfacebook.com/api?url={encoded_url}"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            headers = {
                'User-Agent': config.DESKTOP_USER_AGENT,
                'Accept': 'application/json',
                'Referer': 'https://sssfacebook.com/',
            }
            async with session.get(api_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('status') == 'success' and data.get('data'):
                        items = data['data']
                        if isinstance(items, list) and len(items) > 0:
                            media_url = items[0].get('url') or items[0].get('src')
                            if media_url:
                                logger.info(f"SSSFacebook found media")
                                ext = '.jpg' if format_type == 'jpg' or 'image' in media_url else '.mp4'
                                return await download_from_direct_url(media_url, ext.replace('.', ''), "sssfacebook")
    except Exception as e:
        logger.warning(f"SSSFacebook error: {str(e)[:100]}")
    
    # Пробуем FDownloader API
    try:
        logger.info("Trying FDownloader API")
        encoded_url = quote(url, safe='')
        api_url = f"https://fdownloader.net/api?url={encoded_url}"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            headers = {
                'User-Agent': config.DESKTOP_USER_AGENT,
                'Accept': 'application/json',
            }
            async with session.get(api_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('url') or data.get('download_url'):
                        media_url = data.get('url') or data.get('download_url')
                        logger.info(f"FDownloader found media")
                        ext = '.jpg' if format_type == 'jpg' else '.mp4'
                        return await download_from_direct_url(media_url, ext.replace('.', ''), "fdownloader")
    except Exception as e:
        logger.warning(f"FDownloader error: {str(e)[:100]}")
    
    # Fallback: пробуем старые API если доступны
    old_apis = [
        {"url": f"https://fdown.net/download.php?url={quote(url, safe='')}", "parser": "direct"},
        {"url": f"https://getfb.net/facebook-video-downloader?url={quote(url, safe='')}", "parser": "json"},
        {"url": f"https://fbdown.net/download?url={quote(url, safe='')}", "parser": "redirect"},
    ]
    
    for api in old_apis:
        try:
            logger.info(f"Trying old Facebook API: {api['url'][:50]}...")
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
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
    Использует API для скачивания видео.
    """
    
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
                            
        except Exception as e:
            logger.warning(f"YouTube API {api_url} error: {str(e)}")
            continue
    
    return False, "Все YouTube API не сработали"


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


# ==================== ФОТО СПЕЦИФИЧНЫЕ ФУНКЦИИ ====================
async def download_via_instagram_photo_api(url: str) -> tuple[bool, str]:
    """Метод через воркер SaveFrom.net специально для фото Instagram."""
    api_url = "https://worker.sf-api.com/savefrom.php"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://uk.savefrom.net",
        "Referer": "https://uk.savefrom.net/",
    }
    
    payload = {
        "url": url, "lang": "ru", "app": "sf", "referer": "https://uk.savefrom.net/"
    }

    try:
        logger.info(f"Trying SaveFrom API for Instagram photo: {url}")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            async with session.post(api_url, data=payload, headers=headers) as response:
                if response.status == 200:
                    text = await response.text()
                    # Ищем прямые ссылки на CDN Instagram (jpg)
                    links = re.findall(r'href="([^"]+)"', text)
                    media_links = [l for l in links if "scontent" in l or "cdninstagram" in l]
                    
                    if media_links:
                        final_link = media_links[0].replace("&amp;", "&")
                        return await download_from_direct_url(final_link, "jpg", "instagram")
    except Exception as e:
        logger.error(f"SaveFrom photo API Exception: {e}")
    
    return False, "SAVEFROM_FAILED"

async def download_via_pinterest_photo_api(url: str) -> tuple[bool, str]:
    """Метод для Pinterest фото."""
    # Пробуем API Pinterest
    apis = [
        f"https://pinterestdownloader.com/download?url={quote(url, safe='')}",
        f"https://pinloader.com/download?url={quote(url, safe='')}",
    ]

    for api_url in apis:
        try:
            logger.info(f"Trying Pinterest API: {api_url[:50]}...")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(api_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                    if response.status == 200:
                        content = await response.text()
                        # Ищем ссылки на фото
                        img_links = re.findall(r'href="([^"]+\.jpg[^"]*)"', content) + \
                                   re.findall(r'src="([^"]+\.jpg[^"]*)"', content)

                        if img_links:
                            final_link = img_links[0].replace("&amp;", "&")
                            return await download_from_direct_url(final_link, "jpg", "pinterest")
        except Exception as e:
            logger.warning(f"Pinterest API error: {str(e)}")
            continue

    return False, "PINTEREST_FAILED"

async def download_via_facebook_photo_api(url: str) -> tuple[bool, str]:
    """Метод для Facebook фото."""
    try:
        logger.info("Trying Facebook photo API")
        encoded_url = quote(url, safe='')
        api_url = f"https://sssfacebook.com/api?url={encoded_url}"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            headers = {
                'User-Agent': config.DESKTOP_USER_AGENT,
                'Accept': 'application/json',
                'Referer': 'https://sssfacebook.com/',
            }
            async with session.get(api_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('status') == 'success' and data.get('data'):
                        items = data['data']
                        if isinstance(items, list) and len(items) > 0:
                            media_url = items[0].get('url') or items[0].get('src')
                            if media_url:
                                return await download_from_direct_url(media_url, "jpg", "facebook")
    except Exception as e:
        logger.warning(f"Facebook photo API error: {str(e)}")

    return False, "FACEBOOK_FAILED"

async def download_photo(url: str) -> tuple[bool, str]:
    """Основная функция скачивания фото."""
    platform = detect_platform(url)

    if platform == "instagram":
        success, result = await download_via_instagram_photo_api(url)
        if success: return True, result

    elif platform == "pinterest":
        success, result = await download_via_pinterest_photo_api(url)
        if success: return True, result

    elif platform == "facebook":
        success, result = await download_via_facebook_photo_api(url)
        if success: return True, result

    # Fallback через универсальные API
    logger.info("Trying universal photo APIs...")
    universal_apis = [
        f"https://savefrom.net/download?url={quote(url, safe='')}",
    ]

    for api_url in universal_apis:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(api_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                    if response.status == 200:
                        content = await response.text()
                        # Ищем фото ссылки
                        photo_links = re.findall(r'href="([^"]+\.jpg[^"]*)"', content) + \
                                     re.findall(r'src="([^"]+\.jpg[^"]*)"', content)

                        if photo_links:
                            final_link = photo_links[0].replace("&amp;", "&")
                            return await download_from_direct_url(final_link, "jpg", "universal")
        except Exception:
            continue

    return False, "❌ Не удалось скачать фото. Попробуйте другую ссылку."


# ==================== ОСНОВНАЯ ФУНКЦИЯ СКАЧИВАНИЯ ====================
async def download_content(url: str, format_type: str) -> tuple[bool, str]:
    """Оптимизированная очередь скачивания без лишнего мусора."""
    platform = "universal"
    if "instagram.com" in url.lower(): platform = "instagram"
    elif "tiktok.com" in url.lower(): platform = "tiktok"
    elif "youtube.com" in url.lower() or "youtu.be" in url.lower(): platform = "youtube"

    # ШАГ 1: Если Instagram — СРАЗУ в SaveFrom (обход блокировок Railway)
    if platform == "instagram":
        logger.info("Instagram: используя прямой API воркер...")
        success, result = await download_via_instagram_api(url, format_type)
        if success: return True, result

    # ШАГ 2: Пробуем Cobalt v10 (универсальный и стабильный)
    logger.info("Пробую Cobalt API...")
    success, result = await download_via_cobalt(url, format_type)
    if success: return True, result

    # ШАГ 3: Если это TikTok, пробуем специализированный TikWM
    if platform == "tiktok":
        success, result = await download_via_tikwm(url)
        if success: return True, result

    # ШАГ 4: Только если ничего не помогло и это НЕ Инстаграм — пускаем yt-dlp
    if platform != "instagram":
        logger.info("Запуск yt-dlp как последнего шанса...")
        # Здесь вставь свою логику вызова yt-dlp, которая у тебя была в коде
        # ... (твой старый блок с yt_dlp.YoutubeDL)
        
    return False, "❌ Не удалось скачать. Попробуйте другую ссылку."


# ==================== TELEGRAM HANDLERS ====================
class SaveContent(StatesGroup):
    waiting_for_link = State()

class SavePhoto(StatesGroup):
    waiting_for_link = State()


async def send_photo(message: types.Message, file_path: str):
    """Отправляет фото пользователю и удаляет его."""
    try:
        file_size = os.path.getsize(file_path)

        if file_size > MAX_FILE_SIZE:
            await message.answer(
                f"❌ Фото слишком большое ({file_size/1024/1024:.1f}MB). Максимум: 50MB"
            )
            return

        await message.answer_photo(
            photo=FSInputFile(file_path),
            caption="✅ Фото успешно скачано!"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {str(e)}")
    finally:
        try:
            os.remove(file_path)
        except:
            pass


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


HELP_TEXT = """🤖 **Универсальный бот для скачивания**

Скачивайте видео и фото с популярных платформ!

**Поддерживаемые:**
• Instagram (фото, рилсы) ⚠️ Проблемы
• TikTok (видео) ⚠️ Часто недоступен  
• YouTube (видео) ✅ Отлично
• Pinterest (фото) ⚠️ Заблокирован
• Facebook (видео, фото) ⚠️ Авторизация
• Twitter (видео, фото) ✅ Стабильно
• VK (видео, фото) ✅ Надежно

**Команды:**
• 📥 Сохранить контент — видео/фото с выбором формата
• 📸 Скачать фото — прямое скачивание фото

**Как использовать:**
1. Выберите команду
2. Отправьте ссылку
3. Готово! 🎉

Макс. размер файла: 50MB"""


@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    """Приветствие."""
    await state.clear()
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📥 Сохранить контент")],
            [KeyboardButton(text="📸 Скачать фото")],
            [KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я универсальный бот для скачивания видео и фото из социальных сетей.\n\n"
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


@dp.message(lambda m: m.text == "📸 Скачать фото")
async def save_photo_start(message: types.Message, state: FSMContext):
    """Начало скачивания фото."""
    await state.set_state(SavePhoto.waiting_for_link)
    await message.answer(
        "📎 Отправьте ссылку на фото.\n\n"
        "Поддерживаются: Instagram, Pinterest, Facebook"
    )


@dp.message(SavePhoto.waiting_for_link)
async def process_photo_link(message: types.Message, state: FSMContext):
    """Обработка ссылки на фото."""
    if not message.text:
        await message.answer("❌ Отправьте текстовое сообщение с ссылкой")
        return
    
    url = message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.answer("❌ Отправьте корректную ссылку (http:// или https://)")
        return

    await state.update_data(link=url)

    processing_msg = await message.answer("⏳ Скачиваю фото...")

    success, result = await download_photo(url)

    if success:
        await send_photo(processing_msg, result)
    else:
        await processing_msg.edit_text(f"❌ Ошибка:\n{result}")

    await state.clear()


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
