#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""html_chapter — an HTML document → .hwpx, through markdown_chapter.

    python3 html_chapter.py in.html out.hwpx [template.hwpx]
                            [--md out.md] [--number-levels]

markdown_chapter.py reads a markdown subset written by hand. A manuscript
kept elsewhere usually leaves as pandoc output, and pandoc's markdown does not
fit that subset: a table whose cells wrap is written as a *multiline* table
(dashed rules, columns marked by spacing in display width — two columns per
Hangul syllable), and a figure as a raw `<figure>` block. Both are skipped or
scattered into body paragraphs (#17).

HTML has no such ambiguity — a cell is a `<td>`, a caption a `<figcaption>` —
so this reads pandoc's HTML (or any similar HTML) and writes the subset
markdown_chapter reads, then builds with markdown_chapter.convert():

    <h1>/<h2>/<h3>      → # / ## / ###     (--number-levels: see below)
    <p>                 → paragraph; <strong>/<em>/<code> → ** / * / `
    <ul>/<ol> <li>      → "- " / "N. "      (nested lists are flattened)
    <table>             → pipe table; <p> inside a cell is kept as cell text;
                          a "|" in a cell becomes "｜" (markdown_chapter
                          splits rows on every bar, escaped or not)
    <figure>            → ![](png) + caption line; data: URIs are written out
    <figcaption>        → "**그림 N.** …" — a label repeated after itself
                          ("그림 1. 그림 1. …", as pandoc writes it when the
                          caption already carries the label) is dropped
    "표 N." paragraph    → the same de-duplication, so it stays a caption
    <pre>               → fenced code block, whitespace kept
    <blockquote>        → "> " quote
    <nav>, <style>, <script>, <head> → skipped

--number-levels: some exports put every section heading at the same tag (the
document title as <h1>, then "요약", "1. 개요", "1.1 목적" all as <h2>). With
the flag, a heading numbered "N.M …" becomes a section (##), "N.M.K …" a
sub-section (###), and any other heading below <h1> a chapter (#).

Images must be PNG — hwpx_build reads the size from the IHDR. A data: URI of
another type, or a missing file, stops the build with the figure named,
rather than leaving a hole that every structural check would pass.

--md keeps the intermediate markdown, so a diff against the source, or a
hand edit before building, is possible.
"""

from __future__ import annotations

import base64
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))
import markdown_chapter  # noqa: E402
from hwpx_build import BASE_TEMPLATE, HwpxBuilder  # noqa: E402

SKIP = {"nav", "style", "script", "head"}
# "그림 1. 그림 1. …" / "**표 2.** 표 2. …" — the label written twice
DUP_LABEL = re.compile(r"^(\*\*)?(그림|표|Figure|Table)\s*(\d+)\.(\*\*)?\s*\2\s*\3\.\s*")


def dedupe_label(text: str) -> str:
    m = DUP_LABEL.match(text)
    if not m:
        return text
    bold = "**" if m.group(1) else ""
    return f"{bold}{m.group(2)} {m.group(3)}.{bold} {text[m.end():]}"


def heading_level(tag: str, text: str, number_levels: bool) -> int:
    level = min(int(tag[1]), 3)
    if not number_levels or level == 1:
        return level
    if re.match(r"^\d+\.\d+\.\d+", text):
        return 3
    if re.match(r"^\d+\.\d+", text):
        return 2
    return 1


class _Converter(HTMLParser):
    def __init__(self, base: Path, img_dir: Path, number_levels: bool):
        super().__init__(convert_charrefs=True)
        self.base, self.img_dir, self.number_levels = base, img_dir, number_levels
        self.blocks: list[str] = []
        self.buf: list[str] = []
        self.skip = 0
        self.lists: list[list] = []      # [kind, counter, item already emitted]
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.in_cell = False
        self.in_pre = False
        self.quote = 0
        self.fig_src: str | None = None
        self.in_fig = False
        self.images = 0

    # -- inline text
    def _text(self) -> str:
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        return t

    def _has_text(self) -> bool:
        return bool("".join(self.buf).replace("**", "").replace("*", "").strip())

    def _emit_item(self, kind: list, text: str) -> None:
        kind[1] += 1
        self._emit(("- " if kind[0] == "ul" else f"{kind[1]}. ") + text)

    def _emit(self, line: str) -> None:
        if not line.strip():
            return
        if self.quote:
            line = "\n".join("> " + x for x in line.split("\n"))
        self.blocks.append(line)

    # -- images
    def _image(self, src: str) -> str:
        if src.startswith("data:"):
            head, _, data = src.partition(",")
            mime = head[5:].split(";")[0]
            if mime != "image/png":
                raise SystemExit(f"html_chapter: figure {self.images + 1} is {mime}; "
                                 "hwpx_build reads PNG only")
            self.images += 1
            p = self.img_dir / f"figure_{self.images}.png"
            p.write_bytes(base64.b64decode(data))
            return str(p)
        self.images += 1
        p = Path(src)
        if not p.is_absolute():
            p = self.base / p
        if not p.exists():
            raise SystemExit(f"html_chapter: figure {self.images} not found: {src}")
        if p.suffix.lower() != ".png":
            raise SystemExit(f"html_chapter: figure {self.images} is {p.suffix}; "
                             "hwpx_build reads PNG only")
        return str(p)

    # -- tags
    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        if self.skip:
            return
        a = dict(attrs)
        if tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "code" and not self.in_pre:
            self.buf.append("`")
        elif tag == "br":
            self.buf.append("\n" if self.in_pre else " ")
        elif tag == "pre":
            self.in_pre, self.buf = True, []
        elif tag in ("td", "th"):
            self.in_cell, self.buf = True, []
        elif tag == "tr":
            self.row = []
        elif tag == "table":
            self.table = []
        elif tag in ("ul", "ol"):
            # A list inside an item: write the outer item's own text first,
            # or the inner items' flush would take it with them.
            if self.lists and self._has_text():
                self._emit_item(self.lists[-1], self._text())
                self.lists[-1][2] = True
            self.lists.append([tag, 0, False])
        elif tag == "li":
            self.buf = []
            if self.lists:
                self.lists[-1][2] = False
        elif tag == "blockquote":
            self.quote += 1
        elif tag == "figure":
            self.in_fig, self.fig_src = True, None
        elif tag == "img":
            src = a.get("src", "")
            if self.in_fig:
                self.fig_src = src
            else:                                   # a bare image in a paragraph
                self._emit(f"![]({self._image(src)})")
        elif tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption"):
            if not (self.in_cell or self.lists):     # <p> inside a cell/item is inline
                self.buf = []

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "code" and not self.in_pre:
            self.buf.append("`")
        elif tag == "pre":
            self.in_pre = False
            code = "".join(self.buf).strip("\n")
            self.buf = []
            self._emit("```\n" + code + "\n```")
        elif tag in ("td", "th"):
            # markdown_chapter splits a row on every "|" — an escaped "\\|"
            # included — so a literal bar in a cell is written full-width.
            self.row.append(self._text().replace("|", "｜"))
            self.in_cell = False
        elif tag == "tr":
            self.table.append(self.row)
        elif tag == "table":
            rows = [r for r in self.table if r]
            if rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w]
                out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self._emit("\n".join(out))
            self.table = None
        elif tag == "li":
            kind, text = self.lists[-1], self._text()
            if not (kind[2] and not text):           # already written before its sub-list
                self._emit_item(kind, text)
        elif tag in ("ul", "ol"):
            self.lists.pop()
        elif tag == "blockquote":
            self.quote -= 1
        elif tag == "figcaption":
            cap = dedupe_label(self._text())
            img = f"![]({self._image(self.fig_src)})" if self.fig_src else ""
            self.fig_src = None
            self._emit(f"{img}\n{cap}" if img else cap)
        elif tag == "figure":
            if self.fig_src:                        # a figure with no caption
                self._emit(f"![]({self._image(self.fig_src)})")
            self.in_fig, self.fig_src = False, None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            t = self._text()
            if t:
                lvl = heading_level(tag, t, self.number_levels)
                self._emit("#" * lvl + " " + t)
        elif tag == "p":
            if self.in_cell or self.lists or self.in_fig:
                self.buf.append(" ")
                return
            self._emit(dedupe_label(self._text()))

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)


