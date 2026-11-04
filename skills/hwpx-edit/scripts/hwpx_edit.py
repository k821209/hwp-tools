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

import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

__all__ = ["Hwpx", "HwpxError"]

SECTION = "Contents/section0.xml"
HEADER = "Contents/header.xml"

#: Colours that are rarely present in an ordinary document, offered in order.
CANDIDATE_COLORS = ["#FF00FF", "#00B050", "#7030A0", "#00B0F0", "#E36C0A", "#C00000"]

_RUN = '<hp:run charPrIDRef="{cp}"><hp:t>{t}</hp:t></hp:run>'
_PARA = (
    '<hp:p id="2147483648" paraPrIDRef="{pp}" styleIDRef="{sid}" pageBreak="0" '
    'columnBreak="0" merged="0">{body}</hp:p>'
)
_LINESEG_TAIL = re.compile(r"<hp:linesegarray>.*?</hp:linesegarray></hp:p>$", re.S)


class HwpxError(RuntimeError):
    pass


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

    def _para_span(self, pos):
        """(start, end) of the INNERMOST hp:p containing `pos`.

        hp:p nests — table cells and memo subLists each hold their own. A
        non-greedy `<hp:p .*?</hp:p>` therefore cannot find a paragraph's
        boundary, and taking the OUTERMOST one makes a paragraph that holds a
        table swallow the whole table.
        """
        s, depth, i = self.section, 0, pos
        while True:
            po, pc = s.rfind("<hp:p ", 0, i), s.rfind("</hp:p>", 0, i)
            if po < 0:
                raise HwpxError("no enclosing paragraph")
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
            no, nc = s.find("<hp:p ", j + 1), s.find("</hp:p>", j + 1)
            if nc < 0:
                raise HwpxError("unterminated paragraph")
            if 0 <= no < nc:
                depth += 1
                j = no
            elif depth == 0:
                return a, nc + 7
            else:
                depth -= 1
                j = nc

    def _find(self, needle, occurrence=0):
        pos = -1
        for _ in range(occurrence + 1):
            pos = self.section.index(needle, pos + 1)
        return pos

    def runs_in(self, needle, occurrence=0):
        """The (charPrIDRef, text) runs of the paragraph holding `needle`.

        Use it to pick the charPr to clone, and to see where a run boundary
        falls — an anchor that straddles two runs cannot be matched as one
        string.
        """
        a, b = self._para_span(self._find(needle, occurrence))
        return re.findall(
            r'<hp:run charPrIDRef="(\d+)"><hp:t>(.*?)</hp:t></hp:run>', self.section[a:b], re.S
        )

    # ------------------------------------------------------------------ edits

    def append_run(self, needle, text, color=None, occurrence=0):
        """Add a run after the last run of the paragraph holding `needle`.

        The original runs are untouched, so the addition stays separable.
        """
        a, b = self._para_span(self._find(needle, occurrence))
        blk = self.section[a:b]
        cps = re.findall(r'<hp:run charPrIDRef="(\d+)">', blk)
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
        old = _RUN.format(cp=char_pr, t=before + middle + after)
        if self.section.count(old) != 1:
            raise HwpxError(f"run is not unique ({self.section.count(old)} matches)")
        cp = self.clone_charpr(char_pr, color) if color else str(char_pr)
        self.section = self.section.replace(
            old,
            _RUN.format(cp=char_pr, t=before)
            + _RUN.format(cp=cp, t=middle)
            + _RUN.format(cp=char_pr, t=after),
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
        m = re.search(r"<hp:t>([^<]*?)%s([^<]*?)</hp:t>" % re.escape(anchor), self.section)
        if not m:
            raise HwpxError(f"{anchor!r} is not inside a single <hp:t> — see runs_in()")
        pre = f"<hp:t>{m.group(1)}</hp:t>" if m.group(1) else ""
        post = f"<hp:t>{m.group(2)}</hp:t>" if m.group(2) else ""
        self.section = (self.section[:m.start()]
                        + pre + memo + f"<hp:t>{anchor}</hp:t>" + end + post
                        + self.section[m.end():])
        return self

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
            targets += [m.start() for m in re.finditer(re.escape(needle), self.section)]
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
        with zipfile.ZipFile(self.src) as zin, \
                zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = replaced.get(info.filename)
                zout.writestr(info, zin.read(info.filename) if data is None else data)
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
             "opens_in_hangul": None}
        with zipfile.ZipFile(path) as z:
            r["zip_ok"] = z.testzip() is None
            names = z.namelist()
            section = z.read(SECTION).decode("utf-8")
            header = z.read(HEADER).decode("utf-8")
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
        begins = re.findall(r'<hp:fieldBegin id="(\d+)" type="MEMO"', section)
        r["memos"] = {"count": len(begins),
                      "all_paired": all(f'<hp:fieldEnd beginIDRef="{b}"' in section
                                        for b in begins)}
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
        return r


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit("usage: hwpx_edit.py <file.hwpx>   # print colour usage")
    for color, use in Hwpx(sys.argv[1]).color_usage().items():
        print(f"{color:10s} runs {use['runs']:5d}  chars {use['chars']:7d}")
