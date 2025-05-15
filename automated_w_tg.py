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

# Функция для публикации поста в WordPress
async def post_to_wordpress(title, content, image_url=None):
    # Подготовка данных поста
    post_data = {
        "title": title,
        "content": content,
        "status": "publish"
    }
    
    # Если есть изображение, добавим его
    if image_url:
        # Загрузка изображения в медиатеку WordPress
        media_url = f"{WP_URL}/wp-json/wp/v2/media"
        
        # Скачивание изображения
        img_response = requests.get(image_url)
        if img_response.status_code == 200:
            file_name = f"telegram_image_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
            
            # Загрузка изображения в WordPress
            media_headers = {
                "Content-Disposition": f'attachment; filename="{file_name}"',
                "Content-Type": "image/jpeg"
            }
            
            media_response = requests.post(
                media_url,
                headers=media_headers,
                auth=auth,
                data=img_response.content
            )
            
            if media_response.status_code in [200, 201]:
                # Получение ID загруженного изображения
                image_id = media_response.json().get('id')
                # Установка изображения как миниатюры поста
                post_data["featured_media"] = image_id
    
    # Отправка запроса на создание поста
    response = requests.post(WP_API_URL, headers=headers, auth=auth, json=post_data)
    
    if response.status_code == 201:
        logger.info(f"Пост успешно создан: {response.json().get('link')}")
        return True, response.json().get('link')
    else:
        logger.error(f"Ошибка создания поста: {response.status_code}, {response.text}")
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
    

    await update.message.reply_text("✅ WordPress подключен успешно.")


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
    channel_username = message.chat.username
    
    # Создание контента с текстом и ссылкой на Telegram
    # Избегаем f-строки с обратным слешем, используем обычный формат
    html_content = """
    <div class="telegram-post">
        {0}
        <br><br>
        <a href="https://t.me/{1}/{2}" class="telegram-link">Перейти в Telegram</a>
    </div>
    """.format(text.replace('\n', '<br>'), channel_username, message.message_id)
    
    # Проверка наличия медиа (фото) в сообщении
    image_url = None
    if message.photo:
        # Берем фото максимального размера
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_url = file.file_path  # URL фото на серверах Telegram
    
    # Публикация в WordPress
    success, post_url = await post_to_wordpress(title, html_content, image_url)
    
    # Отправка сообщения администратору о результате
    if success:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"✅ Новый пост с канала успешно опубликован на сайте!\nЗаголовок: {title}\nСсылка: {post_url}"
        )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"❌ Не удалось опубликовать пост с канала на сайт.\nЗаголовок: {title}"
        )

# Обработчик ошибок
async def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Произошла ошибка: {context.error}")
    
    # Отправка сообщения администратору об ошибке
    try:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"❌ Произошла ошибка в работе бота:\n{str(context.error)}"
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
    application.run_polling()

if __name__ == "__main__":
    main()