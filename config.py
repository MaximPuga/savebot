# Telegram Bot Token
TELEGRAM_TOKEN = "8654931118:AAEcsEsVX0tui9RQFRyvuBjQJqgY168ACMs"

# Альтернативные API сервисы (без авторизации)
# Эти сервисы скачивают через свои API, не требуют твоих данных

# TikTok download APIs (исключаем parklogic.com сервисы)
TIKTOK_APIS = [
    "https://snaptik.app/abc?url=",
    "https://tiktokdownload.online/?url=",
    "https://ttdownloader.io/download?url=",
    "https://tiktokonly.net/download?url=",
    "https://lovetik.com/download?url=",
    "https://ttdown.org/download?url=",
    "https://tiktokdownload.app/download?url=",
    "https://tikmate.cc/download?url=",
    "https://snaptik.cc/download?url=",
    "https://tiktokmate.net/download?url=",
    "https://tiktokdownload.cc/download?url=",
    "https://snap-tik.com/download?url=",
    "https://tiktokdownload.net/download?url=",
    "https://tikmate.info/download?url="
]

# Instagram download APIs (приоритет - самые надежные первые)
INSTAGRAM_APIS = [
    "https://insta-mood.com/download?url=",
    "https://downloadgram.org/download?url=",
    "https://instasave.org/download?url=",
    "https://imginn.com/download?url=",
    "https://instadp.com/download?url=",
    "https://instagramdownloader.com/download?url=",
    "https://instamod.net/download?url=",
    "https://instagram-video-downloader.com/download?url=",
    "https://insta-save.net/download?url=",
    "https://download-instagram-videos.com/download?url="
]

# Pinterest download APIs (приоритет - самые надежные первые)
PINTEREST_APIS = [
    "https://pinterestdownloader.com/download?url=",
    "https://pinloader.com/download?url=",
    "https://pinterestvideo.download/download?url=",
    "https://pinterestsave.net/download?url=",
    "https://pinterestdown.org/download?url=",
    "https://pinterest-media-downloader.com/download?url="
]

# Facebook download APIs (приоритет - самые надежные первые)
FACEBOOK_APIS = [
    "https://fbdown.net/download?url=",
    "https://getfb.net/download?url=",
    "https://fdown.net/download?url=",
    "https://fbvideo-downloader.com/download?url=",
    "https://facebook-video-downloader.com/download?url=",
    "https://fbdownloader.com/download?url=",
    "https://savefacebook.net/download?url="
]

# Universal API (работает со всеми платформами)
UNIVERSAL_APIS = [
    "https://savefrom.net/download?url=",
    "https://ru.savefrom.net/download?url=",
    "https://en.savefrom.net/download?url=",
    "https://www.savefrom.net/download?url="
]

# Social Media Credentials for yt-dlp (ОСТАВЛЯЕМ ПУСТЫМИ)
# Эти данные нужны для обхода блокировок Instagram, TikTok, Facebook, Pinterest

# Instagram credentials (если есть)
INSTAGRAM_USERNAME = ""
INSTAGRAM_PASSWORD = ""

# TikTok credentials (если есть) 
TIKTOK_USERNAME = ""
TIKTOK_PASSWORD = ""

# Facebook credentials (если есть)
FACEBOOK_EMAIL = ""
FACEBOOK_PASSWORD = ""

# Pinterest credentials (если есть)
PINTEREST_EMAIL = ""
PINTEREST_PASSWORD = ""

# Cookies файлы (путь к файлам cookies)
# Можно получить через браузер: https://github.com/yt-dlp/yt-dlp/wiki/Extractors#cookies
INSTAGRAM_COOKIES_FILE = "instagram_cookies.txt"
TIKTOK_COOKIES_FILE = "tiktok_cookies.txt"
FACEBOOK_COOKIES_FILE = "facebook_cookies.txt"
PINTEREST_COOKIES_FILE = "pinterest_cookies.txt"

# User-Agent strings
MOBILE_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
DESKTOP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
