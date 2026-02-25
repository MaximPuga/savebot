import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import os
from pathlib import Path
import yt_dlp
import config
import aiohttp
import json
import re
import random
from urllib.parse import quote

# Включаем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _mask_proxy(proxy: str) -> str:
    try:
        if not proxy:
            return ""
        if "@" in proxy:
            scheme_and_auth, hostpart = proxy.split("@", 1)
            scheme = scheme_and_auth.split("://", 1)[0] if "://" in scheme_and_auth else "proxy"
            return f"{scheme}://***@{hostpart}"
        return proxy
    except Exception:
        return "(invalid proxy)"

# Берем токен из переменной окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    # Если переменной нет, пробуем прочитать из файла
    try:
        with open("token.txt", "r") as f:
            TOKEN = f.read().strip()
    except:
        TOKEN = config.TELEGRAM_TOKEN
    
    logger.warning("Используется токен из файла, а не из переменной окружения!")

def get_auth_config(platform):
    """Получает конфигурацию авторизации для платформы"""
    auth_config = {}
    
    if platform.lower() == "instagram":
        if config.INSTAGRAM_USERNAME and config.INSTAGRAM_PASSWORD:
            auth_config['username'] = config.INSTAGRAM_USERNAME
            auth_config['password'] = config.INSTAGRAM_PASSWORD
        if os.path.exists(config.INSTAGRAM_COOKIES_FILE):
            auth_config['cookiefile'] = config.INSTAGRAM_COOKIES_FILE
            
    elif platform.lower() == "tiktok":
        if config.TIKTOK_USERNAME and config.TIKTOK_PASSWORD:
            auth_config['username'] = config.TIKTOK_USERNAME
            auth_config['password'] = config.TIKTOK_PASSWORD
        if os.path.exists(config.TIKTOK_COOKIES_FILE):
            auth_config['cookiefile'] = config.TIKTOK_COOKIES_FILE
            
    elif platform.lower() == "facebook":
        if config.FACEBOOK_EMAIL and config.FACEBOOK_PASSWORD:
            auth_config['username'] = config.FACEBOOK_EMAIL
            auth_config['password'] = config.FACEBOOK_PASSWORD
        if os.path.exists(config.FACEBOOK_COOKIES_FILE):
            auth_config['cookiefile'] = config.FACEBOOK_COOKIES_FILE
            
    elif platform.lower() == "pinterest":
        if config.PINTEREST_EMAIL and config.PINTEREST_PASSWORD:
            auth_config['username'] = config.PINTEREST_EMAIL
            auth_config['password'] = config.PINTEREST_PASSWORD
        if os.path.exists(config.PINTEREST_COOKIES_FILE):
            auth_config['cookiefile'] = config.PINTEREST_COOKIES_FILE
    
    return auth_config

async def handle_redirect_url(redirect_url: str, format_type: str, platform: str, max_redirects: int = 3, current_depth: int = 0) -> tuple[bool, str]:
    """
    Обрабатывает промежуточные URL редиректов с защитой от бесконечных циклов
    """
    # Защита от бесконечных редиректов
    if current_depth >= max_redirects:
        logger.warning(f"Превышено максимальное количество редиректов ({max_redirects})")
        return False, f"❌ Слишком много редиректов для {platform}"
    
    try:
        logger.info(f"Запрашиваем редирект (глубина {current_depth}): {redirect_url}")
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(redirect_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}, allow_redirects=True) as response:
                if response.status == 200:
                    # Проверяем финальный URL после редиректов
                    final_url = str(response.url)
                    logger.info(f"Финальный URL после редиректов: {final_url}")
                    
                    # Если финальный URL содержит видео/фото, скачиваем его
                    if any(ext in final_url for ext in ['.mp4', '.webm', '.jpg', '.jpeg', '.png']):
                        return await download_from_direct_url(final_url, format_type, platform)
                    
                    # Если это все еще редирект, пробуем еще раз с увеличенной глубиной
                    if "router.parklogic.com" in final_url or "download?url=" in final_url:
                        return await handle_redirect_url(final_url, format_type, platform, max_redirects, current_depth + 1)
                    
                    # Если URL не изменился (зацикливание), прекращаем
                    if final_url == redirect_url:
                        logger.warning(f"Обнаружен цикл редиректов, URL не изменился")
                        return False, f"❌ Обнаружен цикл редиректов для {platform}"
                    
                    # Иначе пробуем скачать контент ответа
                    content = await response.text()
                    logger.info(f"Контент редиректа: {len(content)} символов")
                    
                    # Ищем прямые ссылки в ответе
                    video_urls = re.findall(r'https?://[^\s"\'<>]+\.(?:mp4|webm|jpg|jpeg|png)', content)
                    if video_urls:
                        return await download_from_direct_url(video_urls[0], format_type, platform)
                    
                    return False, "❌ Не удалось найти прямую ссылку на видео"
                else:
                    return False, f"❌ Ошибка редиректа: статус {response.status}"
    
    except Exception as e:
        return False, f"❌ Ошибка обработки редиректа: {str(e)}"

