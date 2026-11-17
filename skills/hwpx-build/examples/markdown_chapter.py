#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""markdown_chapter — a book chapter in markdown → .hwpx, via hwpx_build.

A worked example of the library on a real document: a textbook chapter with
headings, tables, figures with captions, code blocks, pull-quotes and a
reference list. Written for a Korean submission with a fixed typographic spec
(A4, 13 pt, 160 % line spacing, 15/25 mm margins).

    python3 markdown_chapter.py chapter.md out.hwpx ["문서 제목"] [template.hwpx]
                                [--page-numbers] [--cover | --no-cover] [--toc]

Without a template it uses ../template/base.hwpx.

A report rather than a chapter — something delivered, with a cover — starts
with a front-matter block:

    ---
    title: AI-X 교과목 개발 결과보고서
    subtitle: 2026학년도 2학기
    org: 생명과학부
    date: 2026. 11.
    ---

With a `title` there (or `--cover` and the title argument), the first page is
a cover: the title, subtitle, organisation and date centred, no heading band.
A first `# ` heading that repeats the title is dropped. `--toc` adds a page
listing the `#` and `##` headings — without page numbers, which only exist
once Hangul lays the document out. `--page-numbers` numbers the pages "- 1 -"
at the foot; the cover and contents pages carry none, and the body starts at 1.

The markdown subset it reads — the same one a matching python-docx builder
reads, so one source produces both formats:

    # 장 제목            → heading 1
    ## 절 제목           → heading 2
    **소제목**           → a bold line on its own
    **표 N. 제목**(주)   → a caption on its own line; the bold need only open it
    | a | b |            → table (a --- separator row is required); <br> in a
                           cell is a line break inside it
    ![](path.png)        → image; the next line starting 그림/표 is its caption,
                           and a 그림 line after a blank line too
    ```                  → code block (fenced)
    > text               → pull-quote; consecutive > lines are one quote
    - item               → bullet
    1. item              → numbered item, hanging indent
    *제목*               → sub-heading inside the reference list
    text[^id]            → footnote reference, in body text or a table cell
    [^id]: text          → its definition, anywhere; indented lines continue it
    everything else      → body paragraph, **bold**, *italic* and `code` kept

A paragraph ends at a blank line or at the start of another block, as in
markdown itself: a source wrapped at 80 columns gives one paragraph per
paragraph, not one per line. A list item takes its continuation lines the
same way, indented or not. Nested lists are not read.

Reference lists set in 10.5 pt with a hanging indent start at a line reading
exactly `**참고문헌**`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from hwpx_build import HwpxBuilder  # noqa: E402

BODY = "함초롬바탕"
MONO = "굴림체"          # the usual fixed-width face present in Hangul installs
INK = "#1F3A5F"         # chapter/section titles
BAND = "#1F3A5F"        # chapter title band
TINT = "#E8EDF3"        # section title wash
PAPER = "#FFFFFF"

SPEC = {"top": 15, "bottom": 15, "left": 25, "right": 25, "header": 15, "footer": 15}

HEADING = re.compile(r"^(#{1,3})\s+(.*)")
IMAGE = re.compile(r"^!\[.*?\]\((.+?)\)")
NUMBERED = re.compile(r"^(\d+)\.\s+(.*)")
CAPTION = re.compile(r"^(\*\*)?(그림|표)\s*\d")
BR = re.compile(r"\s*<br\s*/?>\s*", re.I)
FOOTNOTE_DEF = re.compile(r"^\[\^([^\]\s]+)\]:\s*(.*)")


def take_footnotes(lines):
    """Remove `[^id]: text` definitions, returning (other lines, {id: text}).

    A definition continues on following indented lines. Markdown puts the
    definitions at the end, so they are pulled out before the body is read.
    """
    rest, defs, i = [], {}, 0
    while i < len(lines):
        m = FOOTNOTE_DEF.match(lines[i])
        if not m:
            rest.append(lines[i])
            i += 1
            continue
        buf = [m.group(2).strip()]
        i += 1
        while i < len(lines) and lines[i].startswith(("    ", "\t")) and lines[i].strip():
            buf.append(lines[i].strip())
            i += 1
        defs[m.group(1)] = " ".join(buf)
    return rest, defs


def is_table_head(lines, i):
    """A `|` line followed by a separator row (`|---|:--:|`).

    The separator must actually hold a `-`: an empty line is a subset of
    "-: " too, and testing only the subset turned a stray `|` line followed
    by a blank line into a one-row table.
    """
    if not lines[i].strip().startswith("|") or i + 1 >= len(lines):
        return False
    sep = lines[i + 1].strip()
    return "-" in sep and "|" in sep and set(sep) <= set("|-: ")


def starts_block(lines, i):
    """True if line i opens a block, so it cannot continue the one before."""
    s = lines[i].strip()
    return bool(not s or HEADING.match(s) or IMAGE.match(s) or s.startswith(
        ("```", ">", "- ")) or NUMBERED.match(s) or is_table_head(lines, i))


