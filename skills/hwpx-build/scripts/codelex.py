#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""codelex — a small syntax lexer for colouring code in a generated document.

Enough to tell a comment from a string from a keyword in the three languages a
bioinformatics text actually prints: Python, R and shell. Not a parser, and not
trying to be Pygments — the point is that `hwpx-build` stays standard-library
only, so a document generator does not drag in a dependency to put four colours
on a code block.

    from codelex import lex, guess_lang, THEME

    for text, kind in lex("x = 1  # note", "python"):
        print(repr(text), kind, THEME[kind])

`lex()` returns every character of the input exactly once, in order, so the
caller can concatenate the pieces and get the source back. Leading whitespace
and blank lines survive; a run of text that matches no rule comes back as
`"text"`.

Python 3.8+, standard library only.
"""

from __future__ import annotations

import re

__all__ = ["lex", "guess_lang", "THEME", "LANGS"]

#: Token kind → colour. Muted enough to print, distinct enough on screen.
THEME = {
    "comment": "#6A8759",
    "string": "#A3643B",
    "number": "#7A3E9D",
    "keyword": "#1F4E9C",
    "builtin": "#1A7F7A",
    "option": "#8A6D1F",     # --flag in a shell command
    "command": "#1F4E9C",    # the program name at the start of a shell line
    "text": "#000000",
}

_PY_KW = (
    "False None True and as assert async await break class continue def del "
    "elif else except finally for from global if import in is lambda nonlocal "
    "not or pass raise return try while with yield"
).split()
_PY_BUILTIN = (
    "abs all any bool dict enumerate float format int len list map max min "
    "open print range round set sorted str sum tuple type zip self"
).split()
_R_KW = (
    "if else repeat while function for in next break TRUE FALSE NULL Inf NaN "
    "NA NA_integer_ NA_real_ NA_character_ library require return"
).split()
_R_BUILTIN = (
    "c list data.frame matrix factor nrow ncol dim names rownames colnames "
    "apply sapply lapply vapply summary head tail seq rep which sum mean "
    "sd var plot print paste paste0 cbind rbind"
).split()
_SH_KW = (
    "if then else elif fi for while do done case esac function return exit "
    "export source local set unset"
).split()


def _alt(words):
    return r"\b(?:%s)\b" % "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


# Order matters: the first rule that matches at a position wins, so comments and
# strings must come before anything that could match inside them.
_RULES = {
    "python": [
        ("comment", r"#[^\n]*"),
        ("string", r"(?:[rRbBfFuU]{0,2})(?:'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\")"),
        ("number", r"\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b"),
        ("keyword", _alt(_PY_KW)),
        ("builtin", _alt(_PY_BUILTIN)),
    ],
    "r": [
        ("comment", r"#[^\n]*"),
        ("string", r"'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\""),
        ("number", r"\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?[Lk]?\b"),
        ("keyword", _alt(_R_KW) + r"|<-|<<-|%[a-zA-Z|*/]+%"),
        ("builtin", _alt(_R_BUILTIN)),
    ],
    "shell": [
        ("comment", r"#[^\n]*"),
        ("string", r"'[^'\n]*'|\"(?:\\.|[^\"\\\n])*\""),
        # A long or short option, including --opt=value up to the '='.
        ("option", r"(?<![\w-])--?[A-Za-z][\w-]*"),
        ("number", r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])"),
        ("keyword", _alt(_SH_KW)),
    ],
    "plain": [],
}

LANGS = tuple(_RULES)

_SCANNER = {
    lang: re.compile("|".join(f"(?P<{k}>{p})" for k, p in rules)) if rules else None
    for lang, rules in _RULES.items()
}

# The program name. A command sits at the start of a line or straight after a
# pipe or separator — NOT merely at the start of an unmatched chunk, which is
# also where an argument lands once the option before it has been tokenised.
_CMD_HEAD = re.compile(r"[ \t]*([A-Za-z_][\w.\-/]*)")
_CMD_AFTER = re.compile(r"[\n|;&]+[ \t]*([A-Za-z_][\w.\-/]*)")
_CMD_POS = "\n|;&("


def guess_lang(lines) -> str:
    """Pick a language from a code block's first line.

    The convention this follows is a first-line comment naming the language —
    `# python`, `# R` — which is how a printed listing usually says it. A block
    whose first line starts with `#` and is NOT one of those is treated as
    plain: it is a data dump (a VCF header, a table) and colouring it as shell
    would turn the header row into a comment and the data into commands.
    """
    head = next((l for l in lines if l.strip()), "")
    head = head.strip()
    if re.match(r"#\s*python\b", head, re.I):
        return "python"
    if re.match(r"#\s*R\b", head):
        return "r"
    if head.startswith("#"):
        return "plain"
    return "shell"


def lex(text: str, lang: str):
    """Yield (text, kind) covering every character of `text` exactly once."""
    scanner = _SCANNER.get(lang)
    if scanner is None:
        yield text, "text"
        return

    pos = 0
    for m in scanner.finditer(text):
        if m.start() > pos:
            yield from _plain(text[pos:m.start()], lang, text, pos)
        yield m.group(0), m.lastgroup
        pos = m.end()
    if pos < len(text):
        yield from _plain(text[pos:], lang, text, pos)


def _plain(chunk: str, lang: str, full: str, start: int):
    """Unmatched text. For shell, pick out the program names inside it."""
    if lang != "shell" or not chunk:
        yield chunk, "text"
        return

    before = full[:start].rstrip(" \t")
    at_cmd = (not before) or before[-1] in _CMD_POS

    pos = 0
    if at_cmd:
        m = _CMD_HEAD.match(chunk)
        if m:
            if m.start(1):
                yield chunk[:m.start(1)], "text"
            yield m.group(1), "command"
            pos = m.end(1)
    for m in _CMD_AFTER.finditer(chunk, pos):
        if m.start() > pos:
            yield chunk[pos:m.start()], "text"
        yield chunk[m.start():m.start(1)], "text"   # the separator and spacing
        yield m.group(1), "command"
        pos = m.end(1)
    if pos < len(chunk):
        yield chunk[pos:], "text"


if __name__ == "__main__":
    import sys
    src = sys.stdin.read()
    lang = sys.argv[1] if len(sys.argv) > 1 else guess_lang(src.split("\n"))
    out = []
    for t, k in lex(src, lang):
        out.append(t)
        if k != "text":
            print(f"{k:8s} {t!r}")
    assert "".join(out) == src, "lex() lost or duplicated characters"
    print(f"--- {lang}: round-trip ok ({len(src)} chars)", file=sys.stderr)
