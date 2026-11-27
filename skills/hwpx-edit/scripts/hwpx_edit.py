#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hwpx_edit — edit a Hangul .hwpx in place, without Hangul or LibreOffice.

A .hwpx is a zip of OWPML XML. This copies the original archive and swaps only
`Contents/section0.xml` (+ `header.xml` when styles change), so formatting and
BinData (images) survive byte-for-byte.

The rules below are not style preferences; each one is a failure that was hit
in practice. See reference/OWPML-NOTES.md for the evidence.

    doc = Hwpx("in.hwpx")
    pink = doc.pick_unused_color()              # never reuse a colour the file uses
    doc.append_run("...수행중", " 추가 문장.", color=pink)
    doc.add_memo("3,500천원", "집계표의 합계와 맞지 않습니다.", author="me")
    doc.strip_linesegarray()                    # REQUIRED, and last
    doc.save("out.hwpx")
    print(doc.verify(baseline="in.hwpx"))

Python 3.8+, standard library only.
"""

from __future__ import annotations

import html
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

__all__ = ["Hwpx", "HwpxError"]

SECTION = "Contents/section0.xml"
HEADER = "Contents/header.xml"
HPF = "Contents/content.hpf"

#: 1 mm in HWPUNIT (1/7200 inch).
MM = 7200 / 25.4


def _builder():
    """hwpx_build, for the picture XML. Shared rather than copied, so a picture
    inserted here is exactly the one hwpx-build writes and confirmed in Hangul."""
    here = Path(__file__).resolve().parents[2] / "hwpx-build" / "scripts"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import hwpx_build
    except ImportError as e:
        raise HwpxError(f"pictures need hwpx-build beside hwpx-edit ({here}): {e}")
    return hwpx_build

#: Colours that are rarely present in an ordinary document, offered in order.
CANDIDATE_COLORS = ["#FF00FF", "#00B050", "#7030A0", "#00B0F0", "#E36C0A", "#C00000"]

_RUN = '<hp:run charPrIDRef="{cp}"><hp:t>{t}</hp:t></hp:run>'
_PARA = (
    '<hp:p id="2147483648" paraPrIDRef="{pp}" styleIDRef="{sid}" pageBreak="0" '
    'columnBreak="0" merged="0">{body}</hp:p>'
)
_LINESEG_TAIL = re.compile(r"<hp:linesegarray>.*?</hp:linesegarray></hp:p>$", re.S)


def _top_spans(s: str, open_tok: str, close_tok: str):
    """Spans of the TOP-LEVEL `open_tok`…`close_tok` pairs in `s`.

    A cell may hold another table, so `re.findall` over `<hp:tc .*?</hp:tc>`
    would return the inner cells as if they were the row's own.
    """
    out, i, depth, start = [], 0, 0, 0
    while i < len(s):
        no, nc = s.find(open_tok, i), s.find(close_tok, i)
        if nc < 0:
            break
        if 0 <= no < nc:
            if depth == 0:
                start = no
            depth += 1
            i = no + len(open_tok)
        else:
            depth -= 1
            i = nc + len(close_tok)
            if depth == 0:
                out.append((start, i))
    return out


class HwpxError(RuntimeError):
    pass


def _plain(xml: str) -> str:
    """The text of an XML fragment as a reader sees it: memo bodies (the
    fieldBegin subtrees) left out, one line per paragraph, entities decoded."""
    xml = re.sub(r"<hp:fieldBegin\b.*?</hp:fieldBegin>", "", xml, flags=re.S)
    lines = []
    for part in re.split(r"</hp:p>", xml):
        t = "".join(re.findall(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", part, re.S))
        t = re.sub(r"<[^>]+>", "", t)
        if t or lines:
            lines.append(html.unescape(t))
    return "\n".join(lines).strip("\n")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Hwpx:
    def __init__(self, path):
        self.src = Path(path)
        with zipfile.ZipFile(self.src) as z:
            names = set(z.namelist())
            for part in (SECTION, HEADER):
                if part not in names:
                    raise HwpxError(f"{self.src.name}: {part} missing — not a .hwpx?")
            self.section = z.read(SECTION).decode("utf-8")
            self.header = z.read(HEADER).decode("utf-8")
            self.hpf = z.read(HPF).decode("utf-8") if HPF in names else None
            self._names = names
        self._new_bins: list[tuple[str, Path]] = []   # (item id, png) added this session
        self._clones: dict[tuple[str, str], str] = {}
        self._new_charpr: list[str] = []
        self._marked: set[str] = set()  # charPr ids this session created

    # ------------------------------------------------------------------ colour

    def color_usage(self) -> dict[str, dict]:
        """Which text colours the document ACTUALLY uses, with volume.

        Check this before choosing a colour to mark edits with. Reusing a colour
        the document already carries does not merely blur the distinction: if the
        charPr you clone is already that colour, the clone is a no-op and Hangul
        merges your run into the neighbouring original on its next save, after
        which the two cannot be told apart.
        """
        by_id = {}
        for blk in re.findall(r"<hh:charPr\b.*?</hh:charPr>", self.header, re.S):
            pid = re.match(r'<hh:charPr\b[^>]*?id="(\d+)"', blk).group(1)
            m = re.search(r'<hh:charPr\b[^>]*?textColor="([^"]*)"', blk)
            by_id[pid] = (m.group(1).upper() if m else "?")
        out: dict[str, dict] = {}
        for cp, t in re.findall(
            r'<hp:run charPrIDRef="(\d+)"><hp:t>(.*?)</hp:t></hp:run>', self.section, re.S
        ):
            c = by_id.get(cp, "?")
            e = out.setdefault(c, {"runs": 0, "chars": 0})
            e["runs"] += 1
            e["chars"] += len(t)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["chars"]))

    def pick_unused_color(self, candidates=None) -> str:
        used = set(self.color_usage())
        for c in candidates or CANDIDATE_COLORS:
            if c.upper() not in used:
                return c
        raise HwpxError("every candidate colour is already used; pass your own")

    def clone_charpr(self, src_id, text_color) -> str:
        """Clone a charPr, changing only textColor. Returns the new id.

        Keeps the surrounding size/face, so marked text differs from its
        neighbours in colour alone.
        """
        key = (str(src_id), text_color.upper())
        if key in self._clones:
            return self._clones[key]
        m = re.search(r'<hh:charPr\b[^>]*?id="%s".*?</hh:charPr>' % re.escape(str(src_id)),
                      self.header, re.S)
        if not m:
            raise HwpxError(f"charPr id={src_id} not found")
        blk = m.group(0)
        open_tag = re.match(r"<hh:charPr\b[^>]*>", blk).group(0)
        new_id = str(self._next_charpr_id())
        nt = re.sub(r'\bid="\d+"', f'id="{new_id}"', open_tag, count=1)
        if "textColor=" in nt:
            nt = re.sub(r'\btextColor="[^"]*"', f'textColor="{text_color}"', nt, count=1)
        else:
            nt = nt[:-1] + f' textColor="{text_color}">'
        self._new_charpr.append(nt + blk[len(open_tag):])
        self._clones[key] = new_id
        self._marked.add(new_id)
        return new_id

    def _next_charpr_id(self) -> int:
        ids = [int(i) for i in re.findall(r'<hh:charPr\b[^>]*?id="(\d+)"', self.header)]
        return max(ids + [int(i) for i in self._marked] + [0]) + 1

    # ----------------------------------------------------------------- locate

    def _span(self, open_tok, close_tok, pos):
        """(start, end) of the INNERMOST `open_tok`…`close_tok` around `pos`.

        Every container in OWPML nests — a table cell holds paragraphs, a
        paragraph can hold a table, and a cell can hold another table. So a
        non-greedy regex cannot find any of their boundaries, and the
        OUTERMOST match is the wrong one: it makes a paragraph that holds a
        table swallow the whole table. This scans depth both ways.
        """
        s, depth, i = self.section, 0, pos
        while True:
            po, pc = s.rfind(open_tok, 0, i), s.rfind(close_tok, 0, i)
            if po < 0:
                raise HwpxError(f"no enclosing {open_tok.strip('<> ')}")
            if pc > po:
                depth += 1
                i = pc
            elif depth == 0:
                a = po
                break
            else:
                depth -= 1
                i = po
        depth, j = 0, a
        while True:
            no, nc = s.find(open_tok, j + 1), s.find(close_tok, j + 1)
            if nc < 0:
                raise HwpxError(f"unterminated {open_tok.strip('<> ')}")
            if 0 <= no < nc:
                depth += 1
                j = no
            elif depth == 0:
                return a, nc + len(close_tok)
            else:
                depth -= 1
                j = nc

    def _para_span(self, pos):
        """(start, end) of the INNERMOST hp:p containing `pos`."""
        return self._span("<hp:p ", "</hp:p>", pos)

    def _find(self, needle, occurrence=0):
        """Position of the `occurrence`-th `needle` in the section's text.

        The section holds text escaped, so "R&D" is "R&amp;D" there; the
        needle is looked for in that form, as the reader typed it."""
        xn, pos = _esc(needle), -1
        # Memo bodies are not the document: a reviewer's note quoting the very
        # phrase it marks would otherwise be found first, and the edit land in
        # the note.
        memos = [(m.start(), m.end()) for m in
                 re.finditer(r"<hp:fieldBegin\b.*?</hp:fieldBegin>", self.section, re.S)]
        k = 0
        while k <= occurrence:
            pos = self.section.find(xn, pos + 1)
            if pos < 0:
                raise HwpxError(f"{needle!r} not found" + (f" (occurrence {occurrence})" if k else "")
                                + " — it may straddle two runs; see runs_in()")
            if not any(a <= pos < b for a, b in memos):
                k += 1
        return pos

    def runs_in(self, needle, occurrence=0):
        """The (charPrIDRef, text) runs of the paragraph holding `needle`.

        Use it to pick the charPr to clone, and to see where a run boundary
        falls — an anchor that straddles two runs cannot be matched as one
        string.
        """
        a, b = self._para_span(self._find(needle, occurrence))
        # A memo sits inside a run, with runs of its own in its body: take the
        # memo bodies out first, then each run's text. Text as the reader sees
        # it ("R&D", not "R&amp;D"), which is also what split_run() takes.
        para = re.sub(r"<hp:fieldBegin\b.*?</hp:fieldBegin>", "", self.section[a:b], flags=re.S)
        return [(cp, html.unescape(re.sub(r"<[^>]+>", "", "".join(
                    re.findall(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", body or "", re.S)))))
                for cp, body in re.findall(
                    r'<hp:run charPrIDRef="(\d+)"(?:\s[^>]*)?(?:/>|>(.*?)</hp:run>)', para, re.S)]

    # ------------------------------------------------------------------ edits

    def append_run(self, needle, text, color=None, occurrence=0):
        """Add a run after the last run of the paragraph holding `needle`.

        The original runs are untouched, so the addition stays separable.
        """
        a, b = self._para_span(self._find(needle, occurrence))
        blk = self.section[a:b]
        # The paragraph's own runs only: a memo's body has runs of its own.
        cps = re.findall(r'<hp:run charPrIDRef="(\d+)">',
                         re.sub(r"<hp:fieldBegin\b.*?</hp:fieldBegin>", "", blk, flags=re.S))
        if not cps:
            raise HwpxError(f"no run in the paragraph holding {needle!r}")
        cp = self.clone_charpr(cps[-1], color) if color else cps[-1]
        cut = blk.rindex("</hp:run>") + len("</hp:run>")
        run = _RUN.format(cp=cp, t=_esc(text))
        self.section = self.section[:a] + blk[:cut] + run + blk[cut:] + self.section[b:]
        return self

    def insert_paragraph_after(self, needle, text, para_pr, style_id, char_pr,
                               color=None, occurrence=0):
        """Insert a new paragraph after the one holding `needle`.

        Copy `para_pr`/`style_id`/`char_pr` from a sibling so the new paragraph
        inherits its look. No linesegarray is written: strip_linesegarray()
        would remove it anyway, and Hangul lays the paragraph out on open.
        """
        _, b = self._para_span(self._find(needle, occurrence))
        cp = self.clone_charpr(char_pr, color) if color else str(char_pr)
        body = _RUN.format(cp=cp, t=_esc(text))
        self.section = (self.section[:b]
                        + _PARA.format(pp=para_pr, sid=style_id, body=body)
                        + self.section[b:])
        return self

    def split_run(self, char_pr, before, middle, after, color=None):
        """Re-colour `middle` inside one run by splitting it into three.

        For changing a single value in a sentence — the figure becomes its own
        run and only it carries the mark.
        """
        old = _RUN.format(cp=char_pr, t=_esc(before + middle + after))
        if self.section.count(old) != 1:
            raise HwpxError(f"run is not unique ({self.section.count(old)} matches)")
        cp = self.clone_charpr(char_pr, color) if color else str(char_pr)
        self.section = self.section.replace(
            old,
            _RUN.format(cp=char_pr, t=_esc(before))
            + _RUN.format(cp=cp, t=_esc(middle))
            + _RUN.format(cp=char_pr, t=_esc(after)),
        )
        return self

    def add_table_row(self, needle_in_table, values, color=None):
        """Append a row to the table holding `needle_in_table`.

        The last row is the template. Each cell's subList is rebuilt as exactly
        ONE paragraph: a template cell may hold several, and replacing only the
        first leaves the rest behind, concatenated onto the new value.
        """
        i = self._find(needle_in_table)
        s = self.section
        ta, tb = s.rfind("<hp:tbl ", 0, i), s.index("</hp:tbl>", i)
        open_end = s.index(">", ta) + 1
        rows = re.findall(r"<hp:tr>.*?</hp:tr>", s[ta:tb], re.S)
        if not rows:
            raise HwpxError("no row found in the table")
        cells = re.findall(r"<hp:tc .*?</hp:tc>", rows[-1], re.S)
        if len(cells) != len(values):
            raise HwpxError(f"table has {len(cells)} columns, got {len(values)} values")
        last_addr = max(int(x) for x in re.findall(r'rowAddr="(\d+)"', rows[-1]))
        out = []
        for cell, val in zip(cells, values):
            sl = cell.index(">", cell.index("<hp:subList")) + 1
            se = cell.index("</hp:subList>")
            inner = cell[sl:se]
            pr = re.search(r'paraPrIDRef="(\d+)" styleIDRef="(\d+)"', inner)
            cp0 = re.search(r'<hp:run charPrIDRef="(\d+)">', inner).group(1)
            cp = self.clone_charpr(cp0, color) if color else cp0
            p = _PARA.format(pp=pr.group(1), sid=pr.group(2),
                             body=_RUN.format(cp=cp, t=_esc(val))).replace(
                'id="2147483648"', 'id="0"')
            out.append(cell[:sl] + p + cell[se:])
        row = ("<hp:tr>" + "".join(out) + "</hp:tr>").replace(
            f'rowAddr="{last_addr}"', f'rowAddr="{last_addr + 1}"')
        s = s[:tb] + row + s[tb:]
        cnt = int(re.search(r'rowCnt="(\d+)"', s[ta:open_end]).group(1))
        s = s[:ta] + s[ta:open_end].replace(f'rowCnt="{cnt}"', f'rowCnt="{cnt + 1}"', 1) + s[open_end:]
        self.section = s
        return self

    def fill_cell(self, label, text, col=1, color=None, occurrence=0,
                  overwrite=False):
        """Write `text` into column `col` of the row whose label cell holds `label`.

        Filling a FORM is not adding a row. The cells already exist and are
        empty, and what a form needs is the cell BESIDE a label — which is why
        `add_table_row` does not cover it. Pass a list for several paragraphs
        in the one cell.

        The cell is chosen by its `hp:cellAddr colAddr`, not by its position
        among the row's cells, so a row carrying a merged cell still resolves.
        Its subList is rebuilt as exactly ONE paragraph per item: a blank
        template cell may already hold several empty paragraphs, and writing
        into only the first leaves the others sitting under the text (rule 4).

        Refuses a cell that already has text unless `overwrite=True` — a form
        is filled once, and silently replacing an answer is worse than failing.
        """
        lines = [text] if isinstance(text, str) else list(text)
        if not lines:
            raise HwpxError("no text to write")
        ra, rb = self._span("<hp:tr>", "</hp:tr>", self._find(label, occurrence))
        row = self.section[ra:rb]
        for ca, cb in _top_spans(row, "<hp:tc ", "</hp:tc>"):
            # A cell's own cellAddr follows its subList, so it is the LAST one
            # in the cell; any earlier one belongs to a table nested inside.
            addrs = re.findall(r'<hp:cellAddr colAddr="(\d+)"', row[ca:cb])
            if addrs and int(addrs[-1]) == col:
                break
        else:
            raise HwpxError(f"the row holding {label!r} has no cell at colAddr={col}")
        cell = row[ca:cb]
        # The cell's own subList opens first and closes last; a nested table's
        # cells close theirs in between.
        sl = cell.index(">", cell.index("<hp:subList")) + 1
        se = cell.rindex("</hp:subList>")
        inner = cell[sl:se]
        if not overwrite:
            had = "".join(re.findall(r"<hp:t>(.*?)</hp:t>", inner, re.S)).strip()
            if had:
                raise HwpxError(
                    f"cell at colAddr={col} is not empty ({had[:30]!r}); "
                    "pass overwrite=True to replace it")
        pr = re.search(r'paraPrIDRef="(\d+)" styleIDRef="(\d+)"', inner)
        cp0 = re.search(r'<hp:run charPrIDRef="(\d+)"', inner)
        if not (pr and cp0):
            raise HwpxError("cell has no paragraph to copy its look from")
        cp = self.clone_charpr(cp0.group(1), color) if color else cp0.group(1)
        body = "".join(
            _PARA.format(pp=pr.group(1), sid=pr.group(2),
                         body=_RUN.format(cp=cp, t=_esc(ln)))
                 .replace('id="2147483648"', f'id="{n}"')
            for n, ln in enumerate(lines))
        row = row[:ca] + cell[:sl] + body + cell[se:] + row[cb:]
        self.section = self.section[:ra] + row + self.section[rb:]
        return self

    # --------------------------------------------------------------- pictures

    def _next_item_id(self) -> str:
        """An `imageN` id no BinData file or manifest item already uses."""
        used = set(re.findall(r'<opf:item id="([^"]+)"', self.hpf or ""))
        used |= {Path(n).stem for n in self._names if n.startswith("BinData/")}
        used |= {i for i, _ in self._new_bins}
        n = 1 + max((int(m.group(1)) for u in used
                     for m in [re.fullmatch(r"image(\d+)", u)] if m), default=0)
        return f"image{n}"

    def _next_inst_id(self) -> int:
        """One above every object id and instid in the section.

        Hangul gives each table, picture and control a large id of its own; a
        duplicate passes every check here and is the kind of thing Hangul
        resolves by dropping one of the two.
        """
        # Object ids only: a paragraph's id is a different counter, and new
        # paragraphs carry 2147483648 (2^31), which would push the maximum
        # out of range.
        ids = {int(x) for x in re.findall(
            r'<hp:(?!p\b)\w+\b[^>]*?\b(?:id|instid|instId)="(\d+)"', self.section)}
        ids = {i for i in ids if i < 2 ** 31 - 1}
        n = max(ids, default=1000000) + 1
        while n >= 2 ** 31 - 1 or n in ids:      # at the top of the range: find a gap
            n = (n + 7919) % (2 ** 31 - 1) or 1000000
        return n

    def _pic_run(self, png, width_mm, char_pr) -> str:
        hb = _builder()
        if self.hpf is None:
            raise HwpxError(f"{self.src.name}: no {HPF} to register a picture in")
        png = Path(png)
        if not png.exists():
            raise HwpxError(f"image not found: {png}")
        px_w, px_h = hb.png_size(png)          # raises on anything but PNG
        w = int(round(width_mm * MM))
        h = int(round(w * px_h / px_w))
        item = self._next_item_id()
        self._new_bins.append((item, png))
        pic = hb.pic_xml(self._next_inst_id(), item, w, h, png.name)
        return f'<hp:run charPrIDRef="{char_pr}">{pic}</hp:run>'   # rule 4: in a run

    def insert_image_after(self, needle, png, width_mm=120, para_pr=None,
                           style_id=None, char_pr=None, occurrence=0):
        """Put a PNG in a new paragraph after the one holding `needle`.

        Inside a table cell the paragraph goes into that cell. Its paragraph,
        style and character properties are the anchor paragraph's unless given
        — pass the paraPr of a centred paragraph to centre the picture. The
        image file and its manifest entry are written by save().
        """
        a, b = self._para_span(self._find(needle, occurrence))
        anchor = self.section[a:b]
        m = re.match(r'<hp:p\b[^>]*paraPrIDRef="(\d+)"[^>]*styleIDRef="(\d+)"', anchor)
        cp = re.search(r'<hp:run charPrIDRef="(\d+)"', anchor)
        pp = para_pr or (m.group(1) if m else "0")
        sid = style_id or (m.group(2) if m else "0")
        run = self._pic_run(png, width_mm, char_pr or (cp.group(1) if cp else "0"))
        self.section = self.section[:b] + _PARA.format(pp=pp, sid=sid, body=run) + self.section[b:]
        return self

    def fill_cell_image(self, label, png, col=1, width_mm=None, occurrence=0,
                        overwrite=False):
        """Put a PNG into column `col` of the row whose label cell holds `label`.

        The form-filling counterpart of fill_cell(): the cell is chosen by its
        colAddr, refused if it already has text unless `overwrite`, and its
        subList rebuilt as one paragraph holding the picture. Without
        `width_mm` the picture is as wide as the cell inside its margins.
        """
        ra, rb = self._span("<hp:tr>", "</hp:tr>", self._find(label, occurrence))
        row = self.section[ra:rb]
        for ca, cb in _top_spans(row, "<hp:tc ", "</hp:tc>"):
            addrs = re.findall(r'<hp:cellAddr colAddr="(\d+)"', row[ca:cb])
            if addrs and int(addrs[-1]) == col:
                break
        else:
            raise HwpxError(f"the row holding {label!r} has no cell at colAddr={col}")
        cell = row[ca:cb]
        sl = cell.index(">", cell.index("<hp:subList")) + 1
        se = cell.rindex("</hp:subList>")
        inner = cell[sl:se]
        if not overwrite:
            had = "".join(re.findall(r"<hp:t>(.*?)</hp:t>", inner, re.S)).strip()
            if had or "<hp:pic" in inner:
                raise HwpxError(f"cell at colAddr={col} is not empty; pass overwrite=True")
        pr = re.search(r'paraPrIDRef="(\d+)" styleIDRef="(\d+)"', inner)
        cp0 = re.search(r'<hp:run charPrIDRef="(\d+)"', inner)
        if not (pr and cp0):
            raise HwpxError("cell has no paragraph to copy its look from")
        if width_mm is None:
            cw = int(re.findall(r'<hp:cellSz width="(\d+)"', cell)[-1])
            mg = re.findall(r'<hp:cellMargin left="(\d+)" right="(\d+)"', cell)
            pad = sum(map(int, mg[-1])) if mg else 1020
            width_mm = max(cw - pad - 100, 100) / MM    # 100 HU short of the margin
        run = self._pic_run(png, width_mm, cp0.group(1))
        body = _PARA.format(pp=pr.group(1), sid=pr.group(2), body=run)
        row = row[:ca] + cell[:sl] + body + cell[se:] + row[cb:]
        self.section = self.section[:ra] + row + self.section[rb:]
        return self

    def add_memo(self, anchor, body, author="", number=None, shape_id="65535",
                 created="2026-01-01T00:00:00Z"):
        """Attach a margin memo to `anchor`.

        A memo is NOT an `hp:memo` element: it is an `hp:fieldBegin type="MEMO"`
        carrying the text in a subList, with `fieldBegin … anchor … fieldEnd`
        wrapping the span, all inside one `hp:run`. With shape_id 65535 (the
        default shape) no `hh:memoProperties` is needed, so header styles are
        untouched.

        `anchor` must sit inside a single `<hp:t>`; check with runs_in() first.
        """
        n = number if number is not None else self._next_memo_number()
        fid, fieldid = 1238470000 + n, 623209000 + n
        memo = (
            f'<hp:ctrl><hp:fieldBegin id="{fid}" type="MEMO" name="" editable="1" '
            f'dirty="1" zorder="{n}" fieldid="{fieldid}" metaTag="">'
            '<hp:parameters cnt="7" name="">'
            '<hp:integerParam name="Prop">0</hp:integerParam>'
            f'<hp:stringParam name="Command">MEMO/{shape_id}/{n}/0/0/{author}/\\;;</hp:stringParam>'
            f'<hp:stringParam name="ID">memo{n}</hp:stringParam>'
            f'<hp:integerParam name="Number">{n}</hp:integerParam>'
            f'<hp:stringParam name="Author">{author}</hp:stringParam>'
            f'<hp:stringParam name="MemoShapeIDRef">{shape_id}</hp:stringParam>'
            f'<hp:stringParam name="CreateDateTime">{created}</hp:stringParam>'
            '</hp:parameters>'
            '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
            'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
            'hasTextRef="0" hasNumRef="0">'
            '<hp:p id="0" paraPrIDRef="44" styleIDRef="13" pageBreak="0" columnBreak="0" '
            f'merged="0"><hp:run charPrIDRef="64"><hp:t>{_esc(body)}</hp:t></hp:run></hp:p>'
            '</hp:subList></hp:fieldBegin></hp:ctrl>'
        )
        end = f'<hp:ctrl><hp:fieldEnd beginIDRef="{fid}" fieldid="{fieldid}"/></hp:ctrl>'
        # The section holds the text escaped ("&lt;", "&amp;"), so the anchor is
        # looked for, and written back, in that form — unescaped, an anchor
        # with "&" or "<" was not found, or was written as broken XML.
        xa = _esc(anchor)
        m = re.search(r"<hp:t>([^<]*?)%s([^<]*?)</hp:t>" % re.escape(xa), self.section)
        if not m:
            raise HwpxError(f"{anchor!r} is not inside a single <hp:t> — see runs_in()")
        pre = f"<hp:t>{m.group(1)}</hp:t>" if m.group(1) else ""
        post = f"<hp:t>{m.group(2)}</hp:t>" if m.group(2) else ""
        self.section = (self.section[:m.start()]
                        + pre + memo + f"<hp:t>{xa}</hp:t>" + end + post
                        + self.section[m.end():])
        return self

    def memos(self):
        """Every margin memo, in document order — the reading side of add_memo().

        Each is a dict: number, author, created, body (paragraphs joined with
        "\n"), anchor (the text the memo marks), para (the paragraph it sits
        in), id, and problem. Hangul's own markup, as a reviewer leaves it:

        - the span is `fieldBegin … fieldEnd beginIDRef="<id>"`, paired by id
          — every memo in one file can carry the same `fieldid`, and Hangul
          writes no `id` on fieldEnd;
        - memos nest: one memo's span can hold another memo whole, so the
          anchor leaves out the inner memo's body but keeps the text it marks.

        A memo whose span cannot be found gets anchor None and says why in
        `problem`, never an empty string — an empty anchor would read as "a
        memo on nothing" when the reading itself failed.
        """
        s, out = self.section, []
        for m in re.finditer(r'<hp:fieldBegin\b[^>]*\btype="MEMO"[^>]*>', s):
            fid = re.search(r'\bid="([^"]*)"', m.group(0)).group(1)
            close = s.index("</hp:fieldBegin>", m.end()) + len("</hp:fieldBegin>")
            head = s[m.end():close]
            param = lambda n: (re.search(r'<hp:\w+Param name="%s">([^<]*)</hp:\w+Param>' % n, head)
                               or [None, None])[1]
            sub = re.search(r"<hp:subList\b.*</hp:subList>", head, re.S)
            body = _plain(sub.group(0)) if sub else ""
            num = param("Number")
            item = {"number": int(num) if num and num.isdigit() else num,
                    "author": html.unescape(param("Author") or ""),
                    "created": param("CreateDateTime"), "body": body,
                    "anchor": None, "para": None, "id": fid, "problem": None}
            ends = [e for e in re.finditer(
                r'<hp:fieldEnd\b[^>]*\bbeginIDRef="%s"[^>]*/>' % re.escape(fid), s)]
            if not ends:
                item["problem"] = f"no fieldEnd with beginIDRef={fid}"
            elif len(ends) > 1:
                item["problem"] = f"{len(ends)} fieldEnds with beginIDRef={fid}"
            elif ends[0].start() < m.start():
                item["problem"] = "fieldEnd comes before its fieldBegin"
            else:
                item["anchor"] = _plain(s[close:ends[0].start()])
            a, b = self._para_span(m.start())
            item["para"] = _plain(s[a:b])
            out.append(item)
        return out

    def _next_memo_number(self):
        ns = [int(x) for x in re.findall(r'<hp:stringParam name="ID">memo(\d+)</hp:stringParam>',
                                         self.section)]
        return max(ns, default=0) + 1

    # ---------------------------------------------------------------- finish

    def strip_linesegarray(self, extra_needles=()):
        """Drop the stale line-layout cache from every paragraph touched. REQUIRED.

        `hp:linesegarray` records where each line of a paragraph breaks. Edit the
        paragraph and leave it, and Hangul refuses the file with *"문서가 손상되었거나
        변조되었을 가능성이 있습니다 / [문서 보안 설정]을 [낮음]으로 설정해야 합니다"*.
        Copying one from a sibling to fill the gap is the same error. Removing it
        makes Hangul lay the paragraph out afresh on open.

        Run this LAST — after memos. A memo turns its paragraph into a touched
        one, so stripping before inserting leaves exactly those paragraphs stale.
        """
        targets = [m.start() for cid in self._marked
                   for m in re.finditer(f'charPrIDRef="{cid}"', self.section)]
        targets += [m.start() for m in re.finditer('type="MEMO"', self.section)]
        for needle in extra_needles:
            targets += [m.start() for m in re.finditer(re.escape(_esc(needle)), self.section)]
        spans = sorted({self._para_span(p) for p in targets})
        n = 0
        for a, b in reversed(spans):
            blk = self.section[a:b]
            fixed = _LINESEG_TAIL.sub("</hp:p>", blk)
            if fixed != blk:
                self.section = self.section[:a] + fixed + self.section[b:]
                n += 1
        return n

    def save(self, out_path):
        """Rewrite the archive, replacing only the parts that changed.

        Every other entry — BinData images, Preview, META-INF — is carried over
        with its original ZipInfo, so nothing but the edited XML differs.

        Do NOT append to a copy with ZipFile(..., "a"): zipfile APPENDS a second
        entry under the same name instead of replacing, leaving the stale copy
        in the archive for a reader to find first.
        """
        out = Path(out_path)
        if out.resolve() == self.src.resolve():
            raise HwpxError("refusing to overwrite the source; write to a new path")
        if self._new_charpr:
            cnt = int(re.search(r'<hh:charProperties itemCnt="(\d+)">', self.header).group(1))
            self.header = self.header.replace(
                f'<hh:charProperties itemCnt="{cnt}">',
                f'<hh:charProperties itemCnt="{cnt + len(self._new_charpr)}">', 1)
            self.header = self.header.replace(
                "</hh:charProperties>", "".join(self._new_charpr) + "</hh:charProperties>", 1)
            self._new_charpr = []
        replaced = {SECTION: self.section.encode("utf-8"),
                    HEADER: self.header.encode("utf-8")}
        # Pictures added this session: their files and manifest entries go in
        # the same rewrite, never in a second pass over the saved archive.
        if self._new_bins:
            items = "".join(
                f'<opf:item id="{i}" href="BinData/{i}.png" media-type="image/png" '
                'isEmbeded="1"/>' for i, _ in self._new_bins)
            self.hpf = self.hpf.replace("</opf:manifest>", items + "</opf:manifest>", 1)
            replaced[HPF] = self.hpf.encode("utf-8")
        with zipfile.ZipFile(self.src) as zin, \
                zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = replaced.get(info.filename)
                zout.writestr(info, zin.read(info.filename) if data is None else data)
            for i, png in self._new_bins:
                zout.writestr(f"BinData/{i}.png", png.read_bytes())
        self._names |= {f"BinData/{i}.png" for i, _ in self._new_bins}
        self._new_bins = []
        return out

    @staticmethod
    def verify(path, expect_memos=None):
        """Structural checks. Passing them does NOT mean Hangul will open it.

        LibreOffice cannot load .hwpx, so there is no way to render or open the
        result here. Report these as structural checks, never as "it opens".
        """
        path = Path(path)
        r = {"zip_ok": None, "xml_ok": None, "charpr_count_match": None,
             "charpr_dup_ids": None, "stale_linesegarray": None, "memos": None,
             "images": None, "opens_in_hangul": None}
        with zipfile.ZipFile(path) as z:
            r["zip_ok"] = z.testzip() is None
            names = z.namelist()
            section = z.read(SECTION).decode("utf-8")
            header = z.read(HEADER).decode("utf-8")
            hpf = z.read(HPF).decode("utf-8") if HPF in names else ""
            try:
                ET.fromstring(section.encode())
                ET.fromstring(header.encode())
                r["xml_ok"] = True
            except ET.ParseError as e:
                r["xml_ok"] = f"ParseError: {e}"
        blocks = re.findall(r"<hh:charPr\b.*?</hh:charPr>", header, re.S)
        cnt = int(re.search(r'<hh:charProperties itemCnt="(\d+)">', header).group(1))
        ids = [re.match(r'<hh:charPr\b[^>]*?id="(\d+)"', b).group(1) for b in blocks]
        r["charpr_count_match"] = (cnt == len(blocks))
        r["charpr_dup_ids"] = len(ids) - len(set(ids))
        begins = [re.search(r'\bid="(\d+)"', t).group(1) for t in
                  re.findall(r'<hp:fieldBegin\b[^>]*\btype="MEMO"[^>]*>', section)]
        r["memos"] = {"count": len(begins),
                      # paired by beginIDRef wherever it sits among the attributes
                      "all_paired": all(re.search(r'<hp:fieldEnd\b[^>]*\bbeginIDRef="%s"' % b,
                                                  section) for b in begins)}
        if expect_memos is not None:
            r["memos"]["expected"] = expect_memos
            r["memos"]["ok"] = (len(begins) == expect_memos)
        # A memo paragraph that still carries a line cache is the state Hangul
        # rejects. Other edited paragraphs cannot be identified from the file
        # alone, so this checks the ones that can be.
        probe = Hwpx.__new__(Hwpx)
        probe.section = section
        stale = 0
        for m in re.finditer('type="MEMO"', section):
            a, b = probe._para_span(m.start())
            if _LINESEG_TAIL.search(section[a:b]):
                stale += 1
        r["stale_linesegarray"] = stale
        r["duplicate_zip_entries"] = len(names) - len(set(names))
        # Pictures: the file, its manifest item and the body's reference are
        # three separate things, and a picture missing any one of them passes
        # every other check here and fails only in Hangul.
        files = {n for n in names if n.startswith("BinData/")}
        manifest = dict(re.findall(r'<opf:item id="([^"]+)" href="(BinData/[^"]+)"', hpf))
        refs = set(re.findall(r'binaryItemIDRef="([^"]+)"', section))
        r["images"] = {
            "files": len(files), "manifest": len(manifest), "referenced": len(refs),
            "pictures": len(re.findall(r"<hp:pic\b", section)),
            "ref_not_in_manifest": sorted(refs - set(manifest)),
            "manifest_without_file": sorted(i for i, h in manifest.items() if h not in files),
            "pic_outside_run": len(re.findall(r"<hp:pic\b", section))
                               - len(re.findall(r"<hp:run[^>]*><hp:pic\b", section)),
        }
        r["images"]["ok"] = not (r["images"]["ref_not_in_manifest"]
                                 or r["images"]["manifest_without_file"]
                                 or r["images"]["pic_outside_run"])
        return r


if __name__ == "__main__":
    import json
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit("usage: hwpx_edit.py <file.hwpx> [--memos]   # colour usage, or the memos")
    if "--memos" in sys.argv:
        print(json.dumps(Hwpx(args[0]).memos(), ensure_ascii=False, indent=1))
    else:
        for color, use in Hwpx(args[0]).color_usage().items():
            print(f"{color:10s} runs {use['runs']:5d}  chars {use['chars']:7d}")
