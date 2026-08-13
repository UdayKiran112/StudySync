"""
Book-title and book-id canonicalization for the Offline Library catalog.

Three jobs, shared by the DB-cleanup CLI (clean_offline_catalog.py) and the
offline library loader (load_offline_library.py):

  clean_title(raw, ...)   -- collapse spelling variants of the same real book
                             onto one official title: a curated alias table
                             first (bridges abbreviations/typos edit-distance
                             can't), then an optional common.Canonicalizer for
                             the generic exact/anagram/fuzzy tiers. Input is
                             Unicode-NFKC normalized so full-width digits,
                             curly quotes etc. collapse before matching.
  plan_title_merges(...)  -- a second, whole-corpus pass over titles that the
                             online Canonicalizer can't safely bridge on its
                             own: word-order swaps ("Physics NCERT" vs
                             "NCERT Physics"), significant misspellings caught
                             by character-bigram overlap (e.g. "Quantative
                             Aptitude"), and phonetic-only near-homophones
                             (Metaphone). High-confidence spelling matches are
                             auto-merged; everything else is reviewed.
  plan_id_merges(...)     -- group book_ids by cleaned title and decide which
                             near-duplicate IDs merge onto the most-used ID
                             (e.g. '680' -> '1680', '1860' -> '1680', or a
                             pasted-junk '16791418' -> '1679'), while
                             genuinely distinct catalog IDs stay separate.

Merging is deliberately conservative (tiered): a variant ID is auto-merged
only when it is BOTH rare (few usage rows, and a tiny share of the canonical
ID's rows) AND near-identical to the canonical ID (edit distance <= 2, a
leading-zero variant, or the canonical ID appearing as a contiguous substring
with at most a few stray junk digits). Rare IDs whose title matches but whose
digits do not are flagged for review with a *suggested* correction (likely
wrong-ID entries) rather than guessed. Titles follow the same pattern:
spelling-level similarity >= 0.90 auto-merges, 0.78-0.90 or phonetic-only
matches go to review with the proposed spelling.
"""

import difflib
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

common_dir = Path(__file__).parent.parent
if str(common_dir) not in sys.path:
    sys.path.insert(0, str(common_dir))

from common import collapse_ws, normalize_key  # noqa: E402

# --------------------------------------------------------------------------
# curated book-title overrides
# --------------------------------------------------------------------------

# Explicit map of the same real book's messy spellings to one official
# title. Keys are pre-normalized with normalize_key. Only unambiguous
# variants belong here: ambiguous short forms (e.g. 'G S', which could be
# General Science OR General Studies) are deliberately NOT aliased -- they
# stay their own title rather than being guessed into the wrong one, the
# same rule the exam-topic alias table follows.
_BOOK_TITLE_ALIAS_GROUPS = {
    "5G Tech": ["5Gtech", "5gtech", "5 G Tech", "5 Gtech"],
    "9th Beehive": ["9th Beehive", "9th Beelive", "9Th Beehive"],
    "Hi-Tech Vijaya Rahasyam": [
        "Hi-Tech Vijaya Rahasyam",
        "Hitech",
        "HITECH",
        "Hiteck",
        "Hitech Vijaya Rahasyam",
    ],
    "General Knowledge": [
        "General Knowledge",
        "GK",
        "G K",
        "G.K",
    ],
    "Science & Technology": [
        "Science & Technology",
        "S&T",
        "S & T",
        "S and T",
    ],
    "General Science": [
        "General Science",
        "G Science",
        "G.Science",
        "G. Science",
        "G.Sci",
    ],
    "General Studies": [
        "General Studies",
        "G Studies",
        "G. Studies",
        "Genaral Studies",
    ],
    "Quantitative Aptitude": [
        "Quantitative Aptitude",
        "Q A",
        "Q.A",
        "Q Aptitude",
        "Q.Aptitude",
    ],
    "ICET": ["ICET", "I CET", "I-CET", "I.CET"],
    "NCERT Physics": ["NCERT Physics", "Physics NCERT", "Phy NCERT"],
    "NCERT Biology": ["NCERT Biology", "Biology NCERT", "Bio NCERT"],
    "Data Interpretation": [
        "Data Interpretation",
        "Datainterpretation",
        "Data Inter.",
        "Data Inter",
    ],
    "IBPS": ["IBPS", "Bank IBPS", "IBPS Bank"],
}

