import os, re, pathlib
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
)
from solver import (
    WordleSolver, extract_guess_pairs_from_text, visualize_guess_line,
    build_constraints_report, build_pattern_string, deduce_grays_display,
    mdev_escape, allowed_letters_by_position, green_patterns_lines, yellow_patterns_lines,
    accumulate_constraints
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("wordseek-ui-bot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "words.txt").strip()

solver = None
SESSION = {}
PAGE_SIZE = 10

HELP = (
    "Reply to guesses with .db (solve) | /.gn (greens) | /.yl (yellows) | /.find PATTERN | /inf (diagnostics).\n"
    "Formats: 🟩🟨🟥🟥🟨 HEART | GYBBY CRANE | G Y B B Y AUDIO\n"
    "Commands: .db /.gn /.yl /.find  /db /gn /yl /find /inf /wstats /top /reload /help"
)

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

async def safe_delete_message(update: Update):
    try:
        if update.effective_message:
            await update.effective_message.delete()
    except Exception:
        pass

def abs_path(p: str) -> str:
    if os.path.isabs(p):
        return p
    return str((pathlib.Path(__file__).parent / p).resolve())

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(mdev_escape("WordSeek Solver ready.\n" + HELP), parse_mode=ParseMode.MARKDOWN_V2)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(mdev_escape(HELP), parse_mode=ParseMode.MARKDOWN_V2)

async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global solver
    path = abs_path(WORDLIST_PATH)
    try:
        solver = WordleSolver.from_file(path)
        await update.message.reply_text(mdev_escape(f"Reloaded {len(solver.words)} words from {path}."), parse_mode=ParseMode.MARKDOWN_V2)
    except FileNotFoundError:
        await update.message.reply_text(mdev_escape(f"words.txt not found at {path}"), parse_mode=ParseMode.MARKDOWN_V2)

async def wstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Diagnostic: show total words and 5-letter coverage vs raw lines
    path = abs_path(WORDLIST_PATH)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = [ln.strip() for ln in f if ln.strip()]
        from solver import WordleSolver as WS
        sanitized = WS.sanitize_word_list(raw)
        msg = f"File: {path}\nRaw lines: {len(raw)}\n5-letter sanitized: {len(sanitized)}\nLoaded in solver: {len(solver.words) if solver else 0}"
        await update.message.reply_text(mdev_escape(msg), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(mdev_escape(f"wstats error: {e}"), parse_mode=ParseMode.MARKDOWN_V2)

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ranked = solver.rank_words(solver.words)[:20]
    lines = "\n".join(f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(ranked))
    await update.message.reply_text(mdev_escape("Top starters:\n" + lines), parse_mode=ParseMode.MARKDOWN_V2)

def build_allowed_grid_hint(result):
    allowed = allowed_letters_by_position(result["greens"], result["yellows_not_pos"], result["min_counts"], result["max_counts"])
    lines = []
    for i in range(5):
        col = "".join(sorted(allowed[i])) if allowed[i] else "-"
        lines.append(f"  {i+1}: {col}")
    return "\n".join(lines)

def pattern_matches_strict(greens: dict, yellows_not_pos: dict, wordlist: list[str]) -> list[str]:
    out = []
    for w in wordlist:
        ok = True
        for i, ch in greens.items():
            if w[i] != ch:
                ok = False; break
        if not ok: continue
        for ch, banned in yellows_not_pos.items():
            if ch not in w:
                ok = False; break
            for pos in banned:
                if w[pos] == ch:
                    ok = False; break
            if not ok: break
        if ok:
            out.append(w)
    return out

def format_matches(words: list[str], limit=20) -> str:
    return ", ".join(words[:limit]) if words else "-"

async def quoted_send(chat, text, src_msg=None):
    rp = None
    if src_msg:
        try:
            rp = ReplyParameters(message_id=src_msg.message_id, quote_parse_mode="MarkdownV2")
        except Exception:
            rp = None
    return await chat.send_message(mdev_escape(text), parse_mode=ParseMode.MARKDOWN_V2, reply_parameters=rp)

async def dot_db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_delete_message(update)
    chat = update.effective_chat
    src = update.message.reply_to_message if update.message else None
    if not src or not src.text:
        await quoted_send(chat, "Reply to a guesses message with .db", src); return

    scan = await chat.send_message(mdev_escape("scanning ..."), parse_mode=ParseMode.MARKDOWN_V2)
    try:
        await scan.edit_text(mdev_escape("wait ..."), parse_mode=ParseMode.MARKDOWN_V2)
        pairs = extract_guess_pairs_from_text(src.text)
        await scan.edit_text(mdev_escape("done"), parse_mode=ParseMode.MARKDOWN_V2)

        result = solver.solve(pairs)
        cands = result["candidates"]
        greens_map = result["greens"]
        yellows_np = result["yellows_not_pos"]

        if not cands:
            greens = ", ".join([f"{i+1}:{ch}" for i, ch in sorted(greens_map.items())]) or "-"
            must_have = ", ".join([f"{l}:{v}" for l, v in sorted(result["min_counts"].items())]) or "-"
            bans = ", ".join(f"{ch}!@{','.join(str(i+1) for i in sorted(pos))}" for ch, pos in sorted(yellows_np.items())) or "-"
            allowed_grid = build_allowed_grid_hint(result)
            green_lines = "\n".join(green_patterns_lines(greens_map))
            yellow_lines = "\n".join(yellow_patterns_lines(yellows_np))
            strict_matches = pattern_matches_strict(greens_map, yellows_np, solver.words)
            msg = (
                "No candidates. Check inputs or wordlist.\n"
                "Pattern hints:\n"
                f"• Greens: {greens}\n"
                f"• Must-have counts: {must_have}\n"
                f"• Yellow bans: {bans}\n"
                f"• Allowed letters per position:\n{allowed_grid}\n"
                f"{green_lines}\n"
                f"{yellow_lines}\n"
                f"Pattern matches from words.txt (greens+yellow bans, top 20): {format_matches(strict_matches)}"
            )
            await quoted_send(chat, msg, src)
            return

        ranked = solver.rank_words(cands)
        best = ranked
        pattern = build_pattern_string(result)
        greens = ", ".join([f"{i+1}:{ch}" for i, ch in sorted(greens_map.items())]) or "-"
        yellows = ", ".join([f"{ch} !@ {','.join(str(i+1) for i in sorted(pos))}" for ch, pos in sorted(yellows_np.items())]) or "-"
        minc = ", ".join([f"{l}:{v}" for l, v in sorted(result["min_counts"].items())]) or "-"
        maxc = ", ".join([f"{l}:{v}" for l, v in sorted(result["max_counts"].items())]) or "-"
        grays = deduce_grays_display(pairs)
        strict_matches = pattern_matches_strict(greens_map, yellows_np, cands)

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
            f"Top suggestions \\(page {page+1}\\):\n{top_list}\n"
            f"Pattern matches \\(greens+yellow bans\\): {format_matches(strict_matches)}"
        )
        sent = await quoted_send(chat, msg, src)
        SESSION[(sent.chat_id, sent.message_id)] = {"ranked": ranked, "page": page, "best": best}
    except Exception as e:
        try: await scan.edit_text(mdev_escape("error"), parse_mode=ParseMode.MARKDOWN_V2)
        except: pass
        await quoted_send(chat, f"Parse error: {e}", src)

async def inf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_delete_message(update)
    chat = update.effective_chat
    src = update.message.reply_to_message if update.message else None
    if not src or not src.text:
        await quoted_send(chat, "Reply to a guesses message with /inf", src); return

    step = await chat.send_message(mdev_escape("scanning ..."), parse_mode=ParseMode.MARKDOWN_V2)
    try:
        await step.edit_text(mdev_escape("wait ..."), parse_mode=ParseMode.MARKDOWN_V2)
        pairs = extract_guess_pairs_from_text(src.text)
        await step.edit_text(mdev_escape("done"), parse_mode=ParseMode.MARKDOWN_V2)

        viz = "\n".join(visualize_guess_line(w, fb) for (w, fb) in pairs)
        report = build_constraints_report(pairs)
        greens_map, yellows_np, _, _ = accumulate_constraints(pairs)
        green_lines = "\n".join(green_patterns_lines(greens_map))
        yellow_lines = "\n".join(yellow_patterns_lines(yellows_np))
        greens_section = "Greens:\n" + ("\n".join(f"{ch.upper()} → position {i+1}" for i, ch in sorted(greens_map.items())) if greens_map else "—")
        yellows_section = "Yellows (banned positions):\n" + (
            "\n".join(f"{ch.upper()} → not at {', '.join(str(i+1) for i in sorted(pos))}" for ch, pos in sorted(yellows_np.items()))
            if yellows_np else "—"
        )
        strict_matches = pattern_matches_strict(greens_map, yellows_np, solver.words)
        final = (
            "Info:\nPer-guess breakdown:\n" + viz + "\n\n" +
            report + "\n\n" +
            greens_section + "\n" +
            yellows_section + "\n\n" +
            green_lines + "\n" +
            yellow_lines + "\n" +
            "Matches from words.txt (greens+yellow bans): " + format_matches(strict_matches)
        )
        await quoted_send(chat, final, src)
    except Exception as e:
        try: await step.edit_text(mdev_escape("error"), parse_mode=ParseMode.MARKDOWN_V2)
        except: pass
        await quoted_send(chat, f"Parse error: {e}", src)

async def gn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_delete_message(update)
    chat = update.effective_chat
    src = update.message.reply_to_message if update.message else None
    if not src or not src.text:
        await quoted_send(chat, "Reply to guesses with /.gn", src); return
    try:
        pairs = extract_guess_pairs_from_text(src.text)
        greens_map, _, _, _ = accumulate_constraints(pairs)
        greens_section = "Greens:\n" + ("\n".join(f"{ch.upper()} → position {i+1}" for i, ch in sorted(greens_map.items())) if greens_map else "—")
        green_lines = "\n".join(green_patterns_lines(greens_map))
        await quoted_send(chat, greens_section + "\n" + green_lines, src)
    except Exception as e:
        await quoted_send(chat, f"Parse error: {e}", src)

async def yl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_delete_message(update)
    chat = update.effective_chat
    src = update.message.reply_to_message if update.message else None
    if not src or not src.text:
        await quoted_send(chat, "Reply to guesses with /.yl", src); return
    try:
        pairs = extract_guess_pairs_from_text(src.text)
        _, yellows_np, _, _ = accumulate_constraints(pairs)
        yellow_lines = "\n".join(yellow_patterns_lines(yellows_np))
        await quoted_send(chat, yellow_lines, src)
    except Exception as e:
        await quoted_send(chat, f"Parse error: {e}", src)

def parse_pattern_arg(args: list[str]) -> str:
    if not args: return ""
    raw = " ".join(args).strip()
    if " " in raw:
        parts = raw.split()
        if len(parts) == 5:
            return " ".join(p.lower() for p in parts)
        return ""
    if len(raw) == 5:
        return " ".join(ch.lower() for ch in raw)
    return ""

def filter_by_pattern_and_yellows(pattern_str: str, yellows_np: dict, wordlist: list[str]) -> list[str]:
    parts = pattern_str.split()
    if len(parts) != 5:
        return []
    greens = {i: p for i, p in enumerate(parts) if p != "_"}
    return pattern_matches_strict(greens, yellows_np, wordlist)

async def find_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_delete_message(update)
    chat = update.effective_chat
    src = update.message.reply_to_message if update.message else None

    yellows_np = {}
    if src and src.text:
        try:
            pairs = extract_guess_pairs_from_text(src.text)
            _, yellows_np, _, _ = accumulate_constraints(pairs)
        except:
            yellows_np = {}

    pattern_str = parse_pattern_arg(context.args)
    if not pattern_str:
        await quoted_send(chat, "Usage: /.find s t o _ _  OR  /.find sto__", src); return

    matches = filter_by_pattern_and_yellows(pattern_str, yellows_np, solver.words)
    ranked = solver.rank_words(matches)[:3]
    if ranked:
        best3 = ", ".join(w for w, _ in ranked)
        await quoted_send(chat, f"Matches (top 3): {best3}", src)
    else:
        await quoted_send(chat, "No matches.", src)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    data = q.data or ""
    key = (q.message.chat_id, q.message.message_id)
    state = SESSION.get(key)
    if data.startswith("copy:"):
        parts = data.split(":", 1)
        if len(parts) == 2 and parts:
            await q.answer()
            await q.message.reply_text(mdev_escape(f"`{parts}`"), parse_mode=ParseMode.MARKDOWN_V2)
            return
        await q.answer("Bad data"); return
    if data.startswith("pg:") and state:
        try: page = int(data.split(":", 1))
        except: await q.answer("Invalid page"); return
        ranked = state["ranked"]; best = state["best"]; total = len(ranked)
        start = max(0, page * PAGE_SIZE); end = min(start + PAGE_SIZE, total)
        if start >= total: await q.answer("No more pages"); return
        state["page"] = page
        top_list = "\n".join(f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(ranked[start:end], start=start))
        text = q.message.text or ""
        new_msg = text.split("Top suggestions") + f"Top suggestions \\(page {page+1}\\):\n{top_list}"
        await q.edit_message_text(mdev_escape(new_msg), parse_mode=ParseMode.MARKDOWN_V2,
                                  reply_markup=build_keyboard(best, page, end < total, page > 0))
        await q.answer(); return
    if data == "refresh" and state:
        page = state["page"]; ranked = state["ranked"]; best = state["best"]; total = len(ranked)
        start = page * PAGE_SIZE; end = min(start + PAGE_SIZE, total)
        top_list = "\n".join(f"{i+1}. {w} ({sc})" for i, (w, sc) in enumerate(ranked[start:end], start=start))
        text = q.message.text or ""
        new_msg = text.split("Top suggestions") + f"Top suggestions \\(page {page+1}\\):\n{top_list}"
        await q.edit_message_text(mdev_escape(new_msg), parse_mode=ParseMode.MARKDOWN_V2,
                                  reply_markup=build_keyboard(best, page, end < total, page > 0))
        await q.answer("Refreshed"); return
    await q.answer()

def main():
    global solver
    if not TOKEN:
        raise SystemExit("Set BOT_TOKEN")
    # Load initial
    path = abs_path(WORDLIST_PATH)
    solver = WordleSolver.from_file(path)

    app = ApplicationBuilder().token(TOKEN).build()

    # Slash commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(CommandHandler("wstats", wstats_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("inf", inf_cmd))
    app.add_handler(CommandHandler("info", inf_cmd))
    app.add_handler(CommandHandler("db", dot_db_cmd))
    app.add_handler(CommandHandler("gn", gn_cmd))
    app.add_handler(CommandHandler("yl", yl_cmd))
    app.add_handler(CommandHandler("find", find_cmd))

    # Dot triggers
    async def dot_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        t = update.message.text.strip()
        if t == ".db":
            await dot_db_cmd(update, context)
        elif t == ".gn":
            await gn_cmd(update, context)
        elif t == ".yl":
            await yl_cmd(update, context)
        elif t.startswith(".find"):
            context.args = t.split()[1:] if len(t.split()) > 1 else []
            await find_cmd(update, context)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), dot_router))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.run_polling(drop_pending_updates=True, poll_interval=0.5)

if __name__ == "__main__":
    main()
