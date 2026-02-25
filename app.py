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
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(api_url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("url"):
                            return await download_from_direct_url(data["url"], format_type, "cobalt")
                    
                    # Пробуем упрощенный payload
                    if response.status == 400:
                        async with session.post(api_url, headers=headers, json={"url": url}) as resp2:
                            if resp2.status == 200:
                                data = await resp2.json()
                                if data.get("url"):
                                    return await download_from_direct_url(data["url"], format_type, "cobalt")
        except Exception:
            continue
    
    return False, "SERVER_UNAVAILABLE"


async def download_via_tikwm(url: str) -> tuple[bool, str]:
    """Скачивает TikTok через TikWM API."""
    try:
        payload = {'url': url, 'count': 1, 'cursor': 0, 'web': 1}
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post("https://www.tikwm.com/api/", data=payload) as response:
                if response.status != 200:
                    return False, "TikWM API ошибка"
                
                res_json = await response.json()
                if res_json.get('code') != 0:
                    return False, f"TikWM: {res_json.get('msg', 'unknown error')}"
                
                data = res_json.get('data', {})
                video_url = data.get('play') or data.get('wmplay') or data.get('hdplay')
                
                if not video_url:
                    return False, "TikWM не нашел видео"
                
                if video_url.startswith('/'):
                    video_url = "https://www.tikwm.com" + video_url
                
                return await download_from_direct_url(video_url, "mp4", "tikwm")
    except Exception as e:
        return False, str(e)


async def download_via_instagram_api(url: str, format_type: str) -> tuple[bool, str]:
    """Специализированные методы для Instagram."""
    apis = [
        {"url": "https://downloadgram.org/wp-json/aio-dl/data", "data": {"url": url, "action": "post"}},
        {"url": "https://snapinsta.app/action.php", "data": {"url": url, "action": "post"}},
        {"url": "https://saveinsta.app/api/ajaxSearch", "data": {"q": url, "t": "media", "lang": "en"}},
    ]
    
    video_patterns = [
        r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'"(https?://[^"]+video[^"]*)"',
        r'href="(https?://[^"]+\.mp4[^"]*)"',
        r'url["\']?\s*[:=]\s*["\'](https?://[^"\']+)["\']',
    ]
    
    for api in apis:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.post(
                    api['url'],
                    data=api['data'],
                    headers={'User-Agent': config.DESKTOP_USER_AGENT, 'Referer': api['url']}
                ) as response:
                    if response.status != 200:
                        continue
                    
                    content = await response.text()
                    
                    for pattern in video_patterns:
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for match in matches:
                            if match and ('.mp4' in match or 'video' in match.lower()):
                                if not any(x in match.lower() for x in ['google', 'facebook', 'twitter', 'youtube']):
                                    return await download_from_direct_url(match, format_type, "instagram_api")
        except Exception:
            continue
    
    return False, "Instagram API не сработали"


async def download_via_youtube_api(url: str, format_type: str) -> tuple[bool, str]:
    """Специализированные методы для YouTube."""
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
    
    # YouTube Info API
    api_url = f"https://yt.lemnoslife.com/videos?part=snippet,contentDetails&id={video_id}"
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(api_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and data.get('items'):
                        return True, f"https://www.youtube.com/watch?v={video_id}"
    except Exception:
        pass
    
    return False, "YouTube API не сработал"


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
        ydl_opts.update({
            'format': 'worst[ext=mp4]/worst',
            'socket_timeout': 60,
            'retries': 3,
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