async def download_via_cobalt(url: str, format_type: str) -> tuple[bool, str]:
    """
    Скачивает контент через API cobalt.tools (v10)
    """
    try:
        cobalt_instances = [
            "https://api.cobalt.tools/api/json",
            "https://cobalt.api.ghst.dev/api/json",
            "https://api.boxiv.xyz/api/json",
            "https://cobalt.sm6.zone/api/json"
        ]
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": config.DESKTOP_USER_AGENT
        }
        
        # Основной payload для Cobalt v10
        payload = {
            "url": url,
            "videoQuality": "720",
            "downloadMode": "auto" if format_type == "mp4" else "photo",
            "filenameStyle": "pretty",
            "youtubeVideoCodec": "h264"
        }
        
        for cobalt_api in cobalt_instances:
            try:
                logger.info(f"Пробуем Cobalt ({cobalt_api}) для: {url}")
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                    async with session.post(cobalt_api, headers=headers, json=payload) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("url"):
                                return await download_from_direct_url(data["url"], format_type, "cobalt")
                        elif response.status == 400:
                            # Пробуем упрощенный payload для старых версий или специфичных ошибок
                            simple_payload = {"url": url}
                            async with session.post(cobalt_api, headers=headers, json=simple_payload) as resp2:
                                if resp2.status == 200:
                                    data = await resp2.json()
                                    if data.get("url"):
                                        return await download_from_direct_url(data["url"], format_type, "cobalt")
            except Exception:
                continue
        
        return False, "SERVER_UNAVAILABLE"
    except Exception as e:
        logger.error(f"Ошибка Cobalt API: {str(e)}")
        return False, str(e)

async def download_via_tikwm(url: str) -> tuple[bool, str]:
    """
    Скачивает TikTok через API TikWM (очень надежный метод)
    """
    try:
        api_url = "https://www.tikwm.com/api/"
        payload = {'url': url, 'count': 1, 'cursor': 0, 'web': 1}
        
        logger.info(f"Пробуем TikWM API для: {url}")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(api_url, data=payload) as response:
                if response.status == 200:
                    res_json = await response.json()
                    if res_json.get('code') == 0:
                        data = res_json.get('data', {})
                        # Пробуем видео без вотермарки
                        video_url = data.get('play') or data.get('wmplay') or data.get('hdplay')
                        if video_url:
                            # TikWM иногда возвращает относительные пути
                            if video_url.startswith('/'):
                                video_url = "https://www.tikwm.com" + video_url
                            logger.info(f"TikWM нашел видео: {video_url}")
                            return await download_from_direct_url(video_url, "mp4", "tikwm")
                    
                    logger.warning(f"TikWM API вернул ошибку: {res_json.get('msg')}")
        return False, "TikWM API не нашел видео"
    except Exception as e:
        logger.error(f"Ошибка TikWM API: {str(e)}")
        return False, str(e)

async def download_via_instagram_api(url: str, format_type: str) -> tuple[bool, str]:
    """
    Специализированные методы для Instagram (POST-запросы и парсинг HTML)
    """
    # Список специализированных Instagram API
    insta_apis = [
        {"url": "https://downloadgram.org/wp-json/aio-dl/data", "method": "post", "data": {"url": url, "action": "post"}},
        {"url": "https://snapinsta.app/action.php", "method": "post", "data": {"url": url, "action": "post"}},
        {"url": f"https://saveinsta.app/api/ajaxSearch", "method": "post", "data": {"q": url, "t": "media", "lang": "en"}},
    ]
    
    for api in insta_apis:
        try:
            logger.info(f"Пробуем Instagram API: {api['url']}")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                if api['method'] == 'post':
                    async with session.post(api['url'], data=api['data'], headers={'User-Agent': config.DESKTOP_USER_AGENT, 'Referer': api['url']}) as response:
                        if response.status == 200:
                            content = await response.text()
                            
                            # Ищем видео URL в разных форматах
                            # 1. Прямые ссылки на видео
                            video_patterns = [
                                r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*',
                                r'"(https?://[^"]+video[^"]*)"',
                                r'href="(https?://[^"]+\.mp4[^"]*)"',
                                r'url["\']?\s*[:=]\s*["\'](https?://[^"\']+)["\']',
                                r'data-url="(https?://[^"]+)"',
                                r'onclick=.*?[\'"](https?://[^\'"]+)[\'"]',
                            ]
                            
                            for pattern in video_patterns:
                                matches = re.findall(pattern, content, re.IGNORECASE)
                                for match in matches:
                                    if match and ('.mp4' in match or 'video' in match.lower()):
                                        # Проверяем что это не служебный URL
                                        if not any(x in match.lower() for x in ['google', 'facebook', 'twitter', 'youtube']):
                                            logger.info(f"Найдено видео через Instagram API: {match}")
                                            return await download_from_direct_url(match, format_type, "instagram_api")
        except Exception as e:
            logger.warning(f"Ошибка Instagram API {api['url']}: {str(e)}")
            continue
    
    return False, "Instagram API не сработали"

