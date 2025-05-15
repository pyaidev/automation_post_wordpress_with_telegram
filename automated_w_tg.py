import os
import time
import requests
import logging
from requests.auth import HTTPBasicAuth
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from datetime import datetime
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения из .env файла
load_dotenv()

# Telegram Bot данные
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')  # ID канала (можно получить через @username_to_id_bot)
ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')  # ID администратора для управления ботом

# WordPress API данные
WP_URL = os.getenv('WP_URL')
WP_API_URL = f"{WP_URL}/wp-json/wp/v2/posts"
WP_USERNAME = os.getenv('WP_USERNAME')
WP_PASSWORD = os.getenv('WP_PASSWORD') 

# Настройка авторизации для WordPress
auth = HTTPBasicAuth(WP_USERNAME, WP_PASSWORD)
headers = {"Content-Type": "application/json"}

# Функция для получения информации о медиа по ID
def get_media_info(media_id):
    try:
        media_url = f"{WP_URL}/wp-json/wp/v2/media/{media_id}"
        response = requests.get(media_url, auth=auth)
        
        if response.status_code == 200:
            return response.json()
        
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении информации о медиа: {e}")
        return None

# Функция для загрузки медиа в WordPress
async def upload_media_to_wordpress(media_url, mime_type):
    try:
        # Загрузка медиа
        media_response = requests.get(media_url)
        if media_response.status_code == 200:
            file_name = f"telegram_media_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            file_extension = ".jpg" if mime_type == "image/jpeg" else ".mp4"
            
            # Загрузка медиа в WordPress
            media_headers = {
                "Content-Disposition": f'attachment; filename="{file_name}{file_extension}"',
                "Content-Type": mime_type
            }
            
            media_url_wp = f"{WP_URL}/wp-json/wp/v2/media"
            media_response = requests.post(
                media_url_wp,
                headers=media_headers,
                auth=auth,
                data=media_response.content
            )
            
            if media_response.status_code in [200, 201]:
                # Получение ID загруженного медиа
                media_id = media_response.json().get('id')
                
                # Ждем, пока медиа обработается WordPress
                time.sleep(2)
                
                return media_id
            else:
                logger.error(f"Ошибка загрузки медиа в WordPress: {media_response.status_code}, {media_response.text}")
        
        return None
    except Exception as e:
        logger.error(f"Ошибка при загрузке медиа: {e}")
        return None

# Функция для публикации поста в WordPress
async def post_to_wordpress(title, content, featured_media_id=None):
    try:
        # Подготовка данных поста
        post_data = {
            "title": title,
            "content": content,
            "status": "publish"
        }
        
        # Если есть медиа для миниатюры, добавим его
        if featured_media_id:
            post_data["featured_media"] = featured_media_id
        
        # Отправка запроса на создание поста
        response = requests.post(WP_API_URL, headers=headers, auth=auth, json=post_data)
        
        if response.status_code == 201:
            logger.info(f"Пост успешно создан: {response.json().get('link')}")
            return True, response.json().get('link')
        else:
            logger.error(f"Ошибка создания поста: {response.status_code}, {response.text}")
            return False, None
    except Exception as e:
        logger.error(f"Ошибка при публикации поста: {e}")
        return False, None

# Функция для проверки соединения с WordPress
async def check_wordpress_connection():
    try:
        response = requests.get(f"{WP_URL}/wp-json", timeout=10)
        if response.status_code == 200:
            logger.info("Соединение с WordPress установлено успешно.")
            return True
        else:
            logger.error(f"Ошибка соединения с WordPress: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Ошибка при подключении к WordPress: {e}")
        return False

# Функция для безопасной отправки сообщения администратору
async def send_admin_message(bot, text):
    try:
        # Проверяем, что ADMIN_USER_ID корректный
        if not ADMIN_USER_ID or ADMIN_USER_ID == "":
            logger.warning("ADMIN_USER_ID не установлен в .env файле")
            return False
        
        await bot.send_message(chat_id=ADMIN_USER_ID, text=text)
        return True
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение администратору: {e}")
        return False