BOOK_TITLE_ALIASES = {
    normalize_key(v): canon
    for canon, variants in _BOOK_TITLE_ALIAS_GROUPS.items()
    for v in variants
}

# Alias keys that ARE applied but should also be surfaced in the review
# ledger so a human can confirm the (research-derived) target spelling.
REVIEW_ALIAS_KEYS = {normalize_key("5Gtech"), normalize_key("G Studies")}


def nfkc_norm(s):
    """Unicode NFKC normalization: full-width digits, curly quotes/apostrophes,
    ligatures and other visually-identical but byte-different characters all
    collapse to their ASCII forms before any matching."""
    return unicodedata.normalize("NFKC", s or "")


def clean_title(raw, canon=None, context="", log_review=None):
    """Canonicalize one book title. Alias table first; if `canon` (a
    common.Canonicalizer) is given, unknown titles fall through to its
    exact/anagram/fuzzy tiers. Returns the cleaned title."""
    cleaned = collapse_ws(nfkc_norm(raw)).strip('"').strip()
    if not cleaned:
        return cleaned
    key = normalize_key(cleaned)
    if key in BOOK_TITLE_ALIASES:
        target = BOOK_TITLE_ALIASES[key]
        if key in REVIEW_ALIAS_KEYS and log_review:
            log_review(
                f"{context}{raw!r} -> {target!r} applied from the curated alias "
                f"table (target spelling set by research -- confirm)",
            )
        return target
    if canon is not None:
        return canon.canonicalize(cleaned, context=context)
    return cleaned


# --------------------------------------------------------------------------
# title similarity toolkit (beyond plain edit distance)
# --------------------------------------------------------------------------

# Gates for the whole-corpus title pass (plan_title_merges).
TITLE_MERGE_THRESHOLD = 0.90      # spelling-level match -> auto-merge
TITLE_REVIEW_THRESHOLD = 0.78     # spelling-level match -> review
PHONETIC_REVIEW_THRESHOLD = 0.95  # phonetic keys must be near-equal...
PHONETIC_MIN_KEY_LEN = 6          # ...and long enough to be meaningful
PHONETIC_MIN_SPELLING = 0.45      # ...and the spellings must at least overlap
MAX_TITLE_RARE_COUNT = 8          # variant title must appear in <= this many rows
MAX_TITLE_RARE_RATIO = 0.10       # ...and <= this share of the canonical's rows


def metaphone(word):
    """Lawrence Philips' Metaphone (single-metaphone, public-domain algorithm).
    Returns a coarse phonetic key so that differently-spelled words that sound
    alike collapse to the same key ('chemistry'/'chemestry' -> 'XMSTR')."""
    s = re.sub(r"[^A-Z]", "", nfkc_norm(word).upper())
    if not s:
        return ""
    collapsed = [s[0]]
    for ch in s[1:]:
        if ch == collapsed[-1] and ch != "C":
            continue
        collapsed.append(ch)
    s = "".join(collapsed)
    if s.startswith(("KN", "GN", "PN", "AE", "WR")):
        s = s[1:]
    elif s.startswith("WH"):
        s = "W" + s[2:]
    if s.startswith("X"):
        s = "S" + s[1:]
    result = []
    i, n = 0, len(s)
    vowels = set("AEIOU")
    while i < n:
        c = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        nnxt = s[i + 2] if i + 2 < n else ""
        prev = s[i - 1] if i > 0 else ""
        if c in vowels:
            if i == 0:
                result.append(c)
        elif c == "B":
            if not (i == n - 1 and prev == "M"):
                result.append("B")
        elif c == "C":
            if nxt == "H":
                result.append("X")
            elif nxt in "IEY":
                result.append("S")
            else:
                result.append("K")
        elif c == "D":
            if nxt == "G" and nnxt in "IEY":
                result.append("J")
            else:
                result.append("T")
        elif c == "F":
            result.append("F")
        elif c == "G":
            if nxt == "H" and nnxt:
                pass
            elif nxt == "N" and not nnxt:
                pass
            elif nxt in "IEY":
                result.append("J")
            else:
                result.append("K")
        elif c == "H":
            if i > 0 and nxt in vowels and prev not in vowels:
                result.append("H")
        elif c == "J":
            result.append("J")
        elif c == "K":
            result.append("K")
        elif c == "L":
            result.append("L")
        elif c == "M":
            result.append("M")
        elif c == "N":
            result.append("N")
        elif c == "P":
            result.append("F" if nxt == "H" else "P")
        elif c == "Q":
            result.append("K")
        elif c == "R":
            result.append("R")
        elif c == "S":
            result.append("X" if nxt == "H" else "S")
        elif c == "T":
            if nxt == "H":
                result.append("0")
            elif nxt == "I" and nnxt in "OA":
                result.append("X")
            else:
                result.append("T")
        elif c == "V":
            result.append("F")
        elif c == "W":
            if nxt in vowels or i == 0:
                result.append("W")
        elif c == "X":
            result.append("KS")
        elif c == "Y":
            if nxt in vowels or i == 0:
                result.append("Y")
        elif c == "Z":
            result.append("S")
        i += 1
    # Collapse consecutive duplicate letters so 'science'/'sience' both map
    # to 'SNS' rather than 'SSNS' vs 'SNS'.
    return re.sub(r"(.)\1+", r"\1", "".join(result))