async def download_via_youtube_api(url: str, format_type: str) -> tuple[bool, str]:
    """
    Специализированные методы для YouTube
    Примечание: Invidious не используется из-за IP-binding проблем
    """
    # Для YouTube лучше всего работает Cobalt API, который уже вызывается ранее
    # Если мы здесь, значит Cobalt не сработал - пробуем дополнительные методы
    
    video_id = None
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:shorts\/)([0-9A-Za-z_-]{11})',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            break
    
    if not video_id:
        return False, "Не удалось извлечь YouTube video ID"
    
    # Пробуем прямые ссылки на разные YouTube download сервисы
    # Эти ссылки работают без IP-binding
    ytdl_services = [
        f"https://yt.lemnoslife.com/videos?part=snippet,contentDetails&id={video_id}",
    ]
    
    for service_url in ytdl_services:
        try:
            logger.info(f"Пробуем YouTube info API: {service_url}")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(service_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and data.get('items'):
                            logger.info(f"YouTube API подтвердил видео {video_id}, возвращаем URL для yt-dlp")
                            # Возвращаем стандартный URL - yt-dlp может сработать со свежей попыткой
                            return True, f"https://www.youtube.com/watch?v={video_id}"
        except Exception as e:
            logger.warning(f"Ошибка YouTube API {service_url}: {str(e)}")
            continue
    
    return False, "YouTube API не сработали"

async def download_via_alternative_api(url: str, format_type: str) -> tuple[bool, str]:
    """
    Скачивает контент через альтернативные API сервисы
    """
    platform = None
    apis = []
    
    if any(d in url.lower() for d in ["tiktok.com", "vt.tiktok.com", "vm.tiktok.com", "m.tiktok.com"]):
        platform = "tiktok"
        apis = config.TIKTOK_APIS
    elif "instagram.com" in url.lower():
        platform = "instagram"
        apis = config.INSTAGRAM_APIS
    elif "pinterest.com" in url.lower():
        platform = "pinterest"
        apis = config.PINTEREST_APIS
    elif "facebook.com" in url.lower() or "fb.watch" in url.lower():
        platform = "facebook"
        apis = config.FACEBOOK_APIS
    
    if not platform or not apis:
        apis = config.UNIVERSAL_APIS
        platform = "universal"
    
    for api_url in apis:
        try:
            logger.info(f"Пробуем API {api_url} для {platform}")
            encoded_url = quote(url, safe='')
            full_url = api_url + encoded_url
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(full_url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                    content_bytes = await response.read()
                    content_type = response.headers.get('Content-Type', '').lower()
                    
                    # УЛУЧШЕННОЕ распознавание бинарных данных (magic bytes)
                    is_file = 'video/' in content_type or 'image/' in content_type
                    if not is_file and len(content_bytes) > 32:
                        # Проверка первых 32 байт на наличие сигнатур
                        header = content_bytes[:32]
                        # MP4 (ftyp), JPEG (ffd8), PNG (89PNG), WEBM (1A45DFA3)
                        if (b'ftyp' in header) or header.startswith(b'\xff\xd8') or header.startswith(b'\x89PNG') or header.startswith(b'\x1a\x45\xdf\xa3'):
                            is_file = True
                            if b'ftyp' in header or b'\x1a\x45\xdf\xa3' in header:
                                content_type = 'video/mp4'
                            else:
                                content_type = 'image/jpeg'
                    
                    if is_file:
                        logger.info(f"API {api_url} вернул прямой файл ({content_type}), размер: {len(content_bytes)} байт")
                        download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "telegram_bot")
                        os.makedirs(download_dir, exist_ok=True)
                        ext = '.mp4' if 'video' in content_type else '.jpg'
                        filename = f"{platform}_direct_{hash(url)%1000000}{ext}"
                        file_path = os.path.join(download_dir, filename)
                        with open(file_path, 'wb') as f:
                            f.write(content_bytes)
                        return True, file_path

                    if response.status == 200:
                        try:
                            content = content_bytes.decode('utf-8')
                        except UnicodeDecodeError:
                            content = content_bytes.decode('latin-1', errors='ignore')
                        
                        # Извлечение URL с улучшенной фильтрацией
                        # Ищем все ссылки и фильтруем их
                        found_urls = []
                        # Паттерн для поиска ссылок в кавычках или href/src
                        raw_urls = re.findall(r'href=["\'](https?://[^"\']+)["\']|src=["\'](https?://[^"\']+)["\']|["\'](https?://[^"\']+(?:video|tiktok|download|media)[^"\']*)["\']', content, re.IGNORECASE)
                        
                        for matches in raw_urls:
                            match = next((m for m in matches if m), None)
                            if not match: continue
                            
                            match_lower = match.lower()
                            # Убираем якоря и мусор
                            match = match.split('#')[0].split('?')[0] if '?' not in match else match.split('#')[0]
                            match_lower = match.lower()
                            
                            # Черный список доменов и путей
                            if any(x in match_lower for x in [
                                'twitter.com', 'facebook.com', 'instagram.com', 'youtube.com', 'googlevideo.com',
                                'ssstwitter', 'snapinsta', 'savefrom', 'snaptik', 'musicaldown', 'ssstik',
                                'fonts.googleapis', 'cdnjs', 'jquery', 'cloudflare', 'analytics', 'ads',
                                'google-analytics', 'facebook.net', 'apple.com', 'play.google', 'tiktokdownload',
                                'about', 'contact', 'privacy', 'terms', 'faq', 'blog', 'lang='
                            ]):
                                continue
                            
                            # Игнорируем ссылки заканчивающиеся на .html, .php, .css, .js
                            if any(match_lower.endswith(ext) for ext in ['.html', '.php', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg', '.gif']):
                                if not (platform == "pinterest" and any(ext in match_lower for ext in ['.jpg', '.jpeg', '.png'])):
                                    continue
                            
                            if 'tiktok' in match_lower or 'video' in match_lower or 'cdn' in match_lower or 'download' in match_lower:
                                found_urls.append(match)
                        
                        if found_urls:
                            # Приоритет прямым видео ссылкам
                            direct_video = [u for u in found_urls if any(ext in u.lower() for ext in ['.mp4', '.webm', '.m4v'])]
                            download_url = direct_video[0] if direct_video else found_urls[0]
                            
                            logger.info(f"Найден URL: {download_url}")
                            if "router.parklogic.com" in download_url or "download?url=" in download_url:
                                return await handle_redirect_url(download_url, format_type, platform)
                            
                            return await download_from_direct_url(download_url, format_type, platform)
                            
        except Exception as e:
            logger.warning(f"Ошибка API {api_url}: {str(e)}")
            continue
            
    return False, f"❌ Не удалось скачать {platform} через альтернативные API"

async def download_from_direct_url(url: str, format_type: str, platform: str) -> tuple[bool, str]:
    """
    Скачивает файл по прямой URL ссылке
    """
    try:
        download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "telegram_bot")
        os.makedirs(download_dir, exist_ok=True)
        
        # Определяем расширение файла
        if format_type == "mp4":
            ext = ".mp4"
        else:
            ext = ".jpg"
        
        # Генерируем имя файла
        filename = f"{platform}_{hash(url) % 1000000}{ext}"
        file_path = os.path.join(download_dir, filename)
        
        # Скачиваем файл
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url, headers={'User-Agent': config.DESKTOP_USER_AGENT}) as response:
                if response.status == 200:
                    with open(file_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(1024):
                            f.write(chunk)
                    
                    # Проверяем размер файла
                    if os.path.getsize(file_path) > 10240:  # Минимум 10KB
                        return True, file_path
                    else:
                        os.remove(file_path)
                        return False, "❌ Файл слишком маленький"
                else:
                    return False, f"❌ Ошибка скачивания: статус {response.status}"
    
    except Exception as e:
        return False, f"❌ Ошибка скачивания: {str(e)}"

# Инициализация бота
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Состояния для FSM
class SaveContent(StatesGroup):
    waiting_for_link = State()

async def download_content(url: str, format_type: str) -> tuple[bool, str]:
    """
    Скачивает контент с улучшенными параметрами yt-dlp
    
    Args:
        url: URL для скачивания
        format_type: "mp4" для видео, "jpg" для фото
        
    Returns:
        tuple[bool, str]: (успех, путь_к_файлу или сообщение_об_ошибке)
    """
    original_url = url
    
    try:
        # Создаем папку для скачивания если её нет
        download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "telegram_bot")
        os.makedirs(download_dir, exist_ok=True)
        
        output_path = os.path.join(download_dir, "%(title)s.%(ext)s")
        
        # Proxy support for yt-dlp (useful for YouTube 403 on datacenter IPs)
        proxies_raw = os.getenv("YTDLP_PROXIES", "").strip()
        proxy_single = os.getenv("YTDLP_PROXY", "").strip()
        if not proxy_single:
            proxy_single = os.getenv("PROXY_URL", "").strip()  # Fallback for Railway
        
        # No proxy by default - yt-dlp works better without proxy on Railway for YouTube
        if not proxy_single:
            logger.info("No proxy configured - using direct connection (recommended for Railway)")

        proxies: list[str] = []
        if proxies_raw:
            proxies = [p.strip() for p in proxies_raw.split(",") if p.strip()]
        elif proxy_single:
            proxies = [proxy_single]

        selected_proxy = random.choice(proxies) if proxies else None
        if selected_proxy:
            logger.info("yt-dlp proxy enabled")
            logger.info("Selected proxy: %s", _mask_proxy(selected_proxy))
        else:
            logger.warning("No proxy configured - YTDLP_PROXY or YTDLP_PROXIES not set or empty")

        # Профессиональные параметры yt-dlp (Stacher-style)
        ydl_opts = {
            'quiet': False,
            'no_warnings': False,
            'outtmpl': output_path,
            'socket_timeout': 120,  # Увеличенный таймаут для медленных соединений
            'noplaylist': True,
            'extract_flat': False,
            'geo_bypass': True,
            'no_color': False,
            'extractor_retries': 15,
            'fragment_retries': 15,
            'retries': 15,
            'file_access_retries': 10,
            'fragment_timeout': 180,
            'http_chunk_size': 1048576,  # 1MB чанки для стабильности
            'ignoreerrors': False,  # Ошибки должны вызываться для корректной обработки
            'no_check_certificate': True,
            'prefer_free_formats': True,
            'add_header': [
                'Accept-Language: en-US,en;q=0.9',
                'Sec-Ch-Ua: "Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'Sec-Ch-Ua-Mobile: ?0',
                'Sec-Ch-Ua-Platform: "Windows"',
            ]
        }

        if selected_proxy:
            ydl_opts['proxy'] = selected_proxy
            logger.info("yt-dlp will use proxy: %s", _mask_proxy(selected_proxy))
        
        # Улучшенные параметры для TikTok (без прокси)
        if any(d in url.lower() for d in ["tiktok.com", "vt.tiktok.com", "vm.tiktok.com", "m.tiktok.com"]):
            ydl_opts.update({
                'extractor_args': {
                    'tiktok': {
                        'api_hostname': 'api16-normal-c-useast1a.tiktokv.com',
                        'enable_headers': True,
                        'app_name': 'musical_ly',
                        'device_id': '7234567890123456789',
                    }
                },
                'format': 'best[filesize<50M][ext=mp4]/best[filesize<50M]/worst[ext=mp4]/worst',
                'http_headers': {
                    'User-Agent': config.MOBILE_USER_AGENT,
                    'Referer': 'https://www.tiktok.com/',
                },
                'socket_timeout': 60,
                'retries': 3,
            })
            logger.info("TikTok download without proxy due to connection issues")
        
        # Улучшенные параметры для Instagram
        elif "instagram.com" in url.lower():
            ydl_opts.update({
                'extractor_args': {
                    'instagram': {
                        'include_ads': False,
                        'enable_headers': True,
                    }
                },
                'format': 'best[filesize<50M][ext=mp4]/best[filesize<50M]/worst[ext=mp4]/worst',
                'http_headers': {
                    'User-Agent': config.DESKTOP_USER_AGENT,
                    'Referer': 'https://www.instagram.com/',
                },
            })
        
        # Улучшенные параметры для Pinterest
        elif "pinterest.com" in url.lower():
            ydl_opts.update({
                'format': 'best[ext=jpg]/best[ext=jpeg]/best[ext=png]/best',
                'http_headers': {
                    'User-Agent': config.DESKTOP_USER_AGENT,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                },
                'socket_timeout': 90,
                'extractor_retries': 15,
                'fragment_retries': 15,
            })
        
        # Улучшенные параметры для Facebook
        elif "facebook.com" in url.lower() or "fb.watch" in url.lower():
            ydl_opts.update({
                'format': 'best[filesize<50M][ext=mp4]/best[filesize<50M]/worst[ext=mp4]/worst',
                'http_headers': {
                    'User-Agent': config.DESKTOP_USER_AGENT,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,video/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                },
                'socket_timeout': 90,
                'extractor_retries': 15,
                'fragment_retries': 15,
            })
        
        # Простые параметры для YouTube
        elif "youtube.com" in url.lower() or "youtu.be" in url.lower():
            ydl_opts.update({
                'format': 'worst[ext=mp4]/worst',
                'socket_timeout': 60,
                'retries': 3,
            })

            if selected_proxy:
                logger.info("YouTube download will use yt-dlp proxy")
        
        # Устанавливаем формат в зависимости от типа
        if format_type == "mp4":
            ydl_opts['format'] = ydl_opts.get('format', 'best[filesize<50M][ext=mp4]/best[filesize<50M]/worst[ext=mp4]/worst')
        elif format_type == "jpg":
            ydl_opts['writethumbnail'] = True
            ydl_opts['write_all_thumbnails'] = True
            ydl_opts['skip_download'] = False
            ydl_opts['format'] = 'best[ext=jpg]/best[ext=jpeg]/best[ext=png]/best'
            ydl_opts['postprocessors'] = []
        
        # Запускаем скачивание в отдельном потоке с увеличенным таймаутом
        loop = asyncio.get_event_loop()
        
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # Получаем точный путь к скачанному файлу
                file_path = ydl.prepare_filename(info)
                return file_path
        
        try:
            # Увеличенные таймауты для разных платформ
            if "instagram.com" in url.lower():
                timeout = 180  # 3 минуты для Instagram
            elif "tiktok.com" in url.lower():
                timeout = 120  # 2 минуты для TikTok
            elif "pinterest.com" in url.lower():
                timeout = 120  # 2 минуты для Pinterest
            elif "facebook.com" in url.lower():
                timeout = 120  # 2 минуты для Facebook
            else:
                timeout = 90   # 1.5 минуты для остальных
                
            file_path = await asyncio.wait_for(loop.run_in_executor(None, download), timeout=timeout)
            
            # Проверяем что файл существует и не пустой
            if os.path.exists(file_path) and os.path.getsize(file_path) > 1024:
                return True, file_path
            else:
                return False, "❌ Файл не был скачан или пуст"
            
        except asyncio.TimeoutError:
            return False, "❌ Превышено время ожидания. Попробуйте другую ссылку или повторите позже."
        except Exception as e:
            logger.error(f"Ошибка при скачивании: {str(e)}")
            return False, f"❌ Ошибка скачивания: {str(e)[:200]}"
        
    except Exception as e:
        error_msg = str(e)
        
        # Если это ошибка платформы, пробуем альтернативные API
        if any(platform in error_msg.lower() for platform in ["tiktok", "instagram", "pinterest", "facebook"]) or \
           any(error in error_msg.lower() for error in ["rate-limit", "login required", "403", "sigi state", "unable to extract", "file is empty", "fragment", "forbidden"]):
            
            # СПЕЦИАЛЬНЫЙ ФИКС ДЛЯ YOUTUBE (403 ошибки)
            if any(d in original_url.lower() for d in ["youtube.com", "youtu.be", "youtube.com/shorts"]) or \
               any(error in error_msg.lower() for error in ["403", "forbidden", "precondition check failed"]):
                logger.info(f"Пробуем специализированные YouTube API")
                yt_success, yt_result = await download_via_youtube_api(original_url, format_type)
                if yt_success:
                    return True, yt_result
            
            # СПЕЦИАЛЬНЫЙ ФИКС ДЛЯ TIKTOK (TikWM)
            if any(d in original_url.lower() for d in ["tiktok.com", "vt.tiktok.com", "vm.tiktok.com", "m.tiktok.com"]):
                logger.info(f"Пробуем TikWM как специализированный метод для TikTok")
                tikwm_success, tikwm_result = await download_via_tikwm(original_url)
                if tikwm_success:
                    return True, tikwm_result

            # СПЕЦИАЛЬНЫЙ ФИКС ДЛЯ INSTAGRAM
            if "instagram.com" in original_url.lower():
                logger.info(f"Пробуем специализированные Instagram API")
                insta_success, insta_result = await download_via_instagram_api(original_url, format_type)
                if insta_success:
                    return True, insta_result

            logger.info(f"Пробуем Cobalt API как основной метод для: {original_url}")
            
            # Сначала пробуем супер-надежный Cobalt API
            cobalt_success, cobalt_result = await download_via_cobalt(original_url, format_type)
            if cobalt_success:
                return True, cobalt_result
            
            logger.info(f"Cobalt API не сработал, пробуем список альтернативных API")
            return await download_via_alternative_api(original_url, format_type)
        
        # Обработка специфичных ошибок
        if "No video formats found" in error_msg:
            if format_type == "jpg":
                return False, "❌ На этой странице нет фото для скачивания\n\nПопробуйте:\n• Instagram\n• Pinterest пост с фото\n• Facebook фото\n• Twitter"
            else:
                return False, "❌ На этой странице нет видео для скачивания\n\nПопробуйте:\n• YouTube\n• Instagram рилсы\n• TikTok\n• Facebook видео"
        elif "rate-limit" in error_msg.lower() and "instagram" in error_msg.lower():
            return False, "❌ Instagram временно ограничил доступ\n\nПопробуйте:\n• Подождать 1-2 минуты и повторить\n• YouTube или TikTok видео\n• Другую ссылку Instagram"
        elif "login required" in error_msg.lower() and "instagram" in error_msg.lower():
            return False, "❌ Instagram требует авторизацию\n\nПопробуйте:\n• Публичные аккаунты Instagram\n• YouTube или TikTok\n• Facebook видео"
        elif "sigi state" in error_msg.lower() or "tiktok" in error_msg.lower():
            return False, "❌ TikTok временно недоступен\n\nПопробуйте:\n• YouTube или Instagram видео\n• Повторить попытку через 5 минут\n• Другую ссылку TikTok"
        elif "facebook" in error_msg.lower() or "pfbid" in error_msg:
            return False, "❌ Facebook требует авторизацию\n\nПопробуйте загрузить видео/фото:\n• YouTube\n• Instagram\n• TikTok\n• Twitter"
        elif "pinterest" in error_msg.lower() or "403" in error_msg:
            return False, "❌ Pinterest временно заблокирован\n\nПопробуйте:\n• YouTube или VK видео\n• Instagram фото\n• Twitter изображения"
        elif "Unsupported URL" in error_msg:
            return False, "❌ Неподдерживаемый URL\n\nПроверьте ссылку и попробуйте снова"
        elif "404" in error_msg or "not found" in error_msg.lower():
            return False, "❌ Контент не найден или удален"
        elif "permission" in error_msg.lower():
            return False, "❌ Нет доступа (может быть приватным или требует авторизации)"
        else:
            # Сокращаем сообщение об ошибке
            clean_error = error_msg
            if "\x1b[" in error_msg:
                import re
                clean_error = re.sub(r'\x1b\[[0-9;]*m', '', error_msg)
            
            return False, f"❌ Ошибка: {clean_error[:200]}"

async def send_file(message: types.Message, file_path: str, format_type: str):
    """Отправляет файл пользователю"""
    try:
        # Проверяем размер файла
        file_size = os.path.getsize(file_path)
        max_size = 50 * 1024 * 1024  # 50MB для Telegram
        
        if file_size > max_size:
            await message.answer(
                f"❌ Файл слишком большой ({file_size/1024/1024:.1f}MB)\n"
                f"Максимальный размер: 50MB\n\n"
                f"Попробуйте видео меньшего качества или другую ссылку."
            )
            return
        
        # Отправляем файл
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
        
        # Удаляем файл после отправки
        try:
            os.remove(file_path)
        except:
            pass
            
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки файла: {str(e)}")

# Обработчик команды /start
@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    """Приветствие и главное меню"""
    
    # Создаем клавиатуру
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📥 Сохранить контент")],
            [KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="⚙️ Настройки")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я бот для скачивания видео и фото из социальных сетей.\n\n"
        "Мои возможности:\n"
        "✅ Скачивание видео с Instagram, TikTok, YouTube\n"
        "✅ Загрузка фото и историй с Instagram\n"
        "✅ Скачивание фото с Pinterest, Facebook\n"
        "✅ Поддержка множества других платформ\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )

# Обработчик кнопки "Сохранить контент"
@dp.message(lambda message: message.text and message.text == "📥 Сохранить контент")
async def save_content_start(message: types.Message, state: FSMContext):
    """Начало процесса сохранения контента"""
    
    await state.set_state(SaveContent.waiting_for_link)
    await message.answer(
        "📎 Пожалуйста, отправьте ссылку на видео или фото, который хотите скачать.\n\n"
        "Поддерживаемые платформы (видео и фото):\n"
        "• Instagram (видео, фото, рилсы, истории) ✅\n"
        "• TikTok (видео) ✅\n"
        "• YouTube (видео) ✅\n"
        "• Twitter (видео, фото) ✅\n"
        "• VKontakte (видео, фото) ✅\n"
        "• Pinterest (фото) ✅\n"
        "• Facebook (видео, фото) ✅\n\n"
        "Просто отправьте ссылку 👇"
    )

# Обработчик текстовых сообщений (ссылки)
@dp.message(SaveContent.waiting_for_link)
async def process_link(message: types.Message, state: FSMContext):
    """Обработка ссылки на контент"""
    
    # Проверяем, содержит ли сообщение текст
    if not message.text:
        await message.answer("❌ Пожалуйста, отправьте текстовое сообщение с ссылкой")
        return
    
    # Проверяем, содержит ли сообщение ссылку
    if not (message.text.startswith("http://") or message.text.startswith("https://")):
        await message.answer("❌ Пожалуйста, отправьте корректную ссылку (начинающуюся с http:// или https://)")
        return
    
    # Сохраняем ссылку в состояние
    await state.update_data(link=message.text)
    
    # Показываем варианты формата
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📹 Видео (MP4)", callback_data="format_mp4")],
            [InlineKeyboardButton(text="🖼️ Фото (JPG)", callback_data="format_jpg")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ]
    )
    
    await message.answer(
        f"✅ Ссылка получена!\n\n"
        f"Выберите формат скачивания:\n"
        f"<code>{message.text}</code>",
        reply_markup=kb,
        parse_mode="html"
    )