# Обработчик команды /start
async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_USER_ID:
        await update.message.reply_text("Извините, у вас нет доступа к этому боту.")
        return
    
    await update.message.reply_text(
        "Привет! Я бот для интеграции Telegram канала с WordPress.\n"
        "Используйте /status для проверки соединения.\n"
        "Я автоматически буду публиковать посты из канала на сайт."
    )

# Обработчик команды /status
async def status(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_USER_ID:
        await update.message.reply_text("Извините, у вас нет доступа к этому боту.")
        return
    
    wp_status = await check_wordpress_connection()
    
    if wp_status:
        await update.message.reply_text("✅ WordPress подключен успешно.")
    else:
        await update.message.reply_text("❌ Ошибка подключения к WordPress.")

# Функция для обработки медиа-группы
async def process_media_group(context: CallbackContext):
    media_group_id = context.job.data
    media_group = context.bot_data['media_groups'].get(media_group_id)
    
    if not media_group:
        return
    
    # Создаем галерею из изображений и видео
    gallery_html = '<div class="wp-block-gallery"><ul class="blocks-gallery-grid">'
    media_ids = []
    
    for item in media_group['media']:
        media_type = item['type']
        media_url = item['url']
        
        mime_type = "image/jpeg" if media_type == 'photo' else "video/mp4"
        media_id = await upload_media_to_wordpress(media_url, mime_type)
        
        if media_id:
            media_ids.append(media_id)
            
            if media_type == 'photo':
                # Получаем URL изображения из WordPress
                media_info = get_media_info(media_id)
                if media_info and 'source_url' in media_info:
                    gallery_html += f'<li class="blocks-gallery-item"><figure><img src="{media_info["source_url"]}" alt=""/></figure></li>'
    
    gallery_html += '</ul></div>'
    
    # Создаем контент с галереей и текстом
    html_content = """
    {0}
    <div class="telegram-post">
        {1}
        <br><br>
        <a href="https://t.me/{2}/{3}" class="telegram-link">Перейти в Telegram</a>
    </div>
    """.format(gallery_html, media_group['text'].replace('\n', '<br>'), 
               media_group['channel_username'], media_group['message_id'])
    
    # Публикация в WordPress с первым изображением как миниатюрой (если есть)
    featured_media_id = media_ids[0] if media_ids else None
    success, post_url = await post_to_wordpress(media_group['title'], html_content, featured_media_id)
    
    # Отправка сообщения администратору о результате
    if success:
        await send_admin_message(
            context.bot,
            f"✅ Новый пост с медиа-группой успешно опубликован на сайте!\nЗаголовок: {media_group['title']}\nСсылка: {post_url}"
        )
    else:
        await send_admin_message(
            context.bot,
            f"❌ Не удалось опубликовать пост с медиа-группой на сайт.\nЗаголовок: {media_group['title']}"
        )
    
    # Удаляем обработанную медиа-группу
    del context.bot_data['media_groups'][media_group_id]

# Обработчик новых сообщений в канале
async def channel_post(update: Update, context: CallbackContext):
    message = update.channel_post
    
    # Проверяем, что сообщение пришло из нужного канала
    if str(message.chat.id) != CHANNEL_ID:
        return
    
    logger.info(f"Получено новое сообщение из канала: {message.chat.title}")
    
    # Проверка наличия текста
    if not message.text and not message.caption:
        logger.info("Сообщение без текста, пропускаем.")
        return
    
    # Получение текста сообщения (либо из text, либо из caption)
    text = message.text if message.text else message.caption
    
    # Создание заголовка (первая строка или первые 100 символов)
    title = text.split('\n')[0][:100]
    
    # Получение канала для ссылки
    channel_username = message.chat.username or "channel"  # Если канал без username
    
    # Проверка на наличие медиа-группы
    if hasattr(message, 'media_group_id') and message.media_group_id:
        # Для медиа-группы нужно сохранить ID группы и обработать все сообщения группы
        if not hasattr(context.bot_data, 'media_groups'):
            context.bot_data['media_groups'] = {}
        
        if message.media_group_id not in context.bot_data['media_groups']:
            context.bot_data['media_groups'][message.media_group_id] = {
                'media': [],
                'text': text,
                'title': title,
                'message_id': message.message_id,
                'channel_username': channel_username,
                'timestamp': time.time()
            }
        
        # Добавляем медиа в группу
        if message.photo:
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            context.bot_data['media_groups'][message.media_group_id]['media'].append({
                'type': 'photo',
                'url': file.file_path
            })
        elif message.video:
            file = await context.bot.get_file(message.video.file_id)
            context.bot_data['media_groups'][message.media_group_id]['media'].append({
                'type': 'video',
                'url': file.file_path
            })
        
        # Установим таймер на обработку группы через 2 секунды
        # (чтобы дождаться всех сообщений группы)
        context.job_queue.run_once(
            process_media_group, 
            2, 
            data=message.media_group_id
        )
        return
    
    # Создание контента с текстом и ссылкой на Telegram
    html_content = """
    <div class="telegram-post">
        {0}
        <br><br>
        <a href="https://t.me/{1}/{2}" class="telegram-link">Перейти в Telegram</a>
    </div>
    """.format(text.replace('\n', '<br>'), channel_username, message.message_id)
    
    # Обработка различных типов медиа
    featured_media_id = None
    
    try:
        # Обработка фотографий
        if message.photo:
            # Берем фото максимального размера
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            image_url = file.file_path
            
            # Загрузка изображения в WordPress
            featured_media_id = await upload_media_to_wordpress(image_url, "image/jpeg")
            
            # Если изображение успешно загружено, добавляем его в контент
            if featured_media_id:
                media_info = get_media_info(featured_media_id)
                if media_info and 'source_url' in media_info:
                    img_html = f'<div class="wp-block-image"><figure><img src="{media_info["source_url"]}" alt=""/></figure></div>'
                    html_content = img_html + html_content
        
        # Обработка видео
        elif message.video:
            file = await context.bot.get_file(message.video.file_id)
            video_url = file.file_path
            
            # Загрузка видео в WordPress
            media_id = await upload_media_to_wordpress(video_url, "video/mp4")
            if media_id:
                featured_media_id = media_id
                
                # Получаем информацию о загруженном видео
                media_info = get_media_info(media_id)
                if media_info and 'source_url' in media_info:
                    # Добавляем видео в контент
                    video_html = f'<div class="wp-block-video"><video controls src="{media_info["source_url"]}"></video></div>'
                    html_content = video_html + html_content
    
        # Публикация в WordPress
        success, post_url = await post_to_wordpress(title, html_content, featured_media_id)
        
        # Отправка сообщения администратору о результате
        if success:
            await send_admin_message(
                context.bot,
                f"✅ Новый пост с канала успешно опубликован на сайте!\nЗаголовок: {title}\nСсылка: {post_url}"
            )
        else:
            await send_admin_message(
                context.bot,
                f"❌ Не удалось опубликовать пост с канала на сайт.\nЗаголовок: {title}"
            )
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения канала: {e}")
        await send_admin_message(
            context.bot,
            f"❌ Ошибка при обработке сообщения канала: {e}"
        )

# Обработчик ошибок
async def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Произошла ошибка: {context.error}")
    
    # Отправка сообщения администратору об ошибке
    try:
        await send_admin_message(
            context.bot, 
            f"❌ Произошла ошибка в работе бота:\n{str(context.error)}"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

def main():
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    
    # Обработчик для новых сообщений в канале
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_post))
    
    # Регистрация обработчика ошибок
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logger.info("Бот запускается...")
    
    # Проверка переменных окружения
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не найден в .env файле")
    if not CHANNEL_ID:
        logger.error("TELEGRAM_CHANNEL_ID не найден в .env файле")
    if not ADMIN_USER_ID:
        logger.error("ADMIN_USER_ID не найден в .env файле")
    if not WP_URL:
        logger.error("WP_URL не найден в .env файле")
    if not WP_USERNAME:
        logger.error("WP_USERNAME не найден в .env файле")
    if not WP_PASSWORD:
        logger.error("WP_PASSWORD не найден в .env файле")
    
    application.run_polling()

if __name__ == "__main__":
    main()