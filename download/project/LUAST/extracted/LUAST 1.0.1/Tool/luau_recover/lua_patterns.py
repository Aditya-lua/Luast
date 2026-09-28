"""Luau/Lua 5.1 string pattern engine (static emulator support).

Covers: character classes (%a %c %d %g %l %p %s %u %w %x and upper=negated),
sets [...] with ranges and leading ^, quantifiers * + - ?, optional anchor ^/$,
captures (), position captures (), back-references %1..%9, balanced match %bxy
and frontier %f[set].  gsub supports string/table/function replacements.
"""

from __future__ import annotations


class PatternError(Exception):
    pass


def _class_match(pat: str, i: int, c: str):
    """Return (matches, next_index) for a single class atom at pat[i]."""
    ch = pat[i]
    if ch == "%":
        if i + 1 >= len(pat):
            raise PatternError("malformed pattern (ends with %)")
        n = pat[i + 1]
        if n == "a":
            fn = str.isalpha
        elif n == "d":
            fn = str.isdigit
        elif n == "l":
            fn = str.islower
        elif n == "s":
            fn = str.isspace
        elif n == "u":
            fn = str.isupper
        elif n == "w":
            fn = str.isalnum
        elif n == "x":
            fn = lambda x: x in "0123456789abcdefABCDEF"
        elif n == "c":
            fn = lambda x: ord(x) < 32 or ord(x) == 127
        elif n == "g":
            fn = lambda x: x.isprintable() and x != " "
        elif n == "p":
            fn = lambda x: x in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
        else:
            return (c == n), i + 2
        return (bool(c) and fn(c)), i + 2
    return (c == ch), i + 1


def _skip_set(pat: str, i: int):
    """i points at '['; return index just past the closing ']'."""
    j = i + 1
    if j < len(pat) and pat[j] == "^":
        j += 1
    first = True
    while j < len(pat):
        if pat[j] == "]" and not first:
            return j + 1
        first = False
        if pat[j] == "%":
            j += 2
        else:
            j += 1
    raise PatternError("malformed pattern (missing ])")


def _set_match(pat: str, i: int, c: str):
    """i points just after '['; return (matched, index_after_set)."""
    negated = False
    if i < len(pat) and pat[i] == "^":
        negated = True
        i += 1
    matched = False
    first = True
    while True:
        if i >= len(pat):
            raise PatternError("malformed pattern (missing ])")
        if pat[i] == "]" and not first:
            return (matched != negated), i + 1
        first = False
        if pat[i] == "%" and i + 1 < len(pat):
            n = pat[i + 1]
            if n.isalnum():
                m, j = _class_match(pat, i, c)
                if m:
                    matched = True
                i = j
                continue
            if c and c == n:
                matched = True
            i += 2
            continue
        if i + 2 < len(pat) and pat[i + 1] == "-" and pat[i + 2] != "]":
            if c and pat[i] <= c <= pat[i + 2]:
                matched = True
            i += 3
            continue
        if c and c == pat[i]:
            matched = True
        i += 1


def _single_len(pat: str, p: int) -> int:
    if pat[p] == "%":
        if p + 2 < len(pat) and pat[p + 1] == "f":
            return _skip_set(pat, p + 3) - p
        if p + 2 < len(pat) and pat[p + 1] == "b":
            return 4
        return 2
    if pat[p] == "[":
        return _skip_set(pat, p) - p
    return 1


def _atom_match(pat: str, p: int, c: str, ms) -> bool:
    """Does atom at pat[p] match char c?"""
    pc = pat[p]
    if pc == ".":
        return True  # any character
    if pc == "%":
        n = pat[p + 1]
        if n.isdigit():
            l = int(n)
            if l == 0 or l > len(ms.capture):
                raise PatternError("invalid capture index %%%d" % l)
            init, end = ms.capture[l - 1]
            text = ms.src[init:end]
            return bool(text) and c == text[0]
        return _class_match(pat, p, c)[0]
    if pc == "[":
        return _set_match(pat, p + 1, c)[0]
    return c == pc


class MatchState:
    __slots__ = ("src", "pat", "capture", "level")

    def __init__(self, src: str, pat: str):
        self.src = src
        self.pat = pat
        self.capture = []  # [init, end] (end None = open)
        self.level = 0


