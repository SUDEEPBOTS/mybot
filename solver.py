import re
import unicodedata
from collections import Counter, defaultdict

# Emoji tiles supported
EMOJI_GREEN = "🟩"
EMOJI_YELLOW = "🟨"
EMOJI_GRAY = "🟥"
# Alternate gray tiles commonly seen
ALT_GRAY = {"⬛", "⬜"}

# Feedback letters
VALID_FB = {"G", "Y", "B"}

# MarkdownV2 specials to escape for Telegram
MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!"

def mdev_escape(text: str) -> str:
    """
    Escape text for Telegram MarkdownV2 so that underscores, parentheses, etc. don't break formatting.
    """
    out = []
    for ch in text:
        if ch in MDV2_SPECIALS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)

def normalize_text(s: str) -> str:
    """
    - Normalize Unicode styles to NFKC (handles bold/italic math letters to ASCII)
    - Map alt gray tiles to a unified gray
    - Keep letters, spaces, and our tile emojis; drop other decorative symbols
    """
    s = unicodedata.normalize("NFKC", s)
    for blk in ALT_GRAY:
        s = s.replace(blk, EMOJI_GRAY)
    cleaned = []
    for ch in s:
        if ch in (EMOJI_GREEN, EMOJI_YELLOW, EMOJI_GRAY):
            cleaned.append(ch)
            continue
        if ch.isalpha() or ch.isspace():
            cleaned.append(ch)
            continue
        if ch in "-_'":
            cleaned.append(" ")
            continue
        # drop other decorations
    return "".join(cleaned)

def strip_to_ascii_letters(word: str) -> str:
    """
    Keep alphabetic characters only, downcase them after NFKC normalization.
    """
    w = unicodedata.normalize("NFKC", word)
    w = "".join(ch for ch in w if ch.isalpha())
    return w.lower()

def parse_line(line: str):
    """
    Parse a single line into (word, feedback) where feedback is 5 letters in {G,Y,B}.
    Supports:
    - Emoji tiles + word (spaces between tiles allowed): '🟩🟨🟥🟥🟨 HEART'
    - Shorthand: 'GYBBY CRANE'
    - Spaced: 'G Y B B Y AUDIO'
    Handles unicode-styled words and extra decorations.
    """
    s = normalize_text(line).strip()
    if not s:
        return None

    # Emoji tiles + word (tolerate spaces between tiles)
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
        word = strip_to_ascii_letters(toks[4])
        if len(word) == 5:
            return (word, "".join(t.upper() for t in toks[:5]))

    return None

def extract_guess_pairs_from_text(text: str):
    """
    Extract (word, feedback) pairs from a multi-line text blob.
    """
    pairs = []
    for raw in text.splitlines():
        p = parse_line(raw)
        if p:
            pairs.append(p)
    if not pairs:
        raise ValueError("No valid guess lines found. Use emojis+word, 'GYBBY WORD', or 'G Y B B Y WORD'.")
    return pairs

def accumulate_constraints(guesses):
    """
    Build constraints from guesses handling duplicates:
    - greens: fixed letters by position
    - yellows_not_pos: letter -> positions it cannot occupy
    - min_counts: letter -> minimal total count demanded by G/Y across guesses
    - max_counts: letter -> maximal total count implied when extra gray copies guessed
    """
    greens = {}
    yellows_not_pos = defaultdict(set)
    global_min = Counter()
    global_max_known = {}

    for word, fb in guesses:
        gy_counts = Counter()
        for i, (ch, fl) in enumerate(zip(word, fb)):
            if fl == "G":
                greens[i] = ch
                gy_counts[ch] += 1
            elif fl == "Y":
                yellows_not_pos[ch].add(i)
                gy_counts[ch] += 1
        # Per-guess max: if guessed k copies, but only r marked G/Y, then max occurrences <= r
        per_guess_max = {}
        gc = Counter(word)
        for l, k in gc.items():
            r = gy_counts[l]
            if r < k:
                per_guess_max[l] = r

        # Merge mins and maxs
        for l, r in gy_counts.items():
            if r > global_min[l]:
                global_min[l] = r
        for l, mx in per_guess_max.items():
            global_max_known[l] = min(global_max_known.get(l, mx), mx)

    return greens, yellows_not_pos, dict(global_min), global_max_known

def word_satisfies(word, greens, yellows_not_pos, min_counts, max_counts):
    """
    Check if a candidate satisfies all constraints.
    """
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

def positional_frequencies(words):
    """
    Compute per-position and global letter frequencies.
    """
    pos_freq = [Counter() for _ in range(5)]
    global_freq = Counter()
    for w in words:
        for i, ch in enumerate(w):
            pos_freq[i][ch] += 1
            global_freq[ch] += 1
    return pos_freq, global_freq

