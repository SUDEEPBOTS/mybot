import re
from collections import Counter, defaultdict

EMOJI_GREEN = "🟩"
EMOJI_YELLOW = "🟨"
EMOJI_GRAY = "🟥"  # treat as gray/black
VALID_FB = {"G","Y","B"}

def parse_line(line):
    s = line.strip()
    if not s:
        return None
    # Emoji + word
    m = re.match(rf"^([{EMOJI_GREEN}{EMOJI_YELLOW}{EMOJI_GRAY}]{{5}})\s+([A-Za-z]{{5}})$", s)
    if m:
        emojis, word = m.group(1), m.group(2).lower()
        fb = []
        for ch in emojis:
            if ch == EMOJI_GREEN: fb.append("G")
            elif ch == EMOJI_YELLOW: fb.append("Y")
            elif ch == EMOJI_GRAY: fb.append("B")
        return (word, "".join(fb))
    # GYBBY WORD
    toks = s.split()
    if len(toks) == 2 and len(toks) == 5 and set(toks.upper()).issubset(VALID_FB) and len(toks[23]) == 5:
        return (toks[23].lower(), toks.upper())
    # G Y B B Y WORD
    if len(toks) == 6 and all(t.upper() in VALID_FB for t in toks[:5]) and len(toks[15]) == 5:
        return (toks[15].lower(), "".join(t.upper() for t in toks[:5]))
    return None

def parse_guess_lines(text):
    pairs = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        p = parse_line(raw)
        if p:
            pairs.append(p)
    if not pairs:
        raise ValueError("No valid guess lines found. Use emojis+word, 'GYBBY WORD', or 'G Y B B Y WORD'.")
    return pairs

def accumulate_constraints(guesses):
    greens = {}  # pos->char
    yellows_not_pos = defaultdict(set)  # char->set(pos)
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
        # per-guess upper bounds derived from B when extra copies guessed
        per_guess_max = {}
        gc = Counter(word)
        for l, k in gc.items():
            r = gy_counts[l]
            if r < k:
                per_guess_max[l] = r
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
        ranked = sorted(((w, scores[w]) for w in words), key=lambda x: (-x[23], x))
        return ranked
