import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.ext import MessageHandler, filters
from solver import WordleSolver, parse_guess_lines

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("wordseek-bot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "words.txt")

solver = None  # loaded in main()

HELP_TEXT = (
    "Paste guesses (emoji tiles + word, or GYBBY + word) in a message.\n"
    "Then REPLY to that message with /solve.\n"
    "Accepted lines:\n"
    "🟩🟨🟥🟥🟨 SLATE\n"
    "GYBBY CRANE\n"
    "G Y B B Y HEART\n"
    "Commands: /solve (reply), /top, /help, /reload"
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("WordSeek Solver ready. " + HELP_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global solver
    try:
        solver = WordleSolver.from_file(WORDLIST_PATH)
        await update.message.reply_text(f"Reloaded wordlist: {len(solver.words)} words")
    except Exception as e:
        await update.message.reply_text(f"Reload failed: {e}")

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ranked = solver.rank_words(solver.words)
    top = [f"{w} ({sc})" for w, sc in ranked[:20]]
    await update.message.reply_text("Top starters:\n" + "\n".join(top))

async def solve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Reply to a guesses message with /solve.")
        return
    text = (update.message.reply_to_message.text or "").strip()
    if not text:
        await update.message.reply_text("Replied message has no text.")
        return
    try:
        pairs = parse_guess_lines(text)
    except Exception as e:
        await update.message.reply_text(f"Parse error: {e}")
        return

    result = solver.solve(pairs)
    cands = result["candidates"]
    if not cands:
        await update.message.reply_text("No candidates. Check inputs or wordlist.")
        return

    greens = ", ".join([f"{i+1}:{ch}" for i, ch in sorted(result['greens'].items())]) or "-"
    yellows = ", ".join([f"{ch} !@ {','.join(str(i+1) for i in sorted(pos))}" for ch, pos in sorted(result['yellows_not_pos'].items())]) or "-"
    minc = ", ".join([f"{l}:{v}" for l, v in sorted(result["min_counts"].items())]) or "-"
    maxc = ", ".join([f"{l}:{v}" for l, v in sorted(result["max_counts"].items())]) or "-"

    ranked = solver.rank_words(cands)
    best = ranked
    topn = ranked[:20]
    top_lines = "\n".join([f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(topn)])

    msg = (
        "Analysis:\n"
        f"✅ Greens: {greens}\n"
        f"🟨 Yellows: {yellows}\n"
        f"🔢 Min counts: {minc}\n"
        f"🔒 Max counts: {maxc}\n"
        f"Remaining: {len(ranked)}\n"
        f"🎯 Best Answer: {best}\n"
        f"Top suggestions:\n{top_lines}"
    )
    await update.message.reply_text(msg)

def main():
    global solver
    if not TOKEN:
        raise SystemExit("Set BOT_TOKEN env var.")
    solver = WordleSolver.from_file(WORDLIST_PATH)

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("solve", solve_cmd))

    # Ultra-fast polling
    app.run_polling(drop_pending_updates=True, poll_interval=0.5)

if __name__ == "__main__":
    main()