def _norm_tokens(title):
    """NFKC + lowercase + split into alphanumeric tokens (digits survive)."""
    return re.findall(r"[a-z0-9]+", nfkc_norm(title).lower())


def _token_dice(a_tokens, b_tokens):
    if not a_tokens or not b_tokens:
        return 0.0
    ca, cb = Counter(a_tokens), Counter(b_tokens)
    inter = sum((ca & cb).values())
    return 2.0 * inter / (sum(ca.values()) + sum(cb.values()))


def _bigram_dice(a, b):
    """Dice coefficient over character bigrams. Robust to single-letter
    insertions/deletions/substitutions that drag the global edit ratio down
    ('Quantative Aptitude' vs 'Quantitative Aptitude')."""
    ba, bb = set(a[i:i + 2] for i in range(len(a) - 1)), set(b[i:i + 2] for i in range(len(b) - 1))
    if not ba or not bb:
        return 0.0
    return 2.0 * len(ba & bb) / (len(ba) + len(bb))


def _phonetic_sim(a_tokens, b_tokens):
    pa = " ".join(metaphone(t) for t in a_tokens)
    pb = " ".join(metaphone(t) for t in b_tokens)
    if not pa or not pb:
        return 0.0
    if pa == pb:
        return 1.0
    return difflib.SequenceMatcher(None, pa, pb).ratio()


def title_similarity(a, b):
    """Score two titles four ways. Returns a dict with per-method scores plus
    `spelling` (the max of the three spelling-based methods) and `kind`
    (which spelling method won). `phonetic` is deliberately kept separate so
    callers can review rather than trust it, and `phonetic_key_len` lets
    callers reject the short-acronym collisions metaphone can't distinguish
    ('SI' vs 'SSC', 'Gate' vs 'CTET')."""
    a_tokens, b_tokens = _norm_tokens(a), _norm_tokens(b)
    na, nb = "".join(a_tokens), "".join(b_tokens)
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    tok = _token_dice(a_tokens, b_tokens)
    big = _bigram_dice(na, nb)
    pa = " ".join(metaphone(t) for t in a_tokens)
    pb = " ".join(metaphone(t) for t in b_tokens)
    pho = _phonetic_sim(a_tokens, b_tokens)
    methods = {"sequence": seq, "token": tok, "bigram": big}
    kind = max(methods, key=methods.get)
    return {
        **methods,
        "phonetic": pho,
        "phonetic_key_len": len(pa) + len(pb),
        "spelling": max(methods.values()),
        "kind": kind,
    }


def _resolve_title(variant, title_remap):
    """Follow title_remap chains ('A'->'B', 'B'->'C' becomes 'A'->'C')."""
    seen = set()
    while variant in title_remap and variant not in seen:
        seen.add(variant)
        variant = title_remap[variant]
    return variant


