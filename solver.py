import re
import unicodedata
from collections import Counter, defaultdict
from typing import List, Tuple, Dict

EMOJI_GREEN = "🟩"
EMOJI_YELLOW = "🟨"
EMOJI_GRAY = "🟥"
ALT_GRAY = {"⬛", "⬜"}

VALID_FB = {"G", "Y", "B"}
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
    return "".join(cleaned)

def strip_to_ascii_letters(word: str) -> str:
    w = unicodedata.normalize("NFKC", word)
    w = "".join(ch for ch in w if ch.isalpha())
    return w.lower()

def parse_line(line: str):
    s = normalize_text(line).strip()
    if not s: return None
    m = re.match(rf"^([{EMOJI_GREEN}{EMOJI_YELLOW}{EMOJI_GRAY}\s]{{5,}})\s+([A-Za-z\s]{{3,}})$", s)
    if m:
        tiles_raw, word_raw = m.group(1), m.group(2)
        tiles = [ch for ch in tiles_raw if ch in (EMOJI_GREEN, EMOJI_YELLOW, EMOJI_GRAY)]
        if len(tiles) != 5:
            return None
        fb = "".join("G" if ch == EMOJI_GREEN else "Y" if ch == EMOJI_YELLOW else "B" for ch in tiles)
        word = strip_to_ascii_letters(word_raw)
        if len(word) != 5:
            return None
        return (word, fb)
    toks = s.split()
    if len(toks) >= 2:
        patt, last = toks, toks[-1]
        if len(patt) == 5 and set(patt.upper()).issubset(VALID_FB):
            w = strip_to_ascii_letters(last)
            if len(w) == 5:
                return (w, patt.upper())
    if len(toks) >= 6:
        flags = [t.upper() for t in toks[:5]]
        if all(t in VALID_FB for t in flags):
            w = strip_to_ascii_letters(toks)
            if len(w) == 5:
                return (w, "".join(flags))
    return None

def extract_guess_pairs_from_text(text: str):
    pairs = []
    for raw in text.splitlines():
        p = parse_line(raw)
        if p:
            pairs.append(p)
    if not pairs:
        raise ValueError("No valid guess lines found. Use emojis+word, 'GYBBY WORD', or 'G Y B B Y WORD'.")
    return pairs

def accumulate_constraints(guesses: List[Tuple[str, str]]):
    greens: Dict[int, str] = {}
    yellows_not_pos: Dict[str, set] = defaultdict(set)
    global_min = Counter()
    global_max_known: Dict[str, int] = {}

    for word, fb in guesses:
        gy_counts = Counter()
        for i, (ch, fl) in enumerate(zip(word, fb)):
            if fl == "G":
                greens[i] = ch
                gy_counts[ch] += 1
            elif fl == "Y":
                yellows_not_pos[ch].add(i)
                gy_counts[ch] += 1
                # ✅ Yellow ka matlab: letter must exist somewhere
                if global_min[ch] < gy_counts[ch]:
                    global_min[ch] = gy_counts[ch]

        # per-guess maximums (gray handling with duplicates)
        per_guess_max = {}
        gc = Counter(word)
        for l, k in gc.items():
            r = gy_counts[l]
            if r < k:
                per_guess_max[l] = r

        # update global mins
        for l, r in gy_counts.items():
            if r > global_min[l]:
                global_min[l] = r

        # update global max-known
        for l, mx in per_guess_max.items():
            global_max_known[l] = min(global_max_known.get(l, mx), mx)

    return greens, yellows_not_pos, dict(global_min), global_max_known
    
def word_satisfies(word, greens, yellows_not_pos, min_counts, max_counts):
    # ✅ Green exact match
    for i, ch in greens.items():
        if word[i] != ch:
            return False

    # ✅ Yellow must exist, but not at banned positions
    for ch, banned in yellows_not_pos.items():
        if ch not in word:
            return False
        for pos in banned:
            if word[pos] == ch:
                return False

    # ✅ Check minimum counts
    wc = Counter(word)
    for l, m in min_counts.items():
        if wc[l] < m:
            return False

    # ✅ Check maximum counts
    for l, mx in max_counts.items():
        if wc[l] > mx:
            return False

    return True
