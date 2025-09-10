import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
)
from solver import (
    WordleSolver, extract_guess_pairs_from_text, visualize_guess_line,
    build_constraints_report, build_pattern_string, deduce_grays_display, mdev_escape
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("wordseek-ui-bot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "words.txt")

solver = None

HELP = (
    "Reply to a guesses message with .db (solve) or .info (diagnostics).\n"
    "Accepted lines (decorations/uppercase ok):\n"
    "🟩🟨🟥🟥🟨 HEART\n"
    "GYBBY CRANE\n"
    "G Y B B Y AUDIO\n"
    "Commands: .db  .info  /top  /reload  /help"
)

# In-memory paging state per message
SESSION = {}  # key: (chat_id, message_id) -> {'ranked': [(word,score)], 'page': int, 'best': str}
PAGE_SIZE = 10

def build_keyboard(best_word: str, page: int, has_next: bool, has_prev: bool):
    buttons = []
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pg:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"pg:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton("📋 Copy Best", callback_data=f"copy:{best_word}"),
        InlineKeyboardButton("🔁 Refresh", callback_data="refresh")
    ])
    return InlineKeyboardMarkup(buttons)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(mdev_escape("WordSeek Solver ready.\n" + HELP), parse_mode=ParseMode.MARKDOWN_V2)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(mdev_escape(HELP), parse_mode=ParseMode.MARKDOWN_V2)

async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global solver
    solver = WordleSolver.from_file(WORDLIST_PATH)
    await update.message.reply_text(mdev_escape(f"Reloaded {len(solver.words)} words."), parse_mode=ParseMode.MARKDOWN_V2)

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ranked = solver.rank_words(solver.words)[:20]
    lines = "\n".join(f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(ranked))
    await update.message.reply_text(mdev_escape("Top starters:\n" + lines), parse_mode=ParseMode.MARKDOWN_V2)

async def dot_db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text(mdev_escape("Reply to a guesses message with .db"), parse_mode=ParseMode.MARKDOWN_V2)
        return
    text = (update.message.reply_to_message.text or "").strip()
    if not text:
        await update.message.reply_text(mdev_escape("Replied message has no text."), parse_mode=ParseMode.MARKDOWN_V2)
        return

    scan = await update.message.reply_text(mdev_escape("scanning ..."), parse_mode=ParseMode.MARKDOWN_V2)
    try:
        await scan.edit_text(mdev_escape("wait ..."), parse_mode=ParseMode.MARKDOWN_V2)
        pairs = extract_guess_pairs_from_text(text)
        await scan.edit_text(mdev_escape("done"), parse_mode=ParseMode.MARKDOWN_V2)

        result = solver.solve(pairs)
        cands = result["candidates"]
        if not cands:
            await update.message.reply_text(mdev_escape("No candidates. Check inputs or wordlist."), parse_mode=ParseMode.MARKDOWN_V2)
            return

        ranked = solver.rank_words(cands)
        best = ranked
        pattern = build_pattern_string(result)
        greens = ", ".join([f"{i+1}:{ch}" for i, ch in sorted(result['greens'].items())]) or "-"
        yellows = ", ".join([f"{ch} !@ {','.join(str(i+1) for i in sorted(pos))}" for ch, pos in sorted(result['yellows_not_pos'].items())]) or "-"
        minc = ", ".join([f"{l}:{v}" for l, v in sorted(result["min_counts"].items())]) or "-"
        maxc = ", ".join([f"{l}:{v}" for l, v in sorted(result["max_counts"].items())]) or "-"
        grays = deduce_grays_display(pairs)

        # Page 1
        page = 0
        total = len(ranked)
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)
        top_list = "\n".join(f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(ranked[start:end], start=start))

        msg = (
            "Analysis:\n"
            f"✅ Greens: {greens}\n"
            f"🟨 Yellows: {yellows}\n"
            f"❌ Grays: {grays}\n"
            f"Pattern: {pattern}\n"
            f"Remaining: {total}\n"
            f"👉 Suggestions: {', '.join(w for w, _ in ranked[:3])}\n"
            f"🎯 Best Answer: `{best}`\n"
            f"Top suggestions \\(page {page+1}\\):\n{top_list}"
        )
        sent = await update.message.reply_text(
            mdev_escape(msg), parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=build_keyboard(best, page, end < total, page > 0)
        )
        SESSION[(sent.chat_id, sent.message_id)] = {"ranked": ranked, "page": page, "best": best}
    except Exception as e:
        try:
            await scan.edit_text(mdev_escape("error"), parse_mode=ParseMode.MARKDOWN_V2)
        except:
            pass
        await update.message.reply_text(mdev_escape(f"Parse error: {e}"), parse_mode=ParseMode.MARKDOWN_V2)

