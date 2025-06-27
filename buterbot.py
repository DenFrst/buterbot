import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
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
import asyncpg

#region Настройки
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

# Подключение к DB
async def get_db():
    return await asyncpg.connect(os.getenv("DATABASE_URL_FNL"))

# Храним данные пользователей
user_data = {}
previous_breakfasts = {}
MODELS = [g4f.models.gpt_4o_mini, g4f.models.deepseek_r1, g4f.models.o3_mini, g4f.models.gpt_4, g4f.models.gpt_4_1_mini]
current_model_index = 0
feedback_data = {}  # Для хранения отзывов
#endregion Настройки

# Кнопки - Главное меню
async def show_main_menu(chat_id):
    # Основные кнопки внизу
    reply_markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🍳 Выбрать завтраки"), KeyboardButton(text="⚙️ Настройки аллергенов")],
            [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="📝 Отзыв")]
        ],
        resize_keyboard=True
    )
    # Inline-кнопки для дополнительных действий
    inline_kb = InlineKeyboardBuilder()
    inline_kb.add(types.InlineKeyboardButton(
        text="Популярные завтраки",
        callback_data="allergy_settings"
    ))
    
    await bot.send_message(
        chat_id,
        "Доброе утро! Выберите действие:",
        reply_markup=reply_markup
    )
    #await bot.send_message(
    #    chat_id,
   #     "Дополнительные опции:",
   #     reply_markup=inline_kb.as_markup()
   # )

# /Start
@dp.message(Command('start'))
async def send_welcome(message: types.Message):
    await show_main_menu(message.chat.id)


