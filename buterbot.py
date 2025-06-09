import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from g4f.Provider import RetryProvider
from g4f import Provider, models
import g4f
import warnings
import os
import logging
from flask import Flask
from threading import Thread
from datetime import datetime, timezone
import pytz
import signal

# Настройка Flask для пингов
app = Flask(__name__)

@app.route('/')
def home(): return "Bot is alive!"
def run_flask(): app.run(host='0.0.0.0', port=5000)
def keep_alive(): Thread(target=run_flask).start()
keep_alive()

# Настройка логгирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.environ['DISABLE_OPENAI_BROWSER'] = 'true'
os.environ['G4F_NO_BROWSER'] = 'true'
warnings.filterwarnings("ignore")
g4f.debug.logging = False
g4f.check_version = False
g4f.debug.version_check = False

API_TOKEN = os.getenv('TELEGRAM_TOKEN')  # Используем переменные окружения
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Храним данные пользователей
user_data = {}
previous_breakfasts = {}
MODELS = [g4f.models.gpt_4o_mini, g4f.models.deepseek_r1, g4f.models.o3_mini, g4f.models.gpt_4, g4f.models.gpt_4_1_mini]
current_model_index = 0
feedback_data = {}  # Для хранения отзывов
'''
def is_working_time() -> bool:
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    return (2 <= current_hour < 11) or (18 <= current_hour <= 23)

def check_working_hours():
    if not is_working_time():
        msk_time = datetime.now(timezone.utc).astimezone(
            pytz.timezone('Europe/Moscow')
        ).strftime('%H:%M')
        logger.info(f"🛑 Завершаю работу (МСК: {msk_time})")
        os._exit(0)
'''
async def generate_with_timeout(prompt, timeout=20):
    global current_model_index
    model = MODELS[current_model_index]
    
    for attempt in range(5):
        try:
            response = await asyncio.wait_for(
                g4f.ChatCompletion.create_async(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=timeout
                ),
                timeout=timeout
            )
            logger.info(f"Использована модель: {model}")
            return response
        except Exception as e:
            logger.error(f"Ошибка (попытка {attempt+1}): {str(e)}")
            current_model_index = (current_model_index + 1) % len(MODELS)
            model = MODELS[current_model_index]
            await asyncio.sleep(1 if attempt == 0 else 0)

async def generate_breakfasts(user_id):
   # check_working_hours()  # Проверяем время
   # if not is_working_time():
   #     return ["Бот спит (8:00-22:00 МСК)"] * 6

    try:
        last_breakfasts = previous_breakfasts.get(user_id, [])
        prompt = "Напиши 6 вариантов завтраков, только названия через запятую и название каждого завтрака с большой буквы"
        if last_breakfasts:
            prompt += f", исключая: {', '.join(last_breakfasts)}"
        
        response = await generate_with_timeout(prompt)
        new_breakfasts = [b.strip() for b in response.split(',')[:6]]
        previous_breakfasts[user_id] = new_breakfasts
        return new_breakfasts
    except Exception as e:
        logger.error(f"Ошибка при генерации завтраков: {str(e)}")
        defaults = ["Омлет", "Гранола", "Тосты с авокадо", "Смузи-боул", "Овсянка", "Сырники"]
        return [b for b in defaults if b not in last_breakfasts][:6] or defaults[:6]

async def generate_recipe(breakfast_name):
  #  check_working_hours()  # Проверяем время
   # if not is_working_time():
  #      return "Бот отдыхает с 22:00 до 8:00 МСК 😴"

    try:
        return await generate_with_timeout(f"Напиши рецепт для {breakfast_name} пункты коротко и ясно, пиши текст без курсива и \"###\" и жирного \"**\" (без markdown или html оформления) ")
    except Exception as e:
        logger.error(f"Ошибка при генерации рецепта: {str(e)}")
        return "Не удалось получить рецепт. Попробуйте снова."

# Главное меню
async def show_main_menu(chat_id):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="🍳 Выбрать завтрак",
        callback_data="generate"
    ))
    builder.add(types.InlineKeyboardButton(
        text="📝 Оставить отзыв",
        callback_data="feedback"
    ))
    await bot.send_message(
        chat_id,
        "Доброе утро! Нажми кнопку для выбора завтрака:",
        reply_markup=builder.as_markup()
    )

