import os
import sys
import logging
from datetime import datetime
import telebot
import yt_dlp
import sqlite3
from telebot import types

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
class Config:
    BOT_TOKEN = '7134077321:AAE9_CAkLAnZuDv5nEHxNgamzffhYb8m_N0'  # Replace with your bot token
    ADMIN_IDS = [7439517139]  # Replace with your Telegram user ID
    DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
    DATABASE_PATH = 'bot_stats.db'
    MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB file size limit for Telegram
    CHUNK_SIZE = 100 * 1024 * 1024  # 100 MB chunks for uploading
    DOWNLOAD_TIMEOUT = 12000  # 200 minutes timeout for downloads
    UPLOAD_TIMEOUT = 36000  # 10 hour timeout for uploads

# Ensure directories exist
os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

# Initialize bot with custom timeout
bot = telebot.TeleBot(Config.BOT_TOKEN)

# Database Management (Unchanged)
class DatabaseManager:
    @staticmethod
    def init_database():
        conn = sqlite3.connect(Config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                join_date TEXT,
                total_downloads INTEGER DEFAULT 0,
                last_download_date TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS download_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                video_title TEXT,
                video_url TEXT,
                download_date TEXT,
                file_size INTEGER
            )
        ''')
        conn.commit()
        conn.close()

    @staticmethod
    def update_user_stats(user_id, username, first_name, last_name):
        conn = sqlite3.connect(Config.DATABASE_PATH)
        cursor = conn.cursor()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,))
        existing_user = cursor.fetchone()
        if not existing_user:
            cursor.execute('''
                INSERT INTO user_stats
                (user_id, username, first_name, last_name, join_date, total_downloads)
                VALUES (?, ?, ?, ?, ?, 0)
            ''', (user_id, username, first_name, last_name, current_time))
        cursor.execute('''
            UPDATE user_stats
            SET total_downloads = total_downloads + 1,
                last_download_date = ?
            WHERE user_id = ?
        ''', (current_time, user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def log_download(user_id, video_title, video_url, file_size):
        conn = sqlite3.connect(Config.DATABASE_PATH)
        cursor = conn.cursor()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            INSERT INTO download_logs
            (user_id, video_title, video_url, download_date, file_size)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, video_title, video_url, current_time, file_size))
        conn.commit()
        conn.close()

# YouTube Downloader
class YouTubeDownloader:
    @staticmethod
    def download_video(url, output_path):
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': output_path,
            'nooverwrites': True,
            'no_warnings': True,
            'ignoreerrors': False,
            'merge_output_format': 'mp4',
            'max_filesize': 2 * 1024 * 1024 * 1024,  # 2GB max
            'timeout': Config.DOWNLOAD_TIMEOUT,
            'progress_hooks': [YouTubeDownloader.download_progress],
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info_dict)
                video_title = info_dict.get('title', 'Unknown Title')
                duration = info_dict.get('duration', 0)
                return filename, video_title, duration
        except Exception as e:
            logger.error(f"YouTube download error: {e}")
            return None, None, None

    @staticmethod
    def download_progress(d):
        if d['status'] == 'downloading':
            downloaded_bytes = d.get('downloaded_bytes', 0)
            total_bytes = d.get('total_bytes_estimate', 0)
            if total_bytes > 0:
                percentage = downloaded_bytes / total_bytes * 100
                logger.info(f"Downloading: {percentage:.2f}%")

    @staticmethod
    def retry_upload(bot, chat_id, file_path, caption, retries=3):
        for attempt in range(retries):
            try:
                with open(file_path, 'rb') as video:
                    bot.send_document(
                        chat_id,
                        video,
                        caption=caption,
                        timeout=Config.UPLOAD_TIMEOUT
                    )
                return True
            except Exception as e:
                logger.error(f"Upload attempt {attempt + 1} failed: {e}")
                if attempt == retries - 1:
                    bot.send_message(chat_id, "❌ Upload failed after multiple attempts.")
                    return False

# Handlers
@bot.message_handler(commands=['start'])
def start_command(message):
    DatabaseManager.update_user_stats(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📥 Download Video", "❓ Help", "📊 Statistics")
    bot.send_message(
        message.chat.id,
        "🤖 *YouTube Video Downloader*

Send me a YouTube video link to download it.",
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.message_handler(func=lambda message: 'youtube.com' in message.text or 'youtu.be' in message.text)
def download_video_handler(message):
    processing = bot.send_message(message.chat.id, "⏳ Processing video...")
    try:
        unique_filename = os.path.join(
            Config.DOWNLOAD_DIR,
            f'{message.from_user.id}_{datetime.now().strftime("%Y%m%d%H%M%S")}.mp4'
        )
        downloaded_file, video_title, video_duration = YouTubeDownloader.download_video(
            message.text,
            unique_filename
        )
        if downloaded_file:
            caption = f"📹 *{video_title or 'YouTube Video'}*
⏱️ Duration: {video_duration or 'Unknown'} seconds"
            upload_success = YouTubeDownloader.retry_upload(bot, message.chat.id, downloaded_file, caption)
            if upload_success:
                os.remove(downloaded_file)
            else:
                bot.send_message(message.chat.id, "❌ Failed to upload the video.")
        else:
            bot.send_message(message.chat.id, "❌ Failed to download the video.")
    except Exception as e:
        logger.error(f"Download handler error: {e}")
        bot.send_message(message.chat.id, f"❌ Error: {str(e)}")

def main():
    DatabaseManager.init_database()
    logger.info("Bot started successfully!")
    bot.polling(none_stop=True)

if __name__ == '__main__':
    main()