def gather(lines, i, first):
    """`first` plus every following line up to a blank line or a new block.

    Returns (joined text, index of the next unread line). Markdown joins a
    single line break into a space, so a hard-wrapped paragraph comes back as
    one string.
    """
    buf = [first.strip()]
    i += 1
    while i < len(lines) and not starts_block(lines, i):
        buf.append(lines[i].strip())
        i += 1
    return " ".join(x for x in buf if x), i


def front_matter(lines):
    """`---` / key: value / `---` at the very top → (meta dict, remaining lines)."""
    if not lines or lines[0].strip() != "---":
        return {}, lines
    for end in range(1, len(lines)):
        if lines[end].strip() in ("---", "..."):
            meta = {}
            for ln in lines[1:end]:
                k, sep, v = ln.partition(":")
                if sep and k.strip():
                    meta[k.strip().lower()] = v.strip().strip('"\'')
            return meta, lines[end + 1:]
    return {}, lines                      # no closing fence: not front matter


def headings(lines):
    """(level, text) of every # and ## outside fenced code."""
    out, fenced = [], False
    for ln in lines:
        t = ln.strip()
        if t.startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"^(#{1,2})\s+(.*)", t)
        if m:
            out.append((len(m.group(1)), m.group(2).strip()))
    return out


def cover_page(b, meta, face, numbered):
    """Title, subtitle, organisation and date, centred, on a page of their own."""
    if numbered:
        b.hide_page_number()
    b.para(meta["title"], size=26.0, bold=True, color=INK, face=face, align="CENTER",
           prev_pt=190, next_pt=10, line_spacing=140)
    if meta.get("subtitle"):
        b.para(meta["subtitle"], size=16.0, color=INK, face=face, align="CENTER",
               next_pt=6)
    tail = [meta[k] for k in ("org", "organization", "author", "date") if meta.get(k)]
    for n, t in enumerate(tail):
        b.para(t, size=15.0, face=face, align="CENTER",
               prev_pt=230 if n == 0 else 0, next_pt=6)


def contents_page(b, heads, face, numbered):
    """The # and ## headings, indented by level. No page numbers: they do not
    exist until Hangul lays the document out."""
    b.page_break()
    if numbered:
        b.hide_page_number()
    b.para("차    례", size=18.0, bold=True, color=INK, face=face, align="CENTER",
           prev_pt=20, next_pt=24)
    for lvl, text in heads:
        if lvl == 1:
            b.para(text, size=13.0, bold=True, face=face, align="LEFT",
                   prev_pt=8, next_pt=2)
        else:
            b.para(text, size=12.0, face=face, align="LEFT", left_mm=8, next_pt=2)


