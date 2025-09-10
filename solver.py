import re
import unicodedata
from collections import Counter, defaultdict

EMOJI_GREEN = "🟩"
EMOJI_YELLOW = "🟨"
EMOJI_GRAY = "🟥"
ALT_GRAY = {"⬛", "⬜"}  # accept as gray
VALID_FB = {"G","Y","B"}

# MarkdownV2 specials that need escaping
MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!"

def mdev_escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in MDV2_SPECIALS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)

def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for blk in ALT_GRAY:
        s = s.replace(blk, EMOJI_GRAY)
    cleaned = []
    for ch in s:
        if ch in (EMOJI_GREEN, EMOJI_YELLOW, EMOJI_GRAY):
            cleaned.append(ch); continue
        if ch.isalpha() or ch.isspace():
            cleaned.append(ch); continue
        if ch in "-_'":
            cleaned.append(" "); continue
        # drop decorative chars
    return "".join(cleaned)

def strip_to_ascii_letters(word: str) -> str:
    w = unicodedata.normalize("NFKC", word)
    w = "".join(ch for ch in w if ch.isalpha())
    return w.lower()

def parse_line(line):
    s = normalize_text(line).strip()
    if not s:
        return None
    # Emoji tiles + word
    m = re.match(rf"^([{EMOJI_GREEN}{EMOJI_YELLOW}{EMOJI_GRAY}\s]{{5,}})\s+([A-Za-z\s]{{3,}})$", s)
    if m:
        tiles_raw, word_raw = m.group(1), m.group(2)
        tiles = [ch for ch in tiles_raw if ch in (EMOJI_GREEN, EMOJI_YELLOW, EMOJI_GRAY)]
        if len(tiles) == 5:
            fb = "".join("G" if ch == EMOJI_GREEN else "Y" if ch == EMOJI_YELLOW else "B" for ch in tiles)
            word = strip_to_ascii_letters(word_raw)
            if len(word) == 5:
                return (word, fb)
    toks = s.split()
    # GYBBY WORD
    if len(toks) >= 2 and len(toks) == 5 and set(toks.upper()).issubset(VALID_FB):
        word = strip_to_ascii_letters(toks[-1])
        if len(word) == 5:
            return (word, toks.upper())
    # G Y B B Y WORD
    if len(toks) >= 6 and all(t.upper() in VALID_FB for t in toks[:5]):
        word = strip_to_ascii_letters(toks[5])
        if len(word) == 5:
            return (word, "".join(t.upper() for t in toks[:5]))
    return None

def extract_guess_pairs_from_text(text):
    pairs = []
    for raw in text.splitlines():
        p = parse_line(raw)
        if p:
            pairs.append(p)
    if not pairs:
        raise ValueError("No valid guess lines found. Use emojis+word, 'GYBBY WORD', or 'G Y B B Y WORD'.")
    return pairs

def accumulate_constraints(guesses):
    greens = {}
    yellows_not_pos = defaultdict(set)
    global_min = Counter()
    global_max_known = {}
    for word, fb in guesses:
        gy_counts = Counter()
        for i, (ch, fl) in enumerate(zip(word, fb)):
            if fl == "G":
                greens[i] = ch; gy_counts[ch] += 1
            elif fl == "Y":
                yellows_not_pos[ch].add(i); gy_counts[ch] += 1
        # derive max per letter within this guess from grays
        per_guess_max = {}
        gc = Counter(word)
        for l, k in gc.items():
            r = gy_counts[l]
            if r < k:
                per_guess_max[l] = r
        # merge mins and maxs
        for l, r in gy_counts.items():
            if r > global_min[l]:
                global_min[l] = r
        for l, mx in per_guess_max.items():
            if l in global_max_known:
                global_max_known[l] = min(global_max_known[l], mx)
            else:
                global_max_known[l] = mx
    return greens, yellows_not_pos, dict(global_min), global_max_known