async def dot_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text(mdev_escape("Reply to a guesses message with .info"), parse_mode=ParseMode.MARKDOWN_V2)
        return
    text = (update.message.reply_to_message.text or "").strip()
    if not text:
        await update.message.reply_text(mdev_escape("Replied message has no text."), parse_mode=ParseMode.MARKDOWN_V2)
        return

    step = await update.message.reply_text(mdev_escape("scanning ..."), parse_mode=ParseMode.MARKDOWN_V2)
    try:
        await step.edit_text(mdev_escape("wait ..."), parse_mode=ParseMode.MARKDOWN_V2)
        pairs = extract_guess_pairs_from_text(text)
        await step.edit_text(mdev_escape("done"), parse_mode=ParseMode.MARKDOWN_V2)

        viz = "\n".join(visualize_guess_line(w, fb) for (w, fb) in pairs)
        report = build_constraints_report(pairs)
        final = "Info:\nPer-guess breakdown:\n" + viz + "\n\n" + report
        await update.message.reply_text(mdev_escape(final), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        try:
            await step.edit_text(mdev_escape("error"), parse_mode=ParseMode.MARKDOWN_V2)
        except:
            pass
        await update.message.reply_text(mdev_escape(f"Parse error: {e}"), parse_mode=ParseMode.MARKDOWN_V2)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    key = (q.message.chat_id, q.message.message_id)
    state = SESSION.get(key)
    if data.startswith("copy:"):
        word = data.split(":", 1)[4]
        await q.answer()
        await q.message.reply_text(mdev_escape(f"`{word}`"), parse_mode=ParseMode.MARKDOWN_V2)
        return
    if data.startswith("pg:") and state:
        try:
            page = int(data.split(":", 1)[4])
        except:
            await q.answer("Invalid page")
            return
        ranked = state["ranked"]
        best = state["best"]
        total = len(ranked)
        start = max(0, page * PAGE_SIZE)
        end = min(start + PAGE_SIZE, total)
        if start >= total:
            await q.answer("No more pages")
            return
        state["page"] = page
        top_list = "\n".join(f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(ranked[start:end], start=start))
        text = q.message.text or ""
        new_msg = text.split("Top suggestions") + f"Top suggestions \\(page {page+1}\\):\n{top_list}"
        await q.edit_message_text(
            mdev_escape(new_msg), parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=build_keyboard(best, page, end < total, page > 0)
        )
        await q.answer()
        return
    if data == "refresh" and state:
        page = state["page"]
        ranked = state["ranked"]
        best = state["best"]
        total = len(ranked)
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)
        top_list = "\n".join(f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(ranked[start:end], start=start))
        text = q.message.text or ""
        new_msg = text.split("Top suggestions") + f"Top suggestions \\(page {page+1}\\):\n{top_list}"
        await q.edit_message_text(
            mdev_escape(new_msg), parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=build_keyboard(best, page, end < total, page > 0)
        )
        await q.answer("Refreshed")
        return
    await q.answer()

def main():
    global solver
    if not TOKEN:
        raise SystemExit("Set BOT_TOKEN")
    solver = WordleSolver.from_file(WORDLIST_PATH)

    app = ApplicationBuilder().token(TOKEN).build()

    # Slash commands (valid names)
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("db", dot_db_cmd))     # optional slash alias
    app.add_handler(CommandHandler("info", dot_info_cmd)) # optional slash alias

    # Text triggers for .db and .info (no CommandHandler on dot-names)
    async def dot_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        t = update.message.text.strip()
        if t == ".db":
            await dot_db_cmd(update, context)
        elif t == ".info":
            await dot_info_cmd(update, context)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), dot_router))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    app.run_polling(drop_pending_updates=True, poll_interval=0.5)

if __name__ == "__main__":
    main()
                             