def html_to_markdown(html_path, img_dir, number_levels=False) -> str:
    html_path = Path(html_path)
    c = _Converter(html_path.resolve().parent, Path(img_dir), number_levels)
    c.feed(html_path.read_text(encoding="utf-8"))
    c.close()
    return "\n\n".join(c.blocks) + "\n"


def convert(html_path, out_path, template=None, md_path=None, number_levels=False):
    """Build out_path from html_path; returns the HwpxBuilder, as markdown_chapter does."""
    with tempfile.TemporaryDirectory(prefix="html_chapter_") as tmp:
        # A kept markdown file must still point at its images afterwards, so
        # decoded data: images go beside it rather than into the temp dir.
        if md_path:
            md_file = Path(md_path)
            img_dir = md_file.parent / f"{md_file.stem}_img"
            img_dir.mkdir(parents=True, exist_ok=True)
        else:
            md_file, img_dir = Path(tmp) / "chapter.md", Path(tmp)
        md = html_to_markdown(html_path, img_dir, number_levels)
        md_file.write_text(md, encoding="utf-8")
        return markdown_chapter.convert(md_file, out_path, None, template)


if __name__ == "__main__":
    args = sys.argv[1:]
    number_levels = "--number-levels" in args
    md_out = None
    if "--md" in args:
        i = args.index("--md")
        md_out = args[i + 1]
        del args[i:i + 2]
    args = [a for a in args if a != "--number-levels"]
    if len(args) < 2:
        sys.exit(__doc__.strip().split("\n\n")[1])
    src, out = args[0], args[1]
    template = args[2] if len(args) > 2 else str(BASE_TEMPLATE)
    import json
    for w in HwpxBuilder.check_template(template)["warnings"]:
        print("template:", w, file=sys.stderr)
    b = convert(src, out, template, md_out, number_levels)
    if b.font_fallbacks:
        print("font fallbacks:", b.font_fallbacks, file=sys.stderr)
    print(json.dumps(HwpxBuilder.verify(out), ensure_ascii=False, indent=1))
