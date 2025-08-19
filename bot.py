import logging
import random
import openpyxl
from io import BytesIO
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Настройки ---
TOKEN = ""   # подставь свой токен
ADMIN_ID = 1234           # id админа в телеграме

# --- Глобальное состояние (память) ---
game_state = {
    "active": False,
    "true_prompt": None,
    "image_file_id": None,

    # участники (индивидуальные чаты): {user_id}
    "subscribers": set(),

    # отображаемые имена: {user_id: "username or full name"}
    "usernames": {},

    # ответы текущего раунда: {user_id: {"user": str, "prompt": str, "score": int}}
    "answers": {}
}

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Мок-оценка (рандом 0..100). Заменишь на ollama при желании ---
def score_prompt(_: str, __: str) -> int:
    return random.randint(0, 100)

def display_name(update: Update) -> str:
    return update.effective_user.username or update.effective_user.full_name or str(update.effective_user.id)

def add_subscriber(update: Update):
    uid = update.effective_user.id
    if uid != ADMIN_ID:  # админа не добавляем в список игроков
        game_state["subscribers"].add(uid)
        game_state["usernames"][uid] = display_name(update)

# --- Хэндлеры ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_subscriber(update)
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Привет, админ! 1) Кинь картинку. 2) /setprompt <истинный промпт> — старт раунда.")
    else:
        await update.message.reply_text("Битва промптов скоро начнётся. Ждите старта!")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    photo = update.message.photo[-1]  # лучшее качество
    game_state["image_file_id"] = photo.file_id
    await update.message.reply_text("Картинка сохранена. Теперь задай истинный промпт командой /setprompt <текст>.")

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

    # старт раунда
    game_state["true_prompt"] = true_prompt
    game_state["active"] = True
    game_state["answers"].clear()

    # оповещение: всем подписчикам + админу рассылаем текст и картинку
    targets = set(game_state["subscribers"])
    targets.add(ADMIN_ID)

    for uid in targets:
        try:
            await context.bot.send_message(chat_id=uid, text="🚀 Раунд начался! Отправьте свой промпт одним сообщением.")
            await context.bot.send_photo(chat_id=uid, photo=game_state["image_file_id"])
        except Exception as e:
            logger.warning(f"Не удалось отправить пользователю {uid}: {e}")

    await update.message.reply_text("Ок! Истинный промпт установлен, рассылка отправлена.")

async def handle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # регистрируем участника при любом сообщении
    add_subscriber(update)

    # игнорируем сообщения, если раунд не активен или это админ (админ не играет)
    if not game_state["active"] or update.effective_user.id == ADMIN_ID:
        return

    user_id = update.effective_user.id
    user_prompt = (update.message.text or "").strip()
    if not user_prompt:
        return

    # считаем скор (НЕ показываем его игроку)
    sc = score_prompt(user_prompt, game_state["true_prompt"])
    game_state["answers"][user_id] = {
        "user": game_state["usernames"].get(user_id, display_name(update)),
        "prompt": user_prompt,
        "score": sc
    }

    # игроку — только подтверждение без очков
    await update.message.reply_text("Принято! Твой промпт записан.")

    # админу — уведомление о новом ответе (с промптом и скором)
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📝 Новый ответ от @{game_state['usernames'].get(user_id, user_id)}\n"
                 f"Промпт: {user_prompt}\n"
                 f"Скор: {sc}"
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить админа: {e}")

async def results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ник", "предложенный промпт", "очки"])

    # выгрузим в порядке убывания очков
    for uid, ans in sorted(game_state["answers"].items(), key=lambda kv: kv[1]["score"], reverse=True):
        ws.append([ans["user"], ans["prompt"], ans["score"]])

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    await context.bot.send_document(chat_id=update.effective_chat.id, document=InputFile(bio, "results.xlsx"))

def build_ranking():
    """Возвращает список [(user_id, user, score, place)] с учётом тай-брейков (1,2,2,4...)."""
    # сортируем
    ordered = sorted(
        ((uid, v["user"], v["score"]) for uid, v in game_state["answers"].items()),
        key=lambda x: x[2],
        reverse=True
    )
    result = []
    last_score = None
    last_place = 0
    index = 0
    for uid, user, score in ordered:
        index += 1
        if score == last_score:
            place = last_place
        else:
            place = index
            last_score = score
            last_place = place
        result.append((uid, user, score, place))
    return result

async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    ranking = build_ranking()
    true_prompt = game_state["true_prompt"] or "(не задан)"

    # персональная рассылка игрокам: их очки, место и истинный промпт
    for uid, user, score, place in ranking:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"🏁 Раунд завершён!\n"
                     f"Твой результат: {score} баллов\n"
                     f"Твоё место: {place}\n\n"
                     f"Истинный промпт:\n{true_prompt}"
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить результат пользователю {uid}: {e}")

    # админу — краткое резюме
    if ranking:
        top_lines = "\n".join([f"{pl}. @{user} — {sc}" for _, user, sc, pl in ranking[:10]])
    else:
        top_lines = "Нет ответов."
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Итоги раунда:\n{top_lines}")

    # сбрасываем состояние
    game_state.update({
        "active": False,
        "true_prompt": None,
        "image_file_id": None,
        "answers": {}
    })

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