def convert(md_path, out_path, title=None, template=None, page_numbers=False,
            cover=None, toc=False):
    """`cover` None means "if the front matter has a title". `page_numbers` is
    True, or a dict passed to page_number() (pos, fmt, side_char, start)."""
    md = Path(md_path).read_text(encoding="utf-8")
    base = Path(md_path).resolve().parent

    b = HwpxBuilder(template)
    b.page(margins_mm=SPEC)
    body_face = BODY if BODY in b.fonts() else None
    mono_face = MONO if MONO in b.fonts() else None

    meta, src = front_matter(md.split("\n"))
    if cover is None:                     # a title in the front matter asks for one
        cover = bool(meta.get("title"))
    if title and not meta.get("title"):
        meta["title"] = title
    if cover and not meta.get("title"):
        raise ValueError("a cover needs a title: front matter `title:` or the title argument")
    lines, notes = take_footnotes(src)
    if cover:                             # a first heading repeating the title goes
        for k, ln in enumerate(lines):
            if ln.strip():
                m = HEADING.match(ln.strip())
                if m and len(m.group(1)) == 1 and m.group(2).strip() == meta["title"]:
                    del lines[k]
                break
    if notes:
        b.footnotes(notes, size=9.5, face=body_face)

    numbered = bool(page_numbers)
    if numbered:
        b.page_number(**(page_numbers if isinstance(page_numbers, dict) else {}))
    if cover:
        cover_page(b, meta, body_face, numbered)
    if toc:
        contents_page(b, headings(lines), body_face, numbered)
    if cover or toc:
        b.page_break()                    # the body starts on a page of its own
        if numbered:
            b.restart_page_number(1)
    i, in_refs = 0, False
    while i < len(lines):
        raw, line = lines[i], lines[i].strip()
        if not line:
            i += 1
            continue

        m = HEADING.match(line)
        if m:
            lvl, text = len(m.group(1)), m.group(2)
            # border_offsets is the box's INNER margin — raising it only makes
            # the box fatter. The gap to the body is the OUTER margin,
            # prev_pt/next_pt. Keep the inside tight and buy the separation
            # outside.
            if lvl == 1:                       # chapter — white on a solid band
                b.para(text, size=17.0, bold=True, color=PAPER, face=body_face,
                       align="LEFT", left_mm=3, prev_pt=12, next_pt=20,
                       keep_with_next=True, fill=b.borderfill(face_color=BAND),
                       border_offsets=(340, 340, 150, 150))
            elif lvl == 2:                     # section — wash plus a rule under
                b.para(text, size=14.0, bold=True, color=INK, face=body_face,
                       align="LEFT", left_mm=2, prev_pt=22, next_pt=16,
                       keep_with_next=True,
                       fill=b.borderfill(face_color=TINT, edges=("bottom",),
                                         width="0.4 mm", color=INK),
                       border_offsets=(283, 283, 85, 85))
            else:
                b.para(text, size=13.0, bold=True, color=INK, face=body_face,
                       align="LEFT", prev_pt=10, next_pt=3, keep_with_next=True)
            i += 1
            continue

        m = IMAGE.match(line)
        if m:
            p = Path(m.group(1))
            # A markdown file may hold paths relative to itself or to the
            # directory the build runs from. Try both before giving up, so a
            # missing figure is a missing figure and not a path convention.
            if not p.is_absolute() and not p.exists():
                p = base / p if (base / p).exists() else p
            b.image(p, width_mm=120)
            i += 1
            # Markdown usually puts a blank line between a figure and its
            # caption. Look past blank lines — but after a gap take only a
            # 그림 caption: a "표 N." there is the caption ABOVE the next table.
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            c = CAPTION.match(lines[j].strip()) if j < len(lines) else None
            if c and j > i and c.group(2) != "그림":
                c = None
            if c:
                text, i = gather(lines, j, lines[j])
                # A bare "그림 3-1. 제목" is a label and is centred. One that
                # opens in bold — "**그림 1. 제목.** and then a legend" — is a
                # legend, and a centred legend of several lines is unreadable.
                b.para(text, size=10.0, face=body_face,
                       align="JUSTIFY" if c.group(1) else "CENTER",
                       line_spacing=135 if c.group(1) else 160, next_pt=10)
            continue

        if line.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i].rstrip())
                i += 1
            i += 1
            b.code(buf, face=mono_face)
            continue

        if is_table_head(lines, i):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                # <br> is how GFM and pandoc put a line break in a pipe cell;
                # table() makes each line its own paragraph in the cell.
                cells = [BR.sub("\n", c.strip())
                         for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            b.table(rows, face=body_face)
            continue

        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            b.quote(" ".join(x for x in buf if x), face=body_face)
            continue

        if line.startswith("- "):
            text, i = gather(lines, i, line[2:])
            if in_refs:
                b.para(text, size=10.5, face=body_face, align="LEFT",
                       left_mm=7, first_mm=-5, line_spacing=115, next_pt=2)
            else:
                b.bullet(text, face=body_face)
            continue

        m = NUMBERED.match(line)
        if m:
            text, i = gather(lines, i, m.group(2))
            b.para(f"{m.group(1)}. {text}", face=body_face, left_mm=7,
                   first_mm=-7, next_pt=4)
            continue

        # A caption standing on its own line — usually above a table — whose
        # bold opens the line but need not close it: "**표 3-3. 제목**(행: A)."
        # Testing for a line that is bold end to end missed that form and sent
        # it to the body at 13 pt. The markup is left in, so the bold part
        # stays bold and the note after it does not.
        c = CAPTION.match(line)
        if c and c.group(1):
            text, i = gather(lines, i, line)
            b.para(text, size=11.5, face=body_face, align="LEFT",
                   prev_pt=8, next_pt=3, keep_with_next=True)   # with its table
            continue

        m = re.fullmatch(r"\*\*([^*]+)\*\*", line)
        if m:
            text = m.group(1)
            if text == "참고문헌":
                in_refs = True
                b.para(text, size=14.0, bold=True, color=INK, face=body_face,
                       align="LEFT", left_mm=2, prev_pt=24, next_pt=16,
                       keep_with_next=True,
                       fill=b.borderfill(face_color=TINT, edges=("bottom",),
                                         width="0.4 mm", color=INK),
                       border_offsets=(283, 283, 85, 85))
            else:
                b.para(text, size=12.5, bold=True, color=INK, face=body_face,
                       align="LEFT", left_mm=2, prev_pt=11, next_pt=3,
                       keep_with_next=True,
                       fill=b.borderfill(edges=("left",), width="0.6 mm",
                                         color=INK))
            i += 1
            continue

        m = re.fullmatch(r"\*([^*]+)\*", line)
        if m and in_refs:
            b.para(m.group(1), size=11.0, bold=True, color=INK, face=body_face,
                   align="LEFT", left_mm=2, prev_pt=8, next_pt=3, keep_with_next=True)
            i += 1
            continue

        # Body text. The first line is taken unconditionally: it reached here
        # because no block claimed it, and refusing it too would never advance.
        text, i = gather(lines, i, line)
        b.para(text, face=body_face, next_pt=4)

    b.save(out_path)
    return b


if __name__ == "__main__":
    import argparse
    import json
    from hwpx_build import BASE_TEMPLATE

    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    ap.add_argument("md")
    ap.add_argument("out")
    ap.add_argument("title", nargs="?", help="cover title if the front matter has none")
    ap.add_argument("template", nargs="?", default=str(BASE_TEMPLATE),
                    help="a Hangul-made .hwpx (default: the shipped blank one)")
    ap.add_argument("--page-numbers", action="store_true", help='"- 1 -" at the foot')
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--cover", dest="cover", action="store_true", default=None)
    g.add_argument("--no-cover", dest="cover", action="store_false")
    ap.add_argument("--toc", action="store_true", help="a contents page of # and ## headings")
    a = ap.parse_args()

    for w in HwpxBuilder.check_template(a.template)["warnings"]:
        print("template:", w, file=sys.stderr)
    b = convert(a.md, a.out, a.title, a.template, page_numbers=a.page_numbers,
                cover=a.cover, toc=a.toc)
    if b.font_fallbacks:
        print("font fallbacks:", b.font_fallbacks, file=sys.stderr)
    print(json.dumps(HwpxBuilder.verify(a.out), ensure_ascii=False, indent=1))