# Обработчик кнопки "Помощь"
@dp.message(lambda message: message.text and message.text == "ℹ️ Помощь")
async def help_handler(message: types.Message):
    """Показывает справку"""
    
    help_text = """
🤖 **Справка по боту**

Скачивайте видео и фото с популярных платформ!

**Поддерживаемые платформы:**
• Instagram (фото, рилсы) ⚠️ Временно проблемы
• TikTok (видео) ⚠️ Часто недоступен  
• YouTube (видео) ✅ Работает отлично
• Pinterest (фото) ⚠️ Временно заблокирован
• Facebook (видео, фото) ⚠️ Требует авторизации
• Twitter (видео, фото) ✅ Работает стабильно
• VK (видео, фото) ✅ Работает надежно

**Рекомендуемые платформы:**
✅ YouTube - самая надежная
✅ VK - отличный выбор
✅ Twitter - стабильно работает

**Как использовать:**
1. Нажмите "📥 Сохранить контент"
2. Отправьте ссылку на видео/фото
3. Выберите формат
4. Готово! 🎉

**Временные ограничения:**
• Instagram, TikTok и Pinterest часто блокируют скачивание
• Facebook требует авторизацию для приватного контента
• Максимальный размер файла: 50MB

**Совет:**
Используйте YouTube, VK или Twitter для лучших результатов!
    """
    
    await message.answer(help_text, parse_mode="markdown")