def intelligent_scores(cands):
    """
    Composite heuristic score:
    - Positional frequency sum (encourages likely letters in position)
    - Coverage score across unique letters (global freq)
    - Vowel coverage bonus
    - Duplicate penalty to favor diversity
    """
    pos_freq, global_freq = positional_frequencies(cands)
    vowels = set("aeiou")
    scores = {}
    for w in cands:
        uniq = list(dict.fromkeys(w))  # preserve order, remove duplicates
        pos_score = sum(pos_freq[i][ch] for i, ch in enumerate(w))
        cov_score = sum(global_freq[ch] for ch in uniq)
        vowel_bonus = sum(1 for ch in uniq if ch in vowels) * 50
        dup_pen = (len(w) - len(uniq)) * 100
        scores[w] = pos_score + cov_score + vowel_bonus - dup_pen
    return scores

def allowed_letters_by_position(greens, yellows_not_pos):
    """
    Compute allowed letters per position based on current bans and greens.
    """
    alphabet = set("abcdefghijklmnopqrstuvwxyz")
    allowed = [set(alphabet) for _ in range(5)]
    # Greens fix a position
    for i, ch in greens.items():
        allowed[i] = {ch}
    # Yellows cannot be at banned positions
    for ch, banned in yellows_not_pos.items():
        for i in banned:
            if ch in allowed[i] and (i not in greens or greens[i] != ch):
                allowed[i].discard(ch)
    return allowed

def green_patterns_lines(greens):
    """
    Build display lines for green-only patterns.
    """
    patt = ["_"] * 5
    for i, ch in greens.items():
        patt[i] = ch
    return [
        "Green pattern: " + " ".join(patt),
        "Green pattern (compact): " + "".join(patt)
    ]

class WordleSolver:
    def __init__(self, words):
        # keep only proper 5-letter lowercase words
        self.words = [w for w in words if len(w) == 5 and w.isalpha() and w.islower()]

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
        if not words:
            return []
        scores = intelligent_scores(words)
        return sorted(((w, scores[w]) for w in words), key=lambda x: (-x[5], x))

def visualize_guess_line(word, fb):
    """
    Per-guess positional visualization like:
    ' - 1:H(B) 2:E(B) 3:A(G) 4:R(Y) 5:T(B)'
    """
    tags = []
    for i, (ch, f) in enumerate(zip(word.upper(), fb), 1):
        tags.append(f"{i}:{ch}({f})")
    return " - " + " ".join(tags)

def build_constraints_report(pairs):
    """
    Human-readable constraints summary with greens, yellows bans, min/max counts,
    allowed positions sketch, and gray-only letters.
    """
    greens, ynp, minc, maxc = accumulate_constraints(pairs)

    g_line = "Greens: " + (", ".join([f"{i+1}:{ch}" for i, ch in sorted(greens.items())]) or "-")

    y_lines = []
    for ch, posset in sorted(ynp.items()):
        y_lines.append(f"{ch}: not at {', '.join(str(i+1) for i in sorted(posset))}")
    y_block = "Yellows (position bans): " + (", ".join(y_lines) if y_lines else "-")

    min_line = "Min counts: " + (", ".join([f"{l}:{v}" for l, v in sorted(minc.items())]) or "-")
    max_line = "Max counts: " + (", ".join([f"{l}:{v}" for l, v in sorted(maxc.items())]) or "-")

    # Allowed positions sketch for letters seen in guesses
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

    # Display-only grays: letters seen as B and never G/Y in that same guess
    seen_g_y, seen_b = set(), set()
    for w, fb in pairs:
        for ch, f in zip(w, fb):
            if f in ("G", "Y"):
                seen_g_y.add(ch)
            elif f == "B":
                seen_b.add(ch)
    grays = sorted([ch for ch in seen_b if ch not in seen_g_y])
    gray_line = "Gray-only letters: " + (", ".join(grays).upper() if grays else "-")

    return "\n".join([g_line, y_block, min_line, max_line, allowed_block, gray_line])

def build_pattern_string(result):
    """
    Build a simple pattern string from greens: e.g., 'S T O _ _'
    """
    patt = ["_"] * 5
    for i, ch in result["greens"].items():
        patt[i] = ch
    return " ".join(patt)

def deduce_grays_display(pairs):
    """
    Display-only gray letters across guesses (not used for strict filtering).
    """
    seen_g_y, seen_b = set(), set()
    for w, fb in pairs:
        for ch, f in zip(w, fb):
            if f in ("G", "Y"):
                seen_g_y.add(ch)
            elif f == "B":
                seen_b.add(ch)
    grays = sorted([ch for ch in seen_b if ch not in seen_g_y])
    return ", ".join(grays) if grays else "-"
            