@dp.message(Command('start'))
async def send_welcome(message: types.Message):
   # check_working_hours()  # Проверяем время и завершаемся если нужно
   # if not is_working_time():
   #     await message.answer("⏳ Бот работает с 5:00-14:00 и 21:00-2:00 по МСК!")
   #     return
    await show_main_menu(message.chat.id)
    
@dp.callback_query(lambda c: c.data == "main_menu")
async def back_to_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await show_main_menu(callback_query.message.chat.id)
    
@dp.callback_query(lambda c: c.data == "feedback")
async def ask_feedback(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer(
        "Напишите ваш отзыв о работе бота:",
        reply_markup=InlineKeyboardBuilder()
            .add(types.InlineKeyboardButton(text="↩️ Назад", callback_data="main_menu"))
            .as_markup()
    )
    feedback_data[callback_query.from_user.id] = True

@dp.message(lambda message: message.from_user.id in feedback_data)
async def save_feedback(message: types.Message):
    user_id = message.from_user.id
    feedback = message.text
    logger.info(f"Отзыв от {user_id}: {feedback}")
    del feedback_data[user_id]
    await message.answer("Спасибо за отзыв!", reply_markup=InlineKeyboardBuilder()
        .add(types.InlineKeyboardButton(text="В меню", callback_data="main_menu"))
        .as_markup())

@dp.callback_query(lambda c: c.data == "generate")
async def process_callback(callback_query: types.CallbackQuery):
  #  check_working_hours()  # Проверяем время
  #  if not is_working_time():
  #      await callback_query.answer("Бот спит 😴", show_alert=True)
  #      return

    await callback_query.answer("⏳ Генерируем новые варианты завтраков...")
    loading_msg = await callback_query.message.answer("🔄 Идет генерация новых вариантов...")
    breakfasts = await generate_breakfasts(callback_query.from_user.id)
    user_data[callback_query.from_user.id] = breakfasts
    
    await bot.delete_message(
        chat_id=callback_query.message.chat.id,
        message_id=loading_msg.message_id
    )
    
    builder = InlineKeyboardBuilder()
    for i, breakfast in enumerate(breakfasts, 1):
        builder.add(types.InlineKeyboardButton(
            text=f"{i}. {breakfast}",
            callback_data=f"recipe_{i}"
        ))
    builder.add(types.InlineKeyboardButton(
        text="🔄 Сгенерировать новые варианты",
        callback_data="generate"
    ))
    builder.adjust(2, 2, 2, 1)
    
    await callback_query.message.answer(
        "Выбери завтрак:\n" + "\n".join(f"{i}. {b}" for i, b in enumerate(breakfasts, 1)),
        reply_markup=builder.as_markup()
    )

@dp.callback_query(lambda c: c.data.startswith("recipe_"))
async def show_recipe(callback_query: types.CallbackQuery):
  #  check_working_hours()  # Проверяем время
  #  if not is_working_time():
  #      await callback_query.answer("Бот спит 😴", show_alert=True)
  #      return

    await callback_query.answer("⏳ Готовим рецепт...")
    loading_msg = await callback_query.message.answer("🍳 Готовим рецепт, подождите...")
    
    breakfast_num = int(callback_query.data.split("_")[1]) - 1
    user_id = callback_query.from_user.id
    
    if user_id in user_data and 0 <= breakfast_num < len(user_data[user_id]):
        breakfast_name = user_data[user_id][breakfast_num]
        recipe = await generate_recipe(breakfast_name)
        
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=loading_msg.message_id
        )
        
        builder = InlineKeyboardBuilder()
        for i, breakfast in enumerate(user_data[user_id], 1):
            builder.add(types.InlineKeyboardButton(
                text=f"{i}. {breakfast[:15] + '...' if len(breakfast) > 15 else breakfast}",
                callback_data=f"recipe_{i}"
            ))
        builder.adjust(2, 2, 2)
        
    # Новые кнопки управления
    builder.row(types.InlineKeyboardButton(
        text="🔄 Новые варианты",
        callback_data="generate"
    ))
    builder.row(types.InlineKeyboardButton(
        text="📝 Отзыв",
        callback_data="feedback"
    ))
    builder.row(types.InlineKeyboardButton(
        text="🏠 В меню",
        callback_data="main_menu"
    ))
    
    await callback_query.message.answer(
        f"🍳 {breakfast_name}\n\n{recipe}",
        reply_markup=builder.as_markup()
    )
        
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())