def positional_frequencies(words: List[str]):
    pos_freq = [Counter() for _ in range(5)]
    global_freq = Counter()
    for w in words:
        for i, ch in enumerate(w):
            pos_freq[i][ch] += 1
            global_freq[ch] += 1
    return pos_freq, global_freq

def intelligent_scores(cands: List[str]):
    pos_freq, global_freq = positional_frequencies(cands)
    vowels = set("aeiou")
    scores = {}
    for w in cands:
        uniq = list(dict.fromkeys(w))
        pos_score = sum(pos_freq[i][ch] for i, ch in enumerate(w))
        cov_score = sum(global_freq[ch] for ch in uniq)
        vowel_bonus = sum(1 for ch in uniq if ch in vowels) * 50
        dup_pen = (len(w) - len(uniq)) * 100
        scores[w] = pos_score + cov_score + vowel_bonus - dup_pen
    return scores

def allowed_letters_by_position(greens, yellows_not_pos, min_counts=None, max_counts=None):
    alphabet = set("abcdefghijklmnopqrstuvwxyz")
    allowed = [set(alphabet) for _ in range(5)]
    for i, ch in greens.items():
        allowed[i] = {ch}
    for ch, banned in yellows_not_pos.items():
        for i in banned:
            if i not in greens or greens[i] != ch:
                allowed[i].discard(ch)
    if max_counts:
        for l, mx in max_counts.items():
            if mx == 0:
                for i in range(5):
                    if i not in greens:
                        allowed[i].discard(l)
    return allowed

def green_patterns_lines(greens):
    patt = ["_"] * 5
    for i, ch in greens.items():
        patt[i] = ch
    return [
        "Green pattern: " + " ".join(patt),
        "Green pattern (compact): " + "".join(patt)
    ]

def yellow_patterns_lines(yellows_not_pos):
    lines = []
    for ch, posset in sorted(yellows_not_pos.items()):
        bans = ", ".join(str(i+1) for i in sorted(posset)) if posset else "-"
        lines.append(f"Yellow {ch.upper()}: not at {bans}")
    return lines if lines else ["Yellow pattern: —"]

class WordleSolver:
    def __init__(self, words: List[str]):
        self.words = [w for w in words if len(w)==5 and w.isalpha() and w.islower()]

    @staticmethod
    def sanitize_word_list(raw_lines: List[str]) -> List[str]:
        out = []
        for ln in raw_lines:
            s = strip_to_ascii_letters(ln)
            if len(s) == 5:
                out.append(s)
        return out

    @classmethod
    def from_file(cls, path: str):
        with open(path, "r", encoding="utf-8") as f:
            raw = [ln.strip() for ln in f if ln.strip()]
        words = cls.sanitize_word_list(raw)
        return cls(words)

    def solve(self, guesses: List[Tuple[str, str]]):
        greens, yellows_not_pos, min_counts, max_counts = accumulate_constraints(guesses)
        cands = [w for w in self.words if word_satisfies(w, greens, yellows_not_pos, min_counts, max_counts)]
        return {
            "greens": greens,
            "yellows_not_pos": yellows_not_pos,
            "min_counts": min_counts,
            "max_counts": max_counts,
            "candidates": cands,
        }

    def rank_words(self, words: List[str]):
        if not words: return []
        scores = intelligent_scores(words)
        items = [(w, scores[w]) for w in words]
        return sorted(items, key=lambda x: (-x, x))

def visualize_guess_line(word: str, fb: str):
    tags = []
    for i, (ch, f) in enumerate(zip(word.upper(), fb), 1):
        tags.append(f"{i}:{ch}({f})")
    return " - " + " ".join(tags)

def build_constraints_report(pairs: List[Tuple[str, str]]):
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
            if i in greens and greens[i] != l: continue
            if l in ynp and i in ynp[l]: continue
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

def build_pattern_string(result) -> str:
    patt = ["_"] * 5
    for i, ch in result["greens"].items():
        patt[i] = ch
    return " ".join(patt)

def deduce_grays_display(pairs: List[Tuple[str, str]]) -> str:
    seen_g_y, seen_b = set(), set()
    for w, fb in pairs:
        for ch, f in zip(w, fb):
            if f in ("G","Y"): seen_g_y.add(ch)
            elif f == "B": seen_b.add(ch)
    grays = sorted([ch for ch in seen_b if ch not in seen_g_y])
    return ", ".join(grays) if grays else "-"
    
