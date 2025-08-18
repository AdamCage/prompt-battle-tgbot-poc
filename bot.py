import logging
import random
from io import BytesIO

import openpyxl
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Настройки ---
TOKEN = ""   # подставь свой токен
ADMIN_ID = 1234           # id админа в телеграме

# --- Глобальное состояние ---
game_state = {
    "active": False,
    "true_prompt": None,
    "image_file_id": None,
    "answers": []  # {"user": str, "prompt": str, "score": int}
}

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Мок-оценка (рандом) ---
def score_prompt(_: str, __: str) -> int:
    return random.randint(0, 100)

# --- Хэндлеры ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Привет, админ! Загрузи картинку и укажи истинный промпт командой /setprompt <текст>.")
    else:
        await update.message.reply_text("Битва промптов скоро начнётся, ждите старта.")

async def setprompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    true_prompt = " ".join(context.args)
    if not true_prompt:
        await update.message.reply_text("Нужно указать промпт: /setprompt <текст>")
        return
    if not game_state["image_file_id"]:
        await update.message.reply_text("Сначала загрузи картинку!")
        return
    game_state["true_prompt"] = true_prompt
    game_state["active"] = True
    game_state["answers"] = []
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Раунд начался!")
    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=game_state["image_file_id"])

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    photo = update.message.photo[-1]
    game_state["image_file_id"] = photo.file_id
    await update.message.reply_text("Картинка сохранена. Теперь задай истинный промпт командой /setprompt.")

async def handle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not game_state["active"]:
        return
    user_prompt = update.message.text.strip()
    user = update.effective_user.username or update.effective_user.full_name
    score = score_prompt(user_prompt, game_state["true_prompt"])
    game_state["answers"].append({"user": user, "prompt": user_prompt, "score": score})
    await update.message.reply_text(f"Твой промпт принят! (оценка: {score})")

async def results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ник", "предложенный промпт", "очки"])
    for ans in game_state["answers"]:
        ws.append([ans["user"], ans["prompt"], ans["score"]])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    await context.bot.send_document(chat_id=update.effective_chat.id, document=InputFile(bio, "results.xlsx"))

async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    true_prompt = game_state["true_prompt"]
    for ans in game_state["answers"]:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"@{ans['user']}, твой результат: {ans['score']} баллов.\nИстинный промпт: {true_prompt}"
        )
    game_state.update({"active": False, "true_prompt": None, "image_file_id": None, "answers": []})


# --- main ---
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setprompt", setprompt))
    app.add_handler(CommandHandler("results", results))
    app.add_handler(CommandHandler("finish", finish))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_prompt))
    app.run_polling()


if __name__ == "__main__":
    main()