def plan_title_merges(title_totals):
    """Given {cleaned_title: usage_rows}, propose collapsing rare titles that
    are near-duplicates of a more common one (word-order swaps, significant
    misspellings, phonetic homophones) onto the frequent title.

    Titles with spelling-level similarity >= TITLE_MERGE_THRESHOLD are
    auto-merge proposals; 0.78-0.90 or phonetic-only matches are returned as
    review items carrying the proposed spelling. Only rare titles (few rows,
    small share of the canonical's rows) are ever considered, so genuinely
    distinct frequent series stay separate.

    Returns (merges, reviews): each item is a dict with 'variant', 'canonical',
    counts, 'score' and 'kind'.
    """
    merges, reviews = [], []
    titles = sorted(title_totals.items(), key=lambda kv: (-kv[1], kv[0]))
    for i, (variant, vcount) in enumerate(titles):
        best = None
        # look at more-frequent titles only; stop when they'd be rarer
        for j in range(i - 1, -1, -1):
            canon, ccount = titles[j]
            if vcount > MAX_TITLE_RARE_COUNT and vcount > ccount * MAX_TITLE_RARE_RATIO:
                break
            s = title_similarity(variant, canon)
            phonetic_ok = (
                s["phonetic"] >= PHONETIC_REVIEW_THRESHOLD
                and s["phonetic_key_len"] >= PHONETIC_MIN_KEY_LEN
                and s["spelling"] >= PHONETIC_MIN_SPELLING
            )
            candidate = None
            if s["spelling"] >= TITLE_REVIEW_THRESHOLD or phonetic_ok:
                candidate = (s, canon)
            if candidate and (best is None or candidate[0]["spelling"] > best[0]["spelling"]):
                best = candidate
        if best is None:
            continue
        s, canon = best
        common = {
            "variant": variant,
            "canonical": canon,
            "variant_count": vcount,
            "canonical_count": title_totals[canon],
            "score": round(s["spelling"], 3),
            "kind": s["kind"],
        }
        if s["spelling"] >= TITLE_MERGE_THRESHOLD:
            merges.append(common)
        else:
            common["phonetic_score"] = round(s["phonetic"], 3)
            reviews.append(common)
    return merges, reviews


# --------------------------------------------------------------------------
# near-duplicate book-id detection
# --------------------------------------------------------------------------

# Gates for auto-merging a variant book_id onto the title's most-used id.
MAX_RARE_COUNT = 5          # variant must appear in at most this many rows
MAX_RARE_RATIO = 0.05       # ...and in at most this share of the canonical's rows
MAX_JUNK_DIGITS = 3         # legacy leading/trailing-junk gate (ids_equivalent)
MAX_ID_LEV = 2              # relaxed gate: edit distance at most this (rare IDs only)
MAX_CONFIRMED_JUNK = 6      # relaxed gate: canonical as substring, junk <= this

# A rare variant ID whose digits are dissimilar but whose title is exact is a
# strong wrong-ID signal; these are REVIEWED (with a proposed correction),
# never auto-merged, because genuinely distinct copies share titles.
SUGGEST_WHEN_RARE = MAX_RARE_COUNT + 2