def word_satisfies(word, greens, yellows_not_pos, min_counts, max_counts):
    for i, ch in greens.items():
        if word[i] != ch:
            return False
    for ch, banned in yellows_not_pos.items():
        if ch not in word:
            return False
        for pos in banned:
            if word[pos] == ch:
                return False
    wc = Counter(word)
    for l, m in min_counts.items():
        if wc[l] < m:
            return False
    for l, mx in max_counts.items():
        if wc[l] > mx:
            return False
    return True

def freq_score(cands):
    letter_freq = Counter()
    for w in cands:
        for ch in w:
            letter_freq[ch] += 1
    scores = {}
    for w in cands:
        scores[w] = sum(letter_freq[ch] for ch in set(w))
    return scores

class WordleSolver:
    def __init__(self, words):
        self.words = [w for w in words if len(w)==5 and w.isalpha() and w.islower()]

    @classmethod
    def from_file(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            words = [ln.strip() for ln in f if ln.strip()]
        return cls(words)

    def solve(self, guesses):
        greens, yellows_not_pos, min_counts, max_counts = accumulate_constraints(guesses)
        cands = [w for w in self.words if word_satisfies(w, greens, yellows_not_pos, min_counts, max_counts)]
        return {
            "greens": greens,
            "yellows_not_pos": yellows_not_pos,
            "min_counts": min_counts,
            "max_counts": max_counts,
            "candidates": cands,
        }

    def rank_words(self, words):
        scores = freq_score(words)
        return sorted(((w, scores[w]) for w in words), key=lambda x: (-x[4], x))

def visualize_guess_line(word, fb):
    tags = []
    for i, (ch, f) in enumerate(zip(word.upper(), fb), 1):
        tags.append(f"{i}:{ch}({f})")
    return " - " + " ".join(tags)

def build_constraints_report(pairs):
    greens, ynp, minc, maxc = accumulate_constraints(pairs)
    g_line = "Greens: " + (", ".join([f"{i+1}:{ch}" for i, ch in sorted(greens.items())]) or "-")
    y_lines = []
    for ch, posset in sorted(ynp.items()):
        y_lines.append(f"{ch}: not at {', '.join(str(i+1) for i in sorted(posset))}")
    y_block = "Yellows (position bans): " + (", ".join(y_lines) if y_lines else "-")
    min_line = "Min counts: " + (", ".join([f"{l}:{v}" for l, v in sorted(minc.items())]) or "-")
    max_line = "Max counts: " + (", ".join([f"{l}:{v}" for l, v in sorted(maxc.items())]) or "-")
    letters_seen = sorted({l for w, fb in pairs for l in set(w)})
    allowed_lines = []
    for l in letters_seen:
        allowed = []
        for i in range(5):
            if i in greens and greens[i] != l:
                continue
            if l in ynp and i in ynp[l]:
                continue
            allowed.append(str(i+1))
        if allowed:
            allowed_lines.append(f"{l}: {', '.join(allowed)}")
    allowed_block = "Allowed positions (by bans): " + (", ".join(allowed_lines) if allowed_lines else "-")
    seen_g_y, seen_b = set(), set()
    for w, fb in pairs:
        for ch, f in zip(w, fb):
            if f in ("G","Y"): seen_g_y.add(ch)
            elif f == "B": seen_b.add(ch)
    grays = sorted([ch for ch in seen_b if ch not in seen_g_y])
    gray_line = "Gray-only letters: " + (", ".join(grays).upper() if grays else "-")
    return "\n".join([g_line, y_block, min_line, max_line, allowed_block, gray_line])

def build_pattern_string(result):
    patt = ["_"] * 5
    for i, ch in result["greens"].items():
        patt[i] = ch
    return " ".join(patt)

def deduce_grays_display(pairs):
    seen_g_y, seen_b = set(), set()
    for w, fb in pairs:
        for ch, f in zip(w, fb):
            if f in ("G", "Y"):
                seen_g_y.add(ch)
            elif f == "B":
                seen_b.add(ch)
    grays = sorted([ch for ch in seen_b if ch not in seen_g_y])
    return ", ".join(grays) if grays else "-"
                
