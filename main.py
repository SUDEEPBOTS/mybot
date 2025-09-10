import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from solver import (
    WordleSolver,
    extract_guess_pairs_from_text,
    visualize_guess_line,
    build_constraints_report,
    build_pattern_string,
    deduce_grays_display,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("wordseek-bot")

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

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("WordSeek Solver ready.\n" + HELP)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)

async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global solver
    solver = WordleSolver.from_file(WORDLIST_PATH)
    await update.message.reply_text(f"Reloaded {len(solver.words)} words.")

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ranked = solver.rank_words(solver.words)
    lines = [f"{w} ({sc})" for w, sc in ranked[:20]]
    await update.message.reply_text("Top starters:\n" + "\n".join(lines))

async def dot_db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Must be a reply to guesses message
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Reply to a guesses message with .db")
        return
    text = (update.message.reply_to_message.text or "").strip()
    if not text:
        await update.message.reply_text("Replied message has no text.")
        return

    step = await update.message.reply_text("scanning ...")
    try:
        await step.edit_text("wait ...")
        pairs = extract_guess_pairs_from_text(text)
        await step.edit_text("done")

        result = solver.solve(pairs)
        cands = result["candidates"]
        if not cands:
            await update.message.reply_text("No candidates. Check inputs or wordlist.")
            return

        greens = ", ".join([f"{i+1}:{ch}" for i, ch in sorted(result['greens'].items())]) or "-"
        yellows = ", ".join([f"{ch} !@ {','.join(str(i+1) for i in sorted(pos))}" for ch, pos in sorted(result['yellows_not_pos'].items())]) or "-"
        minc = ", ".join([f"{l}:{v}" for l, v in sorted(result["min_counts"].items())]) or "-"
        maxc = ", ".join([f"{l}:{v}" for l, v in sorted(result["max_counts"].items())]) or "-"
        grays = deduce_grays_display(pairs)

        ranked = solver.rank_words(cands)
        best = ranked
        pattern = build_pattern_string(result)

        topn = ranked[:20]
        top_lines = "\n".join([f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(topn)])

        msg = (
            "Analysis:\n"
            f"✅ Greens: {greens}\n"
            f"🟨 Yellows: {yellows}\n"
            f"❌ Grays: {grays}\n"
            f"Pattern: {pattern}\n"
            f"Remaining: {len(ranked)}\n"
            f"👉 Suggestions: {', '.join(w for w, _ in topn[:3])}\n"
            f"🎯 Best Answer: `{best}`\n"
            f"Top suggestions:\n{top_lines}"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        try:
            await step.edit_text("error")
        except:
            pass
        await update.message.reply_text(f"Parse error: {e}")

async def dot_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Reply to a guesses message with .info")
        return
    text = (update.message.reply_to_message.text or "").strip()
    if not text:
        await update.message.reply_text("Replied message has no text.")
        return

    step = await update.message.reply_text("scanning ...")
    try:
        await step.edit_text("wait ...")
        pairs = extract_guess_pairs_from_text(text)
        await step.edit_text("done")

        viz = "\n".join(visualize_guess_line(w, fb) for (w, fb) in pairs)
        report = build_constraints_report(pairs)
        await update.message.reply_text("Info:\nPer-guess breakdown:\n" + viz + "\n\n" + report)
    except Exception as e:
        try:
            await step.edit_text("error")
        except:
            pass
        await update.message.reply_text(f"Parse error: {e}")

def main():
    global solver
    if not TOKEN:
        raise SystemExit("Set BOT_TOKEN")
    solver = WordleSolver.from_file(WORDLIST_PATH)

    app = ApplicationBuilder().token(TOKEN).build()

    # Slash commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(CommandHandler("top", top_cmd))

    # Dot commands as CommandHandler for “/.db” and “/.info” compatibility
    app.add_handler(CommandHandler(".db", dot_db_cmd))
    app.add_handler(CommandHandler(".info", dot_info_cmd))

    # Plain “.db” / “.info” text triggers
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND),
                                   lambda u, c: dot_db_cmd(u, c) if u.message and u.message.text and u.message.text.strip() == ".db" else None))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND),
                                   lambda u, c: dot_info_cmd(u, c) if u.message and u.message.text and u.message.text.strip() == ".info" else None))

    app.run_polling(drop_pending_updates=True, poll_interval=0.5)

if __name__ == "__main__":
    main()
    