#region Аллегены
# Обновленный обработчик команды /allergy
@dp.message(Command('allergy'))
async def set_allergies(message: types.Message):
    user_id = message.from_user.id
    allergies = message.text.replace('/allergy', '').strip()
    
    if not allergies:
        # Показываем текущие настройки без зацикливания
        await show_allergy_settings(user_id, message)
        return
    
    try:
        conn = await get_db()
        await conn.execute("""
            INSERT INTO user_preferences (user_id, allergies)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET allergies = EXCLUDED.allergies
        """, user_id, allergies)
        
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(
            text="⚙️ Показать настройки",
            callback_data="show_allergy_settings"
        ))
        
        await message.answer(
            f"✅ <b>Список аллергенов обновлён</b>\n\n"
            f"<code>{allergies}</code>\n\n"
            f"Теперь бот будет учитывать их при генерации рецептов",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения аллергенов: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")
    finally:
        if conn: await conn.close()

# Обработчик кнопки настроек аллергенов
@dp.message(lambda msg: msg.text == "⚙️ Настройки аллергенов")
async def show_allergies_button(message: types.Message):
    await show_allergy_settings(message.from_user.id, message)

# Общая функция для отображения настроек
async def show_allergy_settings(user_id, message_or_callback):
    conn = await get_db()
    current_allergies = await conn.fetchval(
        "SELECT allergies FROM user_preferences WHERE user_id = $1", 
        user_id
    )
    await conn.close()
    
    builder = InlineKeyboardBuilder()
    if current_allergies:
        builder.row(types.InlineKeyboardButton(
            text="❌ Очистить аллергены",
            callback_data="clear_allergies"
        ))
    builder.row(types.InlineKeyboardButton(
        text="✏️ Изменить список",
        callback_data="edit_allergies"
    ))
    
    text = "⚙️ <b>Управление аллергенами</b>\n\n"
    if current_allergies:
        text += f"Текущий список:\n<code>{current_allergies}</code>\n\n"
        text += "Чтобы изменить, отправьте:\n<code>/allergy новый список</code>"
    else:
        text += "Аллергены не указаны\n\n"
        text += "Добавьте через запятую:\n<code>/allergy молоко, глютен</code>"
    
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    else:
        await message_or_callback.answer(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

# Обработчики callback
@dp.callback_query(lambda c: c.data == "show_allergy_settings")
async def show_settings_callback(callback_query: types.CallbackQuery):
    await show_allergy_settings(callback_query.from_user.id, callback_query)

@dp.callback_query(lambda c: c.data == "clear_allergies")
async def clear_allergies(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        conn = await get_db()
        await conn.execute(
            "UPDATE user_preferences SET allergies = NULL WHERE user_id = $1",
            user_id
        )
        await callback_query.answer("✅ Список аллергенов очищен")
        await show_allergy_settings(user_id, callback_query)
    except Exception as e:
        logger.error(f"Ошибка очистки аллергенов: {e}")
        await callback_query.answer("❌ Ошибка при очистке")
    finally:
        if conn: await conn.close()

@dp.callback_query(lambda c: c.data == "edit_allergies")
async def edit_allergies(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer(
        "✏️ <b>Редактирование аллергенов</b>\n\n"
        "Пришлите новый список через запятую:\n"
        "<code>/allergy молоко, глютен, мёд</code>\n\n"
        "Или нажмите /cancel чтобы отменить",
        parse_mode="HTML"
    )
#endregion Allergy

#region Избранное

# Обработчик кнопки "⭐ Избранное"
@dp.message(lambda msg: msg.text == "⭐ Избранное")
async def show_favorites_button(message: types.Message):
    await handle_show_favorites(message.from_user.id, message)

# Обработчик callback для избранных рецептов
@dp.callback_query(lambda c: c.data.startswith("show_fav_"))
async def show_favorite_recipe(callback_query: types.CallbackQuery):
    try:
        recipe_name = callback_query.data.split("_", 2)[2]
        user_id = callback_query.from_user.id
        
        # Получаем полный рецепт из базы
        conn = await get_db()
        recipe = await conn.fetchrow(
            "SELECT recipe_text FROM favorites WHERE user_id = $1 AND recipe_name = $2",
            user_id, recipe_name
        )
        
        if recipe:
            # Кнопки для управления
            builder = InlineKeyboardBuilder()
            builder.add(types.InlineKeyboardButton(
                text="🗑 Удалить из избранного",
                callback_data=f"del_fav_{recipe_name}"
            ))
            builder.add(types.InlineKeyboardButton(
                text="⬅️ Назад к списку",
                callback_data="back_to_favorites"
            ))
            
            await callback_query.message.edit_text(
                f"{recipe['recipe_text']}",
                reply_markup=builder.as_markup()
            )
        else:
            await callback_query.answer("Рецепт не найден")
            
    except Exception as e:
        logger.error(f"Ошибка показа рецепта: {e}")
        await callback_query.answer("❌ Ошибка загрузки")
    finally:
        if conn: await conn.close()

# Обработчик возврата к списку избранного
@dp.callback_query(lambda c: c.data == "back_to_favorites")
async def back_to_favorites(callback_query: types.CallbackQuery):
    await handle_show_favorites(callback_query.from_user.id, callback_query)

# Общая функция для отображения избранного
async def handle_show_favorites(user_id, message_or_callback):
    try:
        conn = await get_db()
        favorites = await conn.fetch(
            "SELECT recipe_name FROM favorites WHERE user_id = $1", 
            user_id
        )
        
        if not favorites:
            text = "Ваше избранное пусто."
            if isinstance(message_or_callback, types.CallbackQuery):
                await message_or_callback.message.edit_text(text)
            else:
                await message_or_callback.answer(text)
            return
            
        builder = InlineKeyboardBuilder()
        for idx, fav in enumerate(favorites, 1):
            builder.add(types.InlineKeyboardButton(
                text=f"{idx}. {fav['recipe_name']}",
                callback_data=f"show_fav_{fav['recipe_name']}"
            ))
        builder.adjust(2)
        
        text = "⭐ Ваше избранное:"
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.message.edit_text(
                text,
                reply_markup=builder.as_markup()
            )
        else:
            await message_or_callback.answer(
                text,
                reply_markup=builder.as_markup()
            )
            
    except Exception as e:
        logger.error(f"Ошибка загрузки избранного: {e}")
        error_text = "❌ Ошибка загрузки избранного"
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.message.answer(error_text)
        else:
            await message_or_callback.answer(error_text)
    finally:
        if conn: await conn.close()
        
@dp.callback_query(lambda c: c.data.startswith("add_fav_"))
async def add_to_favorites(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        recipe_name = callback_query.data.split("_", 2)[2]
        
        recipe_text = callback_query.message.text.split("\n\n")[-1]
        
        conn = await get_db()
        # Проверяем, нет ли уже такого рецепта в избранном
        exists = await conn.fetchval(
            "SELECT 1 FROM favorites WHERE user_id = $1 AND recipe_name = $2",
            user_id, recipe_name
        )
        
        if exists:
            await callback_query.answer("⚠️ Этот рецепт уже в избранном")
            return
            
        await conn.execute(
            "INSERT INTO favorites (user_id, recipe_name, recipe_text) VALUES ($1, $2, $3)",
            user_id, recipe_name, recipe_text
        )
        
        # Обновляем кнопку
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(
            text="⭐ Уже в избранном",
            callback_data="already_fav"
        ))
        builder.row(types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="back_to_list"
        ))
        
        await callback_query.message.edit_reply_markup(
            reply_markup=builder.as_markup()
        )
        await callback_query.answer(f"✅ {recipe_name} добавлен в избранное")
        
    except Exception as e:
        logger.error(f"Ошибка добавления в избранное: {e}")
        await callback_query.answer("❌ Не удалось добавить в избранное")
    finally:
        if conn: await conn.close()

@dp.callback_query(lambda c: c.data == "already_fav")
async def already_favorite(callback_query: types.CallbackQuery):
    await callback_query.answer("Этот рецепт уже в вашем избранном")

@dp.callback_query(lambda c: c.data.startswith("del_fav_"))
async def delete_favorite(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        recipe_name = callback_query.data.split("_", 2)[2]
        conn = await get_db()
        
        # Удаляем рецепт из избранного
        await conn.execute(
            "DELETE FROM favorites WHERE user_id = $1 AND recipe_name = $2",
            user_id, recipe_name
        )
        
        # Показываем обновленный список избранного
        await callback_query.answer(f"❌ {recipe_name} удален из избранного")
        await handle_show_favorites(user_id, callback_query)
        
    except Exception as e:
        logger.error(f"Ошибка удаления из избранного: {e}")
        await callback_query.answer("❌ Не удалось удалить рецепт")
    finally:
        if conn: await conn.close()

@dp.callback_query(lambda c: c.data == "back_to_list")
async def back_to_list_handler(callback_query: types.CallbackQuery):
    try:
        await callback_query.message.delete()
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")
        await callback_query.answer("❌ Не удалось вернуться")
        '''
        user_id = callback_query.from_user.id

        # Получаем список всех завтраков пользователя
        breakfasts = user_data[user_id]['breakfasts']  # Из ранее сгенерированных вариантов
        
        builder = InlineKeyboardBuilder()
        for i, breakfast in enumerate(breakfasts, 1):
            builder.add(types.InlineKeyboardButton(
                text=f"{i}. {breakfast[:15] + '...' if len(breakfast) > 15 else breakfast}",
                callback_data=f"recipe_{i}"
            ))
        builder.adjust(2, 2, 2)
        
        builder.row(types.InlineKeyboardButton(
            text="🔄 Новые варианты",
            callback_data="generate"
        ))
        
        await callback_query.message.edit_text(
            "Выбери завтрак:\n" + "\n".join(f"{i}. {b}" for i, b in enumerate(breakfasts, 1)),
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        logger.error(f"Ошибка возврата к списку: {e}")
        await callback_query.answer("❌ Ошибка загрузки списка")
        '''
#endregion Избранное

#region Отзывы
@dp.message(lambda msg: msg.text == "📝 Отзыв")
async def ask_feedback(message: types.Message):
    markup = InlineKeyboardBuilder()
    markup.add(types.InlineKeyboardButton(
        text="❌ Отменить",
        callback_data="cancel_feedback"
    ))
    await message.answer(
        "Напишите ваш отзыв:",
        reply_markup=markup.as_markup()
    )
    feedback_data[message.from_user.id] = True

@dp.callback_query(lambda c: c.data == "cancel_feedback")
async def cancel_feedback(callback: types.CallbackQuery):
    if callback.from_user.id in feedback_data:
        del feedback_data[callback.from_user.id]
    await callback.message.edit_text("Отправка отзыва отменена.")

@dp.message(lambda message: message.from_user.id in feedback_data)
async def save_feedback(message: types.Message):
    user_id = message.from_user.id
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO feedbacks (user_id, username, text) VALUES ($1, $2, $3)",
            user_id, message.from_user.username, message.text
        )
        await message.answer("✅ Спасибо за отзыв!")
    except Exception as e:
        logger.error(f"Ошибка сохранения отзыва: {e}")
        await message.answer("❌ Ошибка. Попробуйте позже.")
    finally:
        if conn: await conn.close()
        if user_id in feedback_data: 
            del feedback_data[user_id]  # Всегда очищаем флаг
#endregion Отзывы

#region Завтраки

# Обработчик для сообщений
@dp.message(lambda msg: msg.text == "🍳 Выбрать завтраки")
async def process_breakfast_message(message: types.Message):
    await handle_generate_breakfasts(message.from_user.id, message)

# Обработчик для callback-запросов
@dp.callback_query(lambda c: c.data == "generate")
async def process_callback(callback_query: types.CallbackQuery):
    await handle_generate_breakfasts(callback_query.from_user.id, callback_query)

async def handle_generate_breakfasts(user_id, message_or_callback):
    try:
        # Отправка начального сообщения
        if isinstance(message_or_callback, types.CallbackQuery):
            message = message_or_callback.message
            await message_or_callback.answer("⏳ Генерируем новые варианты завтраков...")
        else:
            message = message_or_callback
            await message.answer("⏳ Генерируем новые варианты завтраков...")

        loading_msg = await message.answer("🔄 Идет генерация новых вариантов...")
        
        breakfasts = await generate_breakfasts(user_id)
        
        # Инициализация user_data если нужно
        if user_id not in user_data:
            user_data[user_id] = {}
        user_data[user_id]['breakfasts'] = breakfasts
        
        # Удаление сообщения о загрузке с обработкой ошибок
        try:
            await bot.delete_message(
                chat_id=message.chat.id,
                message_id=loading_msg.message_id
            )
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")
        
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
        
        await message.answer(
            "Выбери завтрак:\n" + "\n".join(f"{i}. {b}" for i, b in enumerate(breakfasts, 1)),
            reply_markup=builder.as_markup()
        )
    
    except Exception as e:
        print(f"Ошибка в handle_generate_breakfasts: {e}")
        # Можно добавить обработку ошибки для пользователя
        await message.answer("⚠️ Произошла ошибка при генерации завтраков. Попробуйте еще раз.")

#endregion Завтраки

#region Рецепты
@dp.callback_query(lambda c: c.data.startswith("recipe_"))
async def show_recipe(callback_query: types.CallbackQuery):
    await callback_query.answer("⏳ Готовим рецепт...")
    
    try:
        breakfast_num = int(callback_query.data.split("_")[1]) - 1  # "recipe_1" → 0
    except (IndexError, ValueError):
        await callback_query.message.answer("❌ Ошибка: неверный номер завтрака.")
        return

    user_id = callback_query.from_user.id

    # Проверяем структуру user_data
    if user_id not in user_data:
        await callback_query.message.answer("❌ Данные устарели. Сгенерируйте завтраки заново.")
        return

    # Убедимся, что 'breakfasts' существует и это СПИСОК
    if 'breakfasts' not in user_data[user_id] or not isinstance(user_data[user_id]['breakfasts'], list):
        await callback_query.message.answer("❌ Ошибка данных. Попробуйте сгенерировать завтраки заново.")
        return

    breakfasts = user_data[user_id]['breakfasts']  # Получаем список завтраков

    # Проверяем, что breakfasts не пустой и номер корректен
    if not breakfasts or not (0 <= breakfast_num < len(breakfasts)):
        await callback_query.message.answer(f"❌ Нет завтрака с таким номером. Доступно: {len(breakfasts)} вариантов.")
        return

    # Основная логика
    breakfast_name = breakfasts[breakfast_num]
    recipe = await generate_recipe(breakfast_name, user_id)
    
    # Создаем клавиатуру
    builder = InlineKeyboardBuilder()
    for i, breakfast in enumerate(breakfasts, 1):
        builder.add(types.InlineKeyboardButton(
            text=f"{i}. {breakfast[:15] + '...' if len(breakfast) > 15 else breakfast}",
            callback_data=f"recipe_{i}"
        ))
    builder.adjust(2, 2, 2)
    
    builder.row(types.InlineKeyboardButton(
        text="🔄 Новые варианты",
        callback_data="generate"
    ))
    builder.row(types.InlineKeyboardButton(
        text="⭐ В избранное",
        callback_data=f"add_fav_{breakfast_name}"
    ))
    
    await callback_query.message.answer(
        f"🍳 {recipe}", 
        reply_markup=builder.as_markup()
    )
#endregion Рецепты


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
    try:
        try:
            conn = await get_db()
            current_allergies = await conn.fetchval(
                "SELECT allergies FROM user_preferences WHERE user_id = $1", 
                user_id
            )
            await conn.close()
        except Exception as e:
            logger.error(f"Ошибка при получении allergy in generate_breakfasts: {str(e)}")
            current_allergies = ""
        last_breakfasts = previous_breakfasts.get(user_id, [])
        prompt = (
            "Сгенерируй 6 вариантов завтраков на русском языке. Требования:\n"
            "1. Только названия через запятую\n"
            "2. Каждое название с большой буквы\n"
            "3. Полностью исключи следующие ингредиенты: " 
            f"{current_allergies if current_allergies else 'отсутствуют'}\n"
            "4. В названиях не должно содержаться этих ингредиентов даже частично\n"
            f"{'5. Исключи эти варианты: ' + ', '.join(last_breakfasts) if last_breakfasts else ''}\n"
            "6. Названия должны быть аппетитными и разнообразными\n"
            "Формат: Название1, Название2, Название3"
        )
        
        response = await generate_with_timeout(prompt)
        new_breakfasts = [b.strip() for b in response.split(',')[:6]]
        previous_breakfasts[user_id] = new_breakfasts
        return new_breakfasts
    except Exception as e:
        logger.error(f"Ошибка при генерации завтраков: {str(e)}")
        defaults = ["Омлет", "Гранола", "Тосты с авокадо", "Смузи-боул", "Овсянка", "Сырники"]
        return [b for b in defaults if b not in last_breakfasts][:6] or defaults[:6]

async def generate_recipe(breakfast_name, user_id):
    try:
        conn = await get_db()
        
        allergies = await conn.fetchval(
            "SELECT allergies FROM user_preferences WHERE user_id = $1",
            user_id
        )
        allergies = allergies if allergies else ""
        
        prompt = (
            f"Напиши рецепт для {breakfast_name}." +
            (f" Полностью исключи следующие продукты (аллергены): {allergies}." if allergies else "") +
            "Пункты коротко и ясно, без оформления (без markdown или html)."
            )
        return await generate_with_timeout(prompt)
    except Exception as e:
        logger.error(f"Ошибка при генерации рецепта: {str(e)}")
        return "Не удалось получить рецепт. Попробуйте снова."
    finally:
        if conn: await conn.close()



async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())