import asyncio
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright

BOT_TOKEN = "8826164504:AAFcEDBcLxWn5P0BgclTC76hPCr6TzeTZ0o"
COOKIES_PATH = "cookies.json"
DOWNLOADS_DIR = "downloads"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

os.makedirs(DOWNLOADS_DIR, exist_ok=True)


# --- FSM (Машина состояний диалога) ---
class PostForm(StatesGroup):
    waiting_for_video = State()
    waiting_for_caption = State()
    waiting_for_time = State()


# --- БЛОК TIKTOK: Авторизация и Загрузка ---
async def save_tiktok_cookies():
    """Запускает видимый браузер для ручного входа и сохранения кук."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("\n[AUTH] Открываем страницу входа TikTok...")
        await page.goto("https://www.tiktok.com/login")
        print("[AUTH] Авторизуйтесь руками в окне браузера.")
        print("[AUTH] Когда страница загрузится после входа — вернитесь в терминал и нажмите Enter...")
        
        # Ожидание нажатия Enter в терминале
        await asyncio.to_thread(input)

        await context.storage_state(path=COOKIES_PATH)
        print(f"[AUTH] Успешно! Файл {COOKIES_PATH} сохранен.\n")
        await browser.close()


async def upload_video_to_tiktok(video_path: str, caption: str) -> bool:
    """Загружает видео в фоновом режиме через сохраненную сессию."""
    if not os.path.exists(COOKIES_PATH):
        print("[UPLOAD ERROR] Файл cookies.json не найден. Сначала выполните вход!")
        return False

    abs_video_path = os.path.abspath(video_path)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            storage_state=COOKIES_PATH,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            print("[UPLOAD] Открываем TikTok Studio...")
            await page.goto("https://www.tiktok.com/creator-center/upload", timeout=60000)

            # Выбираем видеофайл
            upload_input = page.locator('input[type="file"]')
            await upload_input.wait_for(timeout=30000)
            await upload_input.set_input_files(abs_video_path)
            print("[UPLOAD] Файл передан, ждем форму описания...")

            # Очищаем поле описания и вводим текст с хэштегами
            caption_box = page.locator('.public-DraftEditor-content, div[contenteditable="true"]')
            await caption_box.wait_for(timeout=45000)
            await asyncio.sleep(2)
            await caption_box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await caption_box.type(caption, delay=40)

            # Ждем кнопку отправки и нажимаем
            post_button = page.locator('button:has-text("Post"), button:has-text("Опубликовать")')
            await post_button.wait_for(state="visible", timeout=60000)
            await asyncio.sleep(5)
            await post_button.click()

            # Ждем подтверждения публикации
            await page.wait_for_selector('text="Manage your posts", text="Управлять публикациями"', timeout=60000)
            print("[UPLOAD] Публикация завершена успешно!")
            await browser.close()
            return True

        except Exception as e:
            print(f"[UPLOAD ERROR] Ошибка: {e}")
            await page.screenshot(path="error_screenshot.png")
            await browser.close()
            return False


# --- БЛОК TELEGRAM: Обработчики команд и сообщений ---
async def scheduled_task(video_path: str, caption: str, user_id: int):
    """Функция, вызываемая планировщиком по таймеру."""
    await bot.send_message(user_id, "⏳ Начинаю выгрузку запланированного видео в TikTok...")
    success = await upload_video_to_tiktok(video_path, caption)

    if success:
        await bot.send_message(user_id, "✅ Видео успешно опубликовано в TikTok!")
    else:
        await bot.send_message(user_id, "❌ Ошибка при публикации. Посмотрите скриншот error_screenshot.png на сервере.")

    # Удаляем временный файл
    if os.path.exists(video_path):
        os.remove(video_path)


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Пришли мне видеоролик, который нужно запланировать для TikTok.\n\n"
        "💡 Если вы еще не вошли в аккаунт на сервере, выполните в чате команду /auth."
    )
    await state.set_state(PostForm.waiting_for_video)


@dp.message(Command("auth"))
async def cmd_auth(message: types.Message):
    await message.answer("⚠️ Внимание! На сервере откроется браузер. Перейдите в терминал консоли, выполните вход в TikTok и нажмите Enter.")
    try:
        await save_tiktok_cookies()
        await message.answer("✅ Сессия сохранена в cookies.json! Теперь можно загружать видео.")
    except Exception as e:
        await message.answer(f"❌ Ошибка авторизации: {e}")


@dp.message(PostForm.waiting_for_video, F.video)
async def process_video(message: types.Message, state: FSMContext):
    file = await bot.get_file(message.video.file_id)
    save_path = os.path.join(DOWNLOADS_DIR, f"{message.video.file_id}.mp4")
    await bot.download_file(file.file_path, save_path)

    await state.update_data(video_path=save_path)
    await message.answer("Видео принято. Теперь напишите описание и хэштеги:")
    await state.set_state(PostForm.waiting_for_caption)


@dp.message(PostForm.waiting_for_caption)
async def process_caption(message: types.Message, state: FSMContext):
    await state.update_data(caption=message.text)
    await message.answer(
        "Укажите дату и время публикации в формате:\n`ГГГГ-ММ-ДД ЧЧ:ММ` (например: `2026-11-01 18:30`)",
        parse_mode="Markdown"
    )
    await state.set_state(PostForm.waiting_for_time)


@dp.message(PostForm.waiting_for_time)
async def process_time(message: types.Message, state: FSMContext):
    time_str = message.text.strip()
    try:
        run_date = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        if run_date <= datetime.now():
            await message.answer("Время должно быть в будущем. Попробуйте еще раз:")
            return

        data = await state.get_data()

        scheduler.add_job(
            scheduled_task,
            trigger="date",
            run_date=run_date,
            args=[data["video_path"], data["caption"], message.from_user.id]
        )

        await message.answer(f"✅ Пост успешно запланирован на: {run_date}")
        await state.clear()

    except ValueError:
        await message.answer("Неверный формат времени. Повторите в виде: `ГГГГ-ММ-ДД ЧЧ:ММ`", parse_mode="Markdown")


# --- Запуск приложения ---
async def main():
    scheduler.start()
    print("Бот запущен. Ожидание сообщений...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
