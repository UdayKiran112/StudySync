#!/usr/bin/env python3
"""
Attendance student-ID correction: verify each attendance row's entered
Student ID against the students roster by ALSO checking the entered Student
Name, and auto-correct the ID only on high-confidence evidence.

WHY
---
Attendance rows in the export are linked purely by Student ID; the Student
Name column is unreliable (initial-compacted spellings, typos). When an
entered ID belongs to a roster student whose name has nothing to do with the
name written on the punch, the row is likely attached to the wrong student.
This module finds the roster student whose name the punch actually matches and
returns their ID.

DECISION RULES (conservative -- never touch a row that is merely uncertain):
  * ID not in roster                  -> no change (caller flags as before)
  * entered name matches the roster
    name of its own ID                -> no change (row is valid)
  * entered name is a plausible
    variant of its own ID's name      -> no change (garbled spelling of the
                                         same student, e.g. 'Psantosh' on the
                                         row of 'Sathoshkumar.Pogiri')
  * one strong name match among the
    whole roster                      -> correct ID to that student
  * several strong name matches and
    exactly one has an entered-ID
    within Damerau distance 1         -> correct ID to that near-ID student
  * anything else                     -> no change (caller flags for review)

Only `strong` name evidence qualifies: the entered name (after stripping up to
3 leading initials) equals a roster token, is a close edit/prefix of it, or is
within edit distance 1 of the whole name. Weak coincidences (a 3-letter
'suffix' like 'rao' shared with an unrelated name) never trigger a correction.
"""

import re
from collections import defaultdict

TITLE_SUFFIXES = {"rao", "raju", "babu", "kumar", "naidu", "amma", "appa", "anna"}


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def damerau(a, b):
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 3:
        return 99
    la, lb = len(a), len(b)
    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        dp[i][0] = i
    for j in range(lb + 1):
        dp[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                dp[i][j] = min(dp[i][j], dp[i - 2][j - 2] + 1)
    return dp[la][lb]


def words_of(original):
    return [w for w in (re.split(r"[^a-z0-9]+", original.lower())) if w]


def fuzzy_prefix(rem, t):
    # rem fuzzy-matches a prefix of t: some prefix P of t with
    # |len(rem)-len(P)| <= 2 and DL(rem, P) <= 2.
    for L in range(4, min(len(t), len(rem) + 2) + 1):
        if abs(len(rem) - L) <= 2 and damerau(rem, t[:L]) <= 2:
            return True
    return False


def related(E, R_orig):
    # Is the entered name a plausible variant of the roster (self) name?
    # True means "same student, garbled spelling" -> KEEP the row untouched.
    toks = [w for w in words_of(R_orig) if len(w) >= 3]
    for k in range(0, min(4, len(E))):
        rem = E[k:]
        if len(rem) < 3:
            continue
        for t in toks:
            if rem in t or t in rem:
                return True
            for i in range(0, len(rem) - 3):
                if rem[i:i + 4] in t:
                    return True
            if fuzzy_prefix(rem, t):
                return True
            if damerau(rem, t) <= 3:
                return True
    return False


def tolerant_match(E, R_norm, R_orig):
    # Broad candidate discovery (may include weak coincidences); candidates
    # are filtered down to strong matches before any correction is made.
    if not E or not R_norm:
        return False
    if E == R_norm:
        return True
    tokens = [w for w in words_of(R_orig) if len(w) >= 3]
    for k in range(0, min(4, len(E))):
        rem = E[k:]
        if len(rem) < 3:
            continue
        if R_norm.startswith(rem):
            return True
        if len(rem) >= 5 and len(R_norm) >= len(rem) and damerau(rem, R_norm[:len(rem)]) <= 1:
            return True
        for t in tokens:
            if t.startswith(rem) or rem.startswith(t):
                return True
            if t.endswith(rem) or rem.endswith(t):
                return True
            if damerau(rem, t) <= 1:
                return True
    return False


def strong_match(E, R_norm, R_orig):
    # High-confidence evidence that the entered name IS this roster name.
    if not E or not R_norm:
        return False
    if E == R_norm:
        return True
    toks = [w for w in words_of(R_orig) if len(w) >= 4]
    for k in range(0, min(4, len(E))):
        rem = E[k:]
        if len(rem) < 4:
            continue
        if R_norm.startswith(rem) and len(R_norm) - len(rem) <= 2:
            return True
        if rem.startswith(R_norm) and len(rem) - len(R_norm) <= 2:
            return True
        if len(rem) >= 5 and len(R_norm) >= len(rem) and damerau(rem, R_norm[:len(rem)]) <= 1:
            return True
        if damerau(rem, R_norm) <= 1:
            return True
        for t in toks:
            if rem == t:
                return True
            if rem.startswith(t) and (len(rem) - len(t) <= 2 or rem[len(t):] in TITLE_SUFFIXES):
                return True
            if t.startswith(rem) and len(t) - len(rem) <= 2:
                return True
            if damerau(rem, t) <= 1:
                return True
    return False


def id_dist(a, b):
    return damerau(str(a), str(b))


def build_index(roster):
    """roster: {student_id: name}. Returns a first-char -> [(norm, id, orig)] index."""
    by_first = defaultdict(list)
    for cid, on in roster.items():
        rn = norm(on)
        if rn:
            by_first[rn[0]].append((rn, cid, on))
    return by_first


def find_candidates(E, by_first):
    out = []
    seen = set()
    for k in range(0, min(4, len(E))):
        rem = E[k:]
        if not rem:
            continue
        for rn, cid, on in by_first.get(rem[0], []):
            if cid in seen:
                continue
            if tolerant_match(E, rn, on):
                seen.add(cid)
                out.append((cid, on))
    return out


def correct(E, sid, roster, by_first):
    """
    Decide the student ID for an attendance row.

    E: normalized entered Student Name; sid: entered Student ID.
    Returns (new_student_id_or_None, reason):
      reason is one of 'valid' | 'variant' | 'notfound' | 'nocand' |
      'ambiguous' (all -> no change) or 'unique' | 'nearid' (-> new ID).
    """
    self_name = roster.get(sid)
    if self_name is None:
        return None, "notfound"
    if tolerant_match(E, norm(self_name), self_name):
        return None, "valid"
    if related(E, self_name):
        return None, "variant"
    cands = set(
        (cid, on, id_dist(sid, cid))
        for cid, on in find_candidates(E, by_first)
        if cid != sid
    )
    strong = [u for u in cands if strong_match(E, norm(u[1]), u[1])]
    if not strong:
        return None, "nocand"
    if len(strong) == 1:
        return strong[0][0], "unique"
    close = [u for u in strong if u[2] <= 1]
    if len(close) == 1:
        return close[0][0], "nearid"
    return None, "ambiguous"