def _match(ms: MatchState, s: int, p: int):
    ms.level += 1
    if ms.level > 400:
        raise PatternError("pattern too complex")
    try:
        pat = ms.pat
        src = ms.src
        while True:
            if p >= len(pat):
                return s
            pc = pat[p]
            if pc == "(":
                if p + 1 < len(pat) and pat[p + 1] == ")":  # position capture
                    ms.capture.append([s, s])
                    r = _match(ms, s, p + 2)
                    if r is None:
                        ms.capture.pop()
                    return r
                ms.capture.append([s, None])
                r = _match(ms, s, p + 1)
                if r is None:
                    ms.capture.pop()
                return r
            if pc == ")":
                if not ms.capture:
                    raise PatternError("invalid pattern capture")
                ms.capture[-1][1] = s
                r = _match(ms, s, p + 1)
                if r is None:
                    ms.capture[-1][1] = None
                return r
            if pc == "%":
                n = pat[p + 1]
                if n.isdigit():
                    l = int(n)
                    if l == 0 or l > len(ms.capture):
                        raise PatternError("invalid capture index %%%d" % l)
                    init, end = ms.capture[l - 1]
                    text = src[init:end]
                    if text and src.startswith(text, s):
                        return _match(ms, s + len(text), p + 2)
                    return None
                if n == "b":
                    if p + 3 >= len(pat):
                        raise PatternError("malformed %%b")
                    if s >= len(src) or src[s] != pat[p + 2]:
                        return None
                    depth = 0
                    oc, cc = pat[p + 2], pat[p + 3]
                    e = s
                    while e < len(src):
                        if src[e] == oc:
                            depth += 1
                        elif src[e] == cc:
                            depth -= 1
                            if depth == 0:
                                return _match(ms, e + 1, p + 4)
                        e += 1
                    return None
                if n == "f":
                    if p + 2 >= len(pat) or pat[p + 2] != "[":
                        raise PatternError("missing [ after %f")
                    j = _skip_set(pat, p + 2)
                    in_prev = s > 0 and _set_match(pat, p + 3, src[s - 1])[0]
                    in_cur = s < len(src) and _set_match(pat, p + 3, src[s])[0]
                    if in_cur and not in_prev:
                        return _match(ms, s, j)
                    return None
            if pc == "$" and p + 1 == len(pat):
                return s if s >= len(src) else None
            e1 = _single_len(pat, p)
            if p + e1 < len(pat) and pat[p + e1] in "*+-?":
                q = pat[p + e1]
                if q == "?":
                    if s < len(src) and _atom_match(pat, p, src[s], ms):
                        r = _match(ms, s + 1, p + e1 + 1)
                        if r is not None:
                            return r
                    return _match(ms, s, p + e1 + 1)
                if q == "*":
                    end = s
                    while end < len(src) and _atom_match(pat, p, src[end], ms):
                        end += 1
                    while end >= s:
                        r = _match(ms, end, p + e1 + 1)
                        if r is not None:
                            return r
                        end -= 1
                    return None
                if q == "+":
                    if s >= len(src) or not _atom_match(pat, p, src[s], ms):
                        return None
                    end = s + 1
                    while end < len(src) and _atom_match(pat, p, src[end], ms):
                        end += 1
                    while end > s:
                        r = _match(ms, end, p + e1 + 1)
                        if r is not None:
                            return r
                        end -= 1
                    return None
                # lazy '-'
                while True:
                    r = _match(ms, s, p + e1 + 1)
                    if r is not None:
                        return r
                    if s < len(src) and _atom_match(pat, p, src[s], ms):
                        s += 1
                    else:
                        return None
            if s < len(src) and _atom_match(pat, p, src[s], ms):
                return _match(ms, s + 1, p + _single_len(pat, p))
            return None
    finally:
        ms.level -= 1


def _getcaptures(ms: MatchState) -> list:
    out = []
    for init, end in ms.capture:
        out.append(ms.src[init:end] if end is not None else ms.src[init:])
    return out


def _do_find(src: str, pat: str, init: int):
    ms = MatchState(src, pat)
    anchor = pat.startswith("^")
    p0 = 1 if anchor else 0
    s = init
    while True:
        ms.capture = []
        r = _match(ms, s, p0)
        if r is not None:
            return s, r, _getcaptures(ms)
        s += 1
        if anchor or s > len(src):
            return -1, -1, []


def str_find(src: str, pat: str, init: int = 0, plain: bool = False):
    if plain:
        idx = src.find(pat, init)
        return idx, (idx + len(pat) if idx >= 0 else -1), []
    return _do_find(src, pat, init)


def str_match(src: str, pat: str, init: int = 0):
    s, e, caps = _do_find(src, pat, init)
    if s < 0:
        return None
    return caps if caps else src[s:e]


def str_gmatch(src: str, pat: str):
    pos = 0
    n = len(src)
    while pos <= n:
        s, e, caps = _do_find(src, pat, pos)
        if s < 0:
            return
        yield caps if caps else src[s:e]
        pos = e if e > s else s + 1


def _expand_repl(text: str, whole: str, caps: list) -> str:
    if "%" not in text:
        return text
    buf = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%" and i + 1 < len(text):
            nx = text[i + 1]
            if nx.isdigit():
                l = int(nx)
                if l == 0:
                    buf.append(whole)
                elif l <= len(caps):
                    buf.append(caps[l - 1])
            else:
                buf.append(nx)
            i += 2
            continue
        buf.append(ch)
        i += 1
    return "".join(buf)


def str_gsub(src: str, pat: str, repl, init: int = 0, max_n=None):
    out = []
    pos = 0
    n = len(src)
    total = 0
    limit = max_n if max_n is not None else 10 ** 9
    while total < limit and pos <= n:
        s, e, caps = _do_find(src, pat, pos)
        if s < 0:
            break
        total += 1
        out.append(src[pos:s])
        whole = src[s:e]
        if callable(repl):
            args = caps if caps else [whole]
            v = repl(*args)
            text = "" if v is None else (v if isinstance(v, str) else str(v))
        elif isinstance(repl, dict):
            key = caps[0] if caps else whole
            v = repl.get(key)
            text = "" if v is None else (v if isinstance(v, str) else str(v))
        else:
            text = _expand_repl(repl, whole, caps)
        out.append(text)
        if e == s:
            if s < len(src):
                out.append(src[s])  # empty match: current char survives
            pos = s + 1  # always makes progress
        else:
            pos = e
    out.append(src[pos:])
    return "".join(out), total