def damerau_levenshtein(a, b):
    """Optimal-string-alignment (restricted Damerau) edit distance."""
    len_a, len_b = len(a), len(b)
    d = [[0] * (len_b + 1) for _ in range(len_a + 1)]
    for i in range(len_a + 1):
        d[i][0] = i
    for j in range(len_b + 1):
        d[0][j] = j
    for i in range(1, len_a + 1):
        for j in range(1, len_b + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if (
                i > 1
                and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len_a][len_b]


def _id_parts(book_id):
    """Split a book_id into its leading non-digit prefix and the rest, so
    'P-247' -> ('p', '247') while '247' -> ('', '247'). The prefix guards
    the P-family boundary: an ID with a letter prefix is never merged into
    a bare number, even when the digit part would match. NFKC-normalized so
    full-width digits ('１６８０') compare as plain digits."""
    nfkc = nfkc_norm(book_id)
    m = re.match(r"^(\D*)(.*)$", nfkc)
    prefix, rest = m.group(1), m.group(2)
    return re.sub(r"[^A-Za-z]", "", prefix).lower(), rest


def _rests_equivalent(a, b):
    """Legacy strict check: identical, leading-zero equivalent, one being the
    other plus at most MAX_JUNK_DIGITS trailing junk digits, or edit
    distance <= 1."""
    if a == b:
        return True
    za, zb = a.lstrip("0") or a, b.lstrip("0") or b
    if za == zb:
        return True
    if za.startswith(zb) and 0 < len(za) - len(zb) <= MAX_JUNK_DIGITS:
        return True
    if zb.startswith(za) and 0 < len(zb) - len(za) <= MAX_JUNK_DIGITS:
        return True
    return damerau_levenshtein(a, b) <= 1


def _rests_near_equivalent(a, b):
    """Relaxed check used for rare IDs: the legacy rules, OR edit distance
    <= MAX_ID_LEV, OR one digit-run appearing as a contiguous substring of the
    other with at most MAX_CONFIRMED_JUNK stray digits (catches pasted-ID
    typos like '16791418' containing '1679', or a doubled key-stroke)."""
    if _rests_equivalent(a, b):
        return True
    za, zb = a.lstrip("0") or a, b.lstrip("0") or b
    if len(za) >= len(zb) and zb in za and len(za) - len(zb) <= MAX_CONFIRMED_JUNK:
        return True
    if len(zb) > len(za) and za in zb and len(zb) - len(za) <= MAX_CONFIRMED_JUNK:
        return True
    return damerau_levenshtein(a, b) <= MAX_ID_LEV


def ids_equivalent(candidate, canonical):
    """True when `candidate` is a near-duplicate of `canonical` (same
    P-family prefix, then matching digits per the LEGACY strict check)."""
    if candidate == canonical:
        return True
    c_prefix, c_rest = _id_parts(candidate)
    k_prefix, k_rest = _id_parts(canonical)
    if (c_prefix == "") != (k_prefix == ""):
        return False
    if c_prefix != k_prefix:
        return False
    return _rests_equivalent(c_rest, k_rest)


def ids_near_equivalent(candidate, canonical):
    """Relaxed variant of ids_equivalent used by plan_id_merges (see
    _rests_near_equivalent)."""
    if candidate == canonical:
        return True
    c_prefix, c_rest = _id_parts(candidate)
    k_prefix, k_rest = _id_parts(canonical)
    if (c_prefix == "") != (k_prefix == ""):
        return False
    if c_prefix != k_prefix:
        return False
    return _rests_near_equivalent(c_rest, k_rest)


def _prefix_compatible(candidate, canonical):
    """Hard boundary before any merge: an ID with a letter prefix belongs
    to a different physical series (e.g. 'P-247') than a bare number
    ('247'), so they can never be merged even when rare and similar."""
    c_prefix, _ = _id_parts(candidate)
    k_prefix, _ = _id_parts(canonical)
    if (c_prefix == "") != (k_prefix == ""):
        return False
    return c_prefix == k_prefix


def plan_id_merges(title_to_ids):
    """Given {cleaned_title: {book_id: usage_count}}, decide which IDs merge.

    For each title seen under more than one ID, the ID with the most usage
    rows is canonical (tie-break: shorter ID, then lexicographic). Every
    other ID is auto-merged onto it only when it passes, in order:
      1. the P-family boundary (_prefix_compatible) -- a letter-prefixed ID
         never merges into a bare number or a differently-prefixed one;
      2. the rarity gates (few rows, tiny share of the canonical's rows);
      3. near-identical digits (ids_near_equivalent -- edit distance <= 2,
         leading zeros, or substring junk containment).

    Anything else is returned as a review item. Rare variants whose title is
    exact but whose digits are dissimilar carry a `suggest` key naming the
    likely correct ID (wrong-ID-by-title signal) -- a proposal, never a guess.

    Returns (merges, reviews), each a list of dicts describing one decision.
    """
    merges = []
    reviews = []
    for title, ids in sorted(title_to_ids.items()):
        if len(ids) < 2:
            continue
        ranked = sorted(ids.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
        canonical_id, canonical_count = ranked[0]
        for variant_id, variant_count in ranked[1:]:
            common = {
                "title": title,
                "canonical_id": canonical_id,
                "variant_id": variant_id,
                "canonical_count": canonical_count,
                "variant_count": variant_count,
            }
            if not _prefix_compatible(variant_id, canonical_id):
                reviews.append(
                    {
                        **common,
                        "reason": "not_similar",
                        "levenshtein": damerau_levenshtein(
                            variant_id, canonical_id
                        ),
                    }
                )
                continue
            if variant_count > MAX_RARE_COUNT or (
                variant_count > canonical_count * MAX_RARE_RATIO
            ):
                reviews.append({**common, "reason": "not_rare"})
                continue
            if not ids_near_equivalent(variant_id, canonical_id):
                suggestion = (
                    canonical_id
                    if variant_count <= SUGGEST_WHEN_RARE
                    and variant_count <= canonical_count * MAX_RARE_RATIO
                    else None
                )
                reviews.append(
                    {
                        **common,
                        "reason": "wrong_id_by_title" if suggestion else "not_similar",
                        "suggest": suggestion,
                        "levenshtein": damerau_levenshtein(
                            variant_id, canonical_id
                        ),
                    }
                )
                continue
            merges.append(common)
    return merges, reviews
