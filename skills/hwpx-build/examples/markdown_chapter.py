#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""markdown_chapter — a book chapter in markdown → .hwpx, via hwpx_build.

A worked example of the library on a real document: a textbook chapter with
headings, tables, figures with captions, code blocks, pull-quotes and a
reference list. Written for a Korean submission with a fixed typographic spec
(A4, 13 pt, 160 % line spacing, 15/25 mm margins).

    python3 markdown_chapter.py chapter.md out.hwpx "제3장 …" [template.hwpx]

The markdown subset it reads — the same one a matching python-docx builder
reads, so one source produces both formats:

    # 장 제목            → heading 1
    ## 절 제목           → heading 2
    **소제목**           → a bold line on its own
    | a | b |            → table (a --- separator row is required)
    ![](path.png)        → image; a following line starting 그림/표 is its caption
    ```                  → code block (fenced)
    > text               → pull-quote
    - item               → bullet
    *제목*               → sub-heading inside the reference list
    everything else      → body paragraph, **bold** and *italic* preserved

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


def convert(md_path, out_path, title=None, template=None):
    md = Path(md_path).read_text(encoding="utf-8")
    base = Path(md_path).resolve().parent

    b = HwpxBuilder(template)
    b.page(margins_mm=SPEC)
    body_face = BODY if BODY in b.fonts() else None
    mono_face = MONO if MONO in b.fonts() else None

    lines = md.split("\n")
    i, in_refs = 0, False
    while i < len(lines):
        raw, line = lines[i], lines[i].strip()
        if not line:
            i += 1
            continue

        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            lvl, text = len(m.group(1)), m.group(2)
            if lvl == 1:                       # chapter — white on a solid band
                b.para(text, size=17.0, bold=True, color=PAPER, face=body_face,
                       align="LEFT", left_mm=3, prev_pt=6, next_pt=14,
                       fill=b.borderfill(face_color=BAND))
            elif lvl == 2:                     # section — wash plus a rule under
                b.para(text, size=14.0, bold=True, color=INK, face=body_face,
                       align="LEFT", left_mm=2, prev_pt=16, next_pt=6,
                       fill=b.borderfill(face_color=TINT, edges=("bottom",),
                                         width="0.4 mm", color=INK))
            else:
                b.para(text, size=13.0, bold=True, color=INK, face=body_face,
                       align="LEFT", prev_pt=10, next_pt=3)
            i += 1
            continue

        m = re.match(r"^!\[.*?\]\((.+?)\)", line)
        if m:
            p = Path(m.group(1))
            # A markdown file may hold paths relative to itself or to the
            # directory the build runs from. Try both before giving up, so a
            # missing figure is a missing figure and not a path convention.
            if not p.is_absolute() and not p.exists():
                p = base / p if (base / p).exists() else p
            b.image(p, width_mm=120)
            i += 1
            if i < len(lines) and re.match(r"^(그림|표)\s*\d", lines[i].strip()):
                b.para(lines[i].strip(), size=10.0, face=body_face,
                       align="CENTER", next_pt=10)
                i += 1
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

        if line.startswith("|") and i + 1 < len(lines) and \
                set(lines[i + 1].replace("|", "").strip()) <= set("-: "):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            b.table(rows, face=body_face)
            continue

        if line.startswith(">"):
            b.quote(line.lstrip("> ").strip(), face=body_face)
            i += 1
            continue

        if line.startswith("- "):
            if in_refs:
                b.para(line[2:], size=10.5, face=body_face, align="LEFT",
                       left_mm=7, first_mm=-5, line_spacing=115, next_pt=2)
            else:
                b.bullet(line[2:], face=body_face)
            i += 1
            continue

        m = re.fullmatch(r"\*\*(.+?)\*\*", line)
        if m:
            text = m.group(1)
            if text == "참고문헌":
                in_refs = True
                b.para(text, size=14.0, bold=True, color=INK, face=body_face,
                       align="LEFT", left_mm=2, prev_pt=20, next_pt=6,
                       fill=b.borderfill(face_color=TINT, edges=("bottom",),
                                         width="0.4 mm", color=INK))
            elif re.match(r"^(표|그림)\s*\d", text):
                b.para(text, size=11.5, bold=True, face=body_face,
                       align="LEFT", prev_pt=8, next_pt=3)
            else:
                b.para(text, size=12.5, bold=True, color=INK, face=body_face,
                       align="LEFT", left_mm=2, prev_pt=11, next_pt=3,
                       fill=b.borderfill(edges=("left",), width="0.6 mm",
                                         color=INK))
            i += 1
            continue

        m = re.fullmatch(r"\*(.+?)\*", line)
        if m and in_refs:
            b.para(m.group(1), size=11.0, bold=True, color=INK, face=body_face,
                   align="LEFT", left_mm=2, prev_pt=8, next_pt=3)
            i += 1
            continue

        b.para(line, face=body_face, next_pt=4)
        i += 1

    b.save(out_path)
    return b


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__.strip().split("\n\n")[2])
    md, out, title = sys.argv[1], sys.argv[2], sys.argv[3]
    template = sys.argv[4] if len(sys.argv) > 4 else None
    if not template:
        sys.exit("a template .hwpx is required — see SKILL.md on why")
    convert(md, out, title, template)
    import json
    print(json.dumps(HwpxBuilder.verify(out), ensure_ascii=False, indent=1))