# Обработчик кнопки "Настройки"
@dp.message(lambda message: message.text and message.text == "⚙️ Настройки")
async def settings_handler(message: types.Message):
    """Показывает настройки"""
    
    await message.answer(
        "⚙️ **Настройки бота**\n\n"
        "🟢 Статус: Активен\n"
        "📊 Загружено файлов: 0\n"
        "⏰ Время работы: 0 минут\n\n"
        "Бот работает в штатном режиме!",
        parse_mode="markdown"
    )

# Обработчик callback кнопок
@dp.callback_query()
async def callback_handler(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик всех callback кнопок"""
    
    if callback.data == "cancel":
        await callback.message.delete()
        await state.clear()
        await callback.answer("❌ Операция отменена")
        
    elif callback.data == "format_mp4":
        processing_msg = await callback.message.edit_text(
            "⏳ Скачиваю видео...\n\n"
            "Это может занять до 2 минут"
        )
        await callback.answer("📥 Загрузка началась!")
        
        # Получаем ссылку из состояния
        data = await state.get_data()
        url = data.get("link")
        
        if url:
            success, result = await download_content(url, "mp4")
            
            if success:
                await send_file(processing_msg, result, "mp4")
            else:
                await processing_msg.edit_text(f"❌ Ошибка загрузки:\n{result}")
        
        await state.clear()
        
    elif callback.data == "format_jpg":
        processing_msg = await callback.message.edit_text(
            "⏳ Скачиваю фото...\n\n"
            "Это может занять до 1 минуты"
        )
        await callback.answer("📥 Загрузка началась!")
        
        # Получаем ссылку из состояния
        data = await state.get_data()
        url = data.get("link")
        
        if url:
            success, result = await download_content(url, "jpg")
            
            if success:
                await send_file(processing_msg, result, "jpg")
            else:
                await processing_msg.edit_text(f"❌ Ошибка загрузки:\n{result}")
        
        await state.clear()
        
    elif callback.data == "start_save":
        await state.set_state(SaveContent.waiting_for_link)
        await callback.message.delete()
        await callback.message.answer("📎 Отправьте ссылку на контент")
        
    elif callback.data == "help":
        help_text = """
🤖 **Справка по боту**

Скачивайте видео и фото с популярных платформ!

**Поддерживаемые платформы:**
• Instagram (видео, фото, рилсы)
• TikTok (видео)
• YouTube (видео)
• Pinterest (фото)
• Facebook (видео, фото)
• Twitter (видео, фото)
• VK (видео, фото)
• И еще много других!
        """
        await callback.message.edit_text(help_text, parse_mode="markdown")
    
    # Убираем await callback.answer() чтобы избежать timeout

# Обработчик команды /status
@dp.message(Command("status"))
async def status_handler(message: types.Message):
    """Показывает статус бота"""
    
    await message.answer(
        "✅ **Статус бота:**\n\n"
        "🟢 Бот активен и работает\n"
        "📊 Сервер: Online\n"
        "⏰ Время работы: 24/7\n"
        "🔧 Все системы в норме!",
        parse_mode="markdown"
    )

# Запуск бота
async def main():
    logger.info("Бот запущен!")
    
    # Debug: log all environment variables (masked for sensitive data)
    env_debug = {k: ("***" if "TOKEN" in k or "PASS" in k or "SECRET" in k or "KEY" in k else v) 
                 for k, v in os.environ.items() if "PROXY" in k or "TOKEN" in k}
    logger.info("Environment variables debug: %s", env_debug)
    
    proxy_single = os.getenv("YTDLP_PROXY", "").strip()
    proxies_raw = os.getenv("YTDLP_PROXIES", "").strip()
    if not proxy_single:
        proxy_single = os.getenv("PROXY_URL", "").strip()  # Fallback for Railway
    if proxy_single or proxies_raw:
        logger.info("YTDLP proxy configured: %s", _mask_proxy(proxy_single) if proxy_single else "YTDLP_PROXIES set")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

# Веб-сервер для Replit (чтобы был открыт порт)
from flask import Flask
import threading

web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Telegram Bot is running!"

def run_web_server():
    web_app.run(host='0.0.0.0', port=5000)

# Запускаем веб-сервер в отдельном потоке
web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()

print("Веб-сервер запущен на порту 5000 для Replit")