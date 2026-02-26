"""
Photo Download Bot - Separate bot for downloading photos only
Optimized for photo downloads from social media platforms.
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

# ==================== КОНФИГУРАЦИЯ ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Константы
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB для Telegram
MIN_FILE_SIZE = 1024  # 1KB минимум
FILENAME_MAX_LEN = 80  # Макс. длина имени файла

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
    PLATFORM_PATTERNS = {
        "instagram": ["instagram.com"],
        "pinterest": ["pinterest.com"],
        "facebook": ["facebook.com", "fb.watch"],
    }
    url_lower = url.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return platform
    return None

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

# ==================== API МЕТОДЫ ДЛЯ ФОТО ====================
async def download_from_direct_url(url: str, format_type: str, platform: str) -> tuple[bool, str]:
    """Скачивает файл по прямой URL."""
    try:
        download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "telegram_bot")
        os.makedirs(download_dir, exist_ok=True)

        ext = ".jpg"  # Всегда JPG для фото
        filename = f"{platform}_direct_{hash(url) % 1000000}{ext}"
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
        logger.error(f"SaveFrom API Exception: {e}")

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

# ==================== ОСНОВНАЯ ЛОГИКА ДЛЯ ФОТО ====================
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

# ==================== TELEGRAM HANDLERS ====================
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

HELP_TEXT_PHOTO = """🤖 **Photo Download Bot**

Скачивайте фото с популярных платформ!

**Поддерживаемые:**
• Instagram (фото, посты)
• Pinterest (фото)
• Facebook (фото)

**Как использовать:**
1. Нажмите "📸 Скачать фото"
2. Отправьте ссылку на фото
3. Готово! 🎉

Макс. размер файла: 50MB"""

@dp.message(CommandStart())
async def start_handler_photo(message: types.Message, state: FSMContext):
    """Приветствие для фото бота."""
    await state.clear()

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📸 Скачать фото")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True
    )

    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я бот для скачивания фото из социальных сетей.\n\n"
        "✅ Instagram, Pinterest, Facebook\n\n"
        "Выберите действие:",
        reply_markup=keyboard
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
async def help_handler_photo(message: types.Message):
    """Показывает справку."""
    await message.answer(HELP_TEXT_PHOTO, parse_mode="markdown")

@dp.message(Command("status"))
async def status_handler_photo(message: types.Message):
    """Статус фото бота."""
    await message.answer(
        "✅ **Статус:** Фото бот активен и работает!",
        parse_mode="markdown"
    )

# ==================== ЗАПУСК ====================
async def main():
    """Запуск фото бота."""
    logger.info("Фото бот запущен!")

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
