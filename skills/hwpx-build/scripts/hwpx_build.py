#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hwpx_build — build a Hangul .hwpx document body, without Hangul or LibreOffice.

`hwpx_edit` revises a document someone else made. This writes a new one: a
chapter, a report, a manual — headings, paragraphs, tables, images, code,
quotes — from a structured description.

It does NOT write `header.xml` from scratch. A header carries font tables,
border fills, style tables and numbering, and inventing one is how you get a
file that passes every structural check and still will not open. Instead a
**template** `.hwpx` made by Hangul supplies the header, and this module
*derives* the character and paragraph properties it needs by cloning entries
already in it. Only `Contents/section0.xml` is written fresh.

    from hwpx_build import HwpxBuilder

    b = HwpxBuilder("template.hwpx")
    b.page(margins_mm={"top": 15, "bottom": 15, "left": 25, "right": 25})
    b.heading(1, "제3장 진핵생물 데이터 분석")
    b.para("본문 문단. **굵게** 와 *기울임*, `코드` 가 산다.")
    b.table([["항목", "값"], ["유전체 크기", "수백 Mb"]])
    b.image("fig3-1.png", width_mm=120)
    b.para("그림 3-1. 설명.", size=10, align="CENTER")
    b.save("out.hwpx")
    HwpxBuilder.verify("out.hwpx")

Python 3.8+, standard library only.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

__all__ = ["HwpxBuilder", "HwpxBuildError", "mono_cols"]

SECTION = "Contents/section0.xml"
HEADER = "Contents/header.xml"
HPF = "Contents/content.hpf"

#: 1 mm in HWPUNIT (1/7200 inch).
MM = 7200 / 25.4

_EMPH = re.compile(r"\*\*(.+?)\*\*|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")

#: A code span: a run of N backticks closed by a run of exactly N.
_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)

#: Private-use code points stand in for code spans while emphasis is parsed,
#: so a `*` inside `a*b*c` is never read as italic. Hangul text can carry
#: private-use characters of its own (HNC fonts put old hanja and symbols
#: there), so only code points absent from the paragraph are used.
_PUA0, _PUA1 = 0xE000, 0xF8FF

#: A footnote reference, markdown style: [^id].
_FNREF = re.compile(r"\[\^([^\]\s]+)\]")
#: Where a reference sits in the body until save() knows every definition.
_FNSLOT = re.compile(r"<!--hwpx-build:fn:(\d+)-->")

_SECTION_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<hs:sec xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app"'
    ' xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    ' xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph"'
    ' xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
    ' xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core"'
    ' xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"'
    ' xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history"'
    ' xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page"'
    ' xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:opf="http://www.idpf.org/2007/opf/"'
    ' xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart"'
    ' xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar"'
    ' xmlns:epub="http://www.idpf.org/2007/ops"'
    ' xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
)


class HwpxBuildError(RuntimeError):
    pass


def mono_cols(text: str) -> int:
    """Columns `text` takes in a code() block set in 굴림체: ASCII 1, else 2.

    Measured in Hangul (#9): Hangul syllables, hanja, box drawing (├ ─ │),
    arrows (→ ↓), circled digits (①), and the Latin-1 symbols · × ° plus ‘
    all took exactly two columns. Unicode calls most of those "ambiguous"
    width, and a Western monospace face draws them one column wide — so a
    width table from that world misaligns here. Characters outside that set
    (accented Latin, emoji, half-width kana) have not been measured.
    """
    return sum(1 if ord(c) < 128 else 2 for c in text)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _png_size(path: Path):
    """(width, height) in pixels, from the PNG IHDR. No Pillow needed."""
    with open(path, "rb") as f:
        head = f.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise HwpxBuildError(f"{path.name}: not a PNG (only PNG is supported)")
    return struct.unpack(">II", head[16:24])


class HwpxBuilder:
    """Assemble a section body against a Hangul-made template header."""

    def __init__(self, template):
        self.template = Path(template)
        with zipfile.ZipFile(self.template) as z:
            names = set(z.namelist())
            for part in (SECTION, HEADER, HPF):
                if part not in names:
                    raise HwpxBuildError(
                        f"{self.template.name}: {part} missing — not a .hwpx?")
            self.header = z.read(HEADER).decode("utf-8")
            self.hpf = z.read(HPF).decode("utf-8")
            tsec = z.read(SECTION).decode("utf-8")

        m = re.search(r"<hp:secPr\b.*?</hp:secPr>", tsec, re.S)
        if not m:
            raise HwpxBuildError("template has no hp:secPr to borrow")
        self.secpr = m.group(0)
        # Hangul writes the column definition right after secPr, in the same
        # run, and reads the two together. Borrowing secPr without it, the page
        # setup was ignored on open: Hangul laid out the body with its own
        # defaults (30 mm sides, 20 + 15 mm top), so every table sized to
        # text_width overran the right margin and every margin passed to
        # page() was silently dropped.
        c = re.match(r"\s*(<hp:ctrl>\s*<hp:colPr\b[^>]*/>\s*</hp:ctrl>)", tsec[m.end():])
        self.colpr = c.group(1) if c else (
            '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
            'sameSz="1" sameGap="0"/></hp:ctrl>')

        self._base_charpr = self._first_id("charPr")
        self._base_parapr = self._first_id("paraPr")
        self._base_borderfill = self._first_id("borderFill")
        self._fonts = self._font_table()

        self._new = {"charPr": [], "paraPr": [], "borderFill": []}
        self._cache: dict = {}
        self._fill_color: dict = {}   # borderFill id → faceColor, to merge fill+edges
        self._body: list[str] = []
        self._images: list[tuple[str, Path]] = []
        self._instid = 1000000
        self._fn_defs: dict = {}      # footnote id → text
        self._fn_refs: list = []      # footnote id per reference, in order written
        self._fn_style = (9.5, None)

    # ------------------------------------------------------------- header ids

    def _first_id(self, kind: str) -> str:
        m = re.search(r'<hh:%s\b[^>]*?id="(\d+)"' % kind, self.header)
        if not m:
            raise HwpxBuildError(f"template header has no hh:{kind}")
        return m.group(1)

    def _font_table(self) -> dict:
        m = re.search(r'<hh:fontface lang="HANGUL".*?</hh:fontface>', self.header, re.S)
        if not m:
            return {}
        return {face: fid for fid, face in
                re.findall(r'<hh:font id="(\d+)" face="([^"]+)"', m.group(0))}

    def font_id(self, face: str, default: str = "0") -> str:
        """Font id for a face name present in the template, else `default`.

        A face the template does not carry cannot be referenced — adding one to
        the font table means Hangul substitutes on open anyway. Pick from
        `builder.fonts()` instead of naming a face and hoping.
        """
        return self._fonts.get(face, default)

    def fonts(self) -> dict:
        return dict(self._fonts)

    def _next_id(self, kind: str) -> str:
        ids = [int(i) for i in re.findall(r'<hh:%s\b[^>]*?id="(\d+)"' % kind, self.header)]
        ids += [int(re.match(r'<hh:%s\b[^>]*?id="(\d+)"' % kind, b).group(1))
                for b in self._new[kind]]
        return str(max(ids + [0]) + 1)

    def _clone(self, kind: str, src_id: str, mutate) -> str:
        """Clone one header entry, run `mutate(open_tag, inner)`, return new id."""
        # No \b after the closing quote: the next character is a space, and a
        # quote-to-space transition is not a word boundary, so the match fails.
        m = re.search(r'<hh:%s\b[^>]*?id="%s".*?</hh:%s>'
                      % (kind, re.escape(str(src_id)), kind), self.header, re.S)
        if not m:
            raise HwpxBuildError(f"{kind} id={src_id} not found in template")
        blk = m.group(0)
        open_tag = re.match(r"<hh:%s\b[^>]*>" % kind, blk).group(0)
        inner = blk[len(open_tag):-len(f"</hh:{kind}>")]
        new_id = self._next_id(kind)
        open_tag = re.sub(r'\bid="\d+"', f'id="{new_id}"', open_tag, count=1)
        open_tag, inner = mutate(open_tag, inner)
        self._new[kind].append(open_tag + inner + f"</hh:{kind}>")
        return new_id

    # ------------------------------------------------------------ properties

    @staticmethod
    def _set_attr(tag: str, name: str, value: str) -> str:
        if f'{name}="' in tag:
            return re.sub(r'\b%s="[^"]*"' % name, f'{name}="{value}"', tag, count=1)
        return tag[:-1].rstrip("/") + f' {name}="{value}"' + ("/>" if tag.endswith("/>") else ">")

    def charpr(self, size=13.0, bold=False, italic=False, color="#000000",
               face=None, fill=None, shade=None) -> str:
        """Character properties id. Cloned from the template's first charPr.

        `size` in points, `face` a font name from `fonts()`, `fill` a
        borderFill id for character borders/background, `shade` a colour for
        the character shade (Hangul's 글자 모양 → 음영 색).
        """
        key = ("c", size, bold, italic, color.upper(), face, fill, shade)
        if key in self._cache:
            return self._cache[key]

        def mutate(tag, inner):
            tag = self._set_attr(tag, "height", str(int(round(size * 100))))
            tag = self._set_attr(tag, "textColor", color)
            tag = self._set_attr(tag, "shadeColor", shade or "none")
            if fill is not None:
                tag = self._set_attr(tag, "borderFillIDRef", str(fill))
            if face:
                fid = self.font_id(face)
                inner = re.sub(r"<hh:fontRef\b[^>]*/>",
                               '<hh:fontRef hangul="{0}" latin="{0}" hanja="{0}" '
                               'japanese="{0}" other="{0}" symbol="{0}" user="{0}"/>'.format(fid),
                               inner, count=1)
            # <hh:bold/> and <hh:italic/> sit between hh:offset and hh:underline.
            inner = inner.replace("<hh:bold/>", "").replace("<hh:italic/>", "")
            mark = ("<hh:bold/>" if bold else "") + ("<hh:italic/>" if italic else "")
            if mark:
                if "<hh:underline" in inner:
                    inner = inner.replace("<hh:underline", mark + "<hh:underline", 1)
                else:
                    inner += mark
            return tag, inner

        cid = self._clone("charPr", self._base_charpr, mutate)
        self._cache[key] = cid
        return cid

    def parapr(self, align="JUSTIFY", left_mm=0.0, right_mm=0.0, first_mm=0.0,
               prev_pt=0.0, next_pt=0.0, line_spacing=160, fill=None,
               border_edges=(), border_offsets=(283, 283, 142, 142),
               ignore_margin=True, auto_spacing=None) -> str:
        """Paragraph properties id.

        `first_mm` negative gives a hanging indent. `fill` shades the paragraph,
        `border_edges` is any of ("left","right","top","bottom").

        `border_offsets` is (left, right, top, bottom) in HWPUNIT: how far the
        shaded box extends beyond the text.

        `auto_spacing` sets Hangul's extra space between Korean and Latin /
        digits (문단 모양 → 한글과 영어·숫자 간격 자동 조절). None keeps the
        template's; False turns it off, which a fixed-width block needs.

        `ignore_margin` keeps the box off the paragraph's own space before and
        after. With it False the shading covers that space too, so raising
        `next_pt` to push a heading away from the body just makes the box
        taller and the gap never appears.
        """
        key = ("p", align, left_mm, right_mm, first_mm, prev_pt, next_pt,
               line_spacing, fill, tuple(border_edges), tuple(border_offsets),
               ignore_margin, auto_spacing)
        if key in self._cache:
            return self._cache[key]

        margin = (
            "<hh:margin>"
            '<hc:intent value="{i}" unit="HWPUNIT"/>'
            '<hc:left value="{l}" unit="HWPUNIT"/>'
            '<hc:right value="{r}" unit="HWPUNIT"/>'
            '<hc:prev value="{p}" unit="HWPUNIT"/>'
            '<hc:next value="{n}" unit="HWPUNIT"/>'
            "</hh:margin>"
            '<hh:lineSpacing type="PERCENT" value="{ls}" unit="HWPUNIT"/>'
        ).format(i=int(round(first_mm * MM)), l=int(round(left_mm * MM)),
                 r=int(round(right_mm * MM)), p=int(round(prev_pt * 100)),
                 n=int(round(next_pt * 100)), ls=int(line_spacing))

        # Shading and paragraph borders are the SAME borderFill. Taking `fill`
        # and ignoring `border_edges` is how a heading's rule silently vanishes
        # while the call that asked for it still reads correctly.
        if border_edges and fill is None:
            bf = self.borderfill(edges=border_edges)
        elif border_edges:
            bf = self.borderfill(face_color=self._fill_color.get(fill),
                                 edges=border_edges)
        else:
            bf = fill if fill is not None else self._base_borderfill
        ol, orr, ot, ob = border_offsets
        border = ('<hh:border borderFillIDRef="{bf}" offsetLeft="{l}" offsetRight="{r}" '
                  'offsetTop="{t}" offsetBottom="{b}" connect="0" ignoreMargin="{ig}"/>'
                  ).format(bf=bf, l=int(ol), r=int(orr), t=int(ot), b=int(ob),
                           ig=1 if ignore_margin else 0)

        def mutate(tag, inner):
            inner = re.sub(r"<hh:align\b[^>]*/>",
                           f'<hh:align horizontal="{align}" vertical="BASELINE"/>',
                           inner, count=1)
            # Margins appear twice — inside hp:case AND hp:default. Setting only
            # one leaves Hangul reading the other and the indent silently lost.
            inner = re.sub(r"<hh:margin>.*?</hh:margin>"
                           r'<hh:lineSpacing\b[^>]*/>', margin, inner, flags=re.S)
            inner = re.sub(r"<hh:border\b[^>]*/>", border, inner, count=1)
            if auto_spacing is not None:
                v = "1" if auto_spacing else "0"
                inner = re.sub(r"<hh:autoSpacing\b[^>]*/>",
                               f'<hh:autoSpacing eAsianEng="{v}" eAsianNum="{v}"/>', inner)
            return tag, inner

        pid = self._clone("paraPr", self._base_parapr, mutate)
        self._cache[key] = pid
        return pid

    def borderfill(self, face_color=None, edges=(), width="0.12 mm",
                   color="#000000") -> str:
        """A borderFill id for paragraph/cell shading and borders."""
        key = ("b", face_color, tuple(edges), width, color)
        if key in self._cache:
            return self._cache[key]

        def mutate(tag, inner):
            for e in ("left", "right", "top", "bottom"):
                kind = "SOLID" if e in edges else "NONE"
                inner = re.sub(r'<hh:%sBorder\b[^>]*/>' % e,
                               f'<hh:{e}Border type="{kind}" width="{width}" color="{color}"/>',
                               inner, count=1)
            brush = ('<hc:fillBrush><hc:winBrush faceColor="%s" hatchColor="#999999" '
                     'alpha="0"/></hc:fillBrush>' % (face_color or "none"))
            if "<hc:fillBrush>" in inner:
                inner = re.sub(r"<hc:fillBrush>.*?</hc:fillBrush>", brush, inner, flags=re.S)
            else:
                inner += brush
            return tag, inner

        bid = self._clone("borderFill", self._base_borderfill, mutate)
        self._cache[key] = bid
        self._fill_color[bid] = face_color
        return bid

    # ----------------------------------------------------------------- layout

    def page(self, width_mm=210, height_mm=297, margins_mm=None):
        """A4 by default. `margins_mm` keys: top bottom left right header footer."""
        m = {"top": 20, "bottom": 15, "left": 30, "right": 30,
             "header": 15, "footer": 15}
        m.update(margins_mm or {})
        self.secpr = re.sub(
            r'<hp:pagePr\b[^>]*>',
            '<hp:pagePr landscape="WIDELY" width="{w}" height="{h}" gutterType="LEFT_ONLY">'
            .format(w=int(round(width_mm * MM)), h=int(round(height_mm * MM))),
            self.secpr, count=1)
        self.secpr = re.sub(
            r"<hp:margin\b[^>]*/>",
            '<hp:margin header="{hd}" footer="{ft}" gutter="0" left="{l}" right="{r}" '
            'top="{t}" bottom="{b}"/>'.format(
                hd=int(round(m["header"] * MM)), ft=int(round(m["footer"] * MM)),
                l=int(round(m["left"] * MM)), r=int(round(m["right"] * MM)),
                t=int(round(m["top"] * MM)), b=int(round(m["bottom"] * MM))),
            self.secpr, count=1)
        self._page = (width_mm, height_mm, m)
        return self

    def _page_geom(self) -> dict:
        """Page size and margins in HWPUNIT, read from the secPr in use.

        Read from the XML rather than from what page() was given: without a
        page() call the geometry is the template's, and assuming A4 with
        30 mm sides sized every table wrongly in a template that was not.
        """
        pp = re.search(r"<hp:pagePr\b[^>]*>", self.secpr).group(0)
        mg = re.search(r"<hp:margin\b[^>]*/>", self.secpr).group(0)
        g = {k: int(v) for k, v in re.findall(r'\b(width|height)="(\d+)"', pp)}
        g.update({k: int(v) for k, v in re.findall(
            r'\b(header|footer|left|right|top|bottom)="(\d+)"', mg)})
        return g

    @property
    def text_width(self) -> int:
        """Usable text width in HWPUNIT: page width minus left and right."""
        g = self._page_geom()
        return g["width"] - g["left"] - g["right"]

    @property
    def text_height(self) -> int:
        """Usable text height in HWPUNIT, for sizing a tall figure or table.

        Page height minus top, bottom, header AND footer. Hangul lays out the
        header and footer areas inside the top and bottom margins' far side,
        so subtracting only top and bottom overstates the body by their sum —
        30 mm at the usual 15/15, which is enough to push a tall figure onto
        the next page.
        """
        g = self._page_geom()
        return (g["height"] - g["top"] - g["bottom"]
                - g["header"] - g["footer"])

    # ------------------------------------------------------------------ runs

    def inline_code(self, face="굴림체", shade="#F2F2F2", color=None):
        """How `` `code` `` spans inside para/heading/bullet/table text are set.

        `face` must be in `fonts()` (rule 3) or the span falls back to the
        surrounding face and only the shade marks it. `color=None` keeps the
        surrounding text colour. Call with `face=None, shade=None` to switch
        the styling off; the backticks are still removed.
        """
        self._code_style = (face, shade, color)
        return self

    def _runs(self, text, size, bold, color, face, notes=True) -> str:
        """One hp:run per span: **bold**, *italic*, `code`, and [^id] notes.

        Code spans are cut out first, as in markdown: what is between
        backticks is literal, so `a*b*c` keeps its asterisks and `[^x]`
        stays text. A code span inside bold stays bold. With `notes` False
        (the text of a footnote itself) a [^id] is left as written.
        """
        spans = {}                      # placeholder char -> ("code"|"fn", text)
        free = (chr(c) for c in range(_PUA0, _PUA1 + 1) if chr(c) not in text)

        def hold(m):
            body = m.group(2)
            # CommonMark: one space either side is padding, so `` `x` `` can
            # show a backtick next to the fence.
            if len(body) > 2 and body[0] == body[-1] == " " and body.strip():
                body = body[1:-1]
            ph = next(free, None)
            if ph is None:
                return m.group(0)
            spans[ph] = ("code", body)
            return ph

        def hold_note(m):
            ph = next(free, None)
            if ph is None:
                return m.group(0)
            spans[ph] = ("fn", m.group(1))
            return ph

        held = _CODE.sub(hold, text) if "`" in text else text
        if notes and "[^" in held:
            held = _FNREF.sub(hold_note, held)

        pieces, pos = [], 0
        for m in _EMPH.finditer(held):
            if m.start() > pos:
                pieces.append((held[pos:m.start()], bold, False))
            if m.group(1) is not None:
                pieces.append((m.group(1), True, False))
            else:
                pieces.append((m.group(2), bold, True))
            pos = m.end()
        if pos < len(held):
            pieces.append((held[pos:], bold, False))

        out = []          # (text, bold, italic, is_code)
        for t, b, i in pieces:
            last = 0
            for k, ch in enumerate(t) if spans else ():
                if ch in spans:
                    if k > last:
                        out.append((t[last:k], b, i, False))
                    kind, val = spans[ch]
                    out.append((val, b, i, kind))
                    last = k + 1
            if last < len(t):
                out.append((t[last:], b, i, None))
        if not out:
            out = [("", bold, False, None)]

        cface, cshade, ccolor = getattr(self, "_code_style", ("굴림체", "#F2F2F2", None))
        if cface and cface not in self.fonts():
            cface = None          # rule 3: never name a face the header lacks

        def run(t, b, i, kind):
            if kind == "fn":
                # The control goes in its own run, inside hp:ctrl. Its content
                # is written at save(), once every definition is known.
                self._fn_refs.append(t)
                return ('<hp:run charPrIDRef="%s"><hp:ctrl><!--hwpx-build:fn:%d-->'
                        '</hp:ctrl></hp:run>'
                        % (self.charpr(size=size, bold=b, italic=i, color=color,
                                       face=face), len(self._fn_refs) - 1))
            if kind == "code":
                cid = self.charpr(size=size, bold=b, italic=i, color=ccolor or color,
                                  face=cface or face, shade=cshade)
            else:
                cid = self.charpr(size=size, bold=b, italic=i, color=color, face=face)
            return '<hp:run charPrIDRef="%s"><hp:t>%s</hp:t></hp:run>' % (cid, _esc(t))

        return "".join(run(*o) for o in out)

    # ------------------------------------------------------------- footnotes

    def footnotes(self, defs, size=9.5, face=None):
        """Define footnotes, referenced as `[^id]` in any paragraph or table cell.

        `defs` maps id → text; the text reads **bold**, *italic* and `code`.
        Definitions may come before or after the references — they are
        resolved at save(), as markdown puts them at the end. A reference with
        no definition makes save() raise rather than print `[^id]`.

        Numbering is the template's (`hp:footNotePr` in the borrowed secPr)
        and runs in the order references appear. A note referenced twice is
        written twice: OWPML has no shared footnote.
        """
        self._fn_defs.update(defs)
        self._fn_style = (size, face)
        return self

    def _footnote_xml(self, n, text) -> str:
        fmt = re.search(r"<hp:footNotePr>\s*(<hp:autoNumFormat\b[^>]*/>)", self.secpr)
        fmt = fmt.group(1) if fmt else ('<hp:autoNumFormat type="DIGIT" userChar="" '
                                        'prefixChar="" suffixChar=")" supscript="0"/>')
        suffix = re.search(r'suffixChar="([^"]*)"', fmt).group(1)
        style = re.search(r'<hh:style id="(\d+)" type="PARA" [^>]*engName="Footnote"',
                          self.header)
        style = style.group(1) if style else "0"
        size, face = self._fn_style
        pp = self.parapr(align="JUSTIFY", line_spacing=130)
        num = ('<hp:run charPrIDRef="%s"><hp:ctrl><hp:autoNum num="%d" numType="FOOTNOTE">'
               '%s</hp:autoNum></hp:ctrl><hp:t> </hp:t></hp:run>'
               % (self.charpr(size=size, face=face), n, fmt))
        body = self._runs(text, size, False, "#000000", face, notes=False)
        self._instid += 1
        return ('<hp:footNote number="{n}" suffixChar="{sc}" instId="{i}">'
                '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
                'vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" '
                'textHeight="0" hasTextRef="0" hasNumRef="0">'
                '<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="{st}" pageBreak="0" '
                'columnBreak="0" merged="0">{num}{body}</hp:p></hp:subList></hp:footNote>'
                ).format(n=n, sc=ord(suffix) if suffix else 0, i=self._instid, pp=pp,
                         st=style, num=num, body=body)

    def _fill_footnotes(self, xml) -> str:
        missing = sorted({self._fn_refs[int(k)] for k in _FNSLOT.findall(xml)}
                         - set(self._fn_defs))
        if missing:
            raise HwpxBuildError(
                "footnote reference(s) with no definition: "
                + ", ".join(f"[^{m}]" for m in missing)
                + " — pass them to footnotes()")
        counter = iter(range(1, len(self._fn_refs) + 1))
        return _FNSLOT.sub(
            lambda m: self._footnote_xml(next(counter),
                                         self._fn_defs[self._fn_refs[int(m.group(1))]]),
            xml)

    def _p(self, para_pr, body, style="0") -> str:
        # No hp:linesegarray is written. It is a layout cache keyed to text that
        # does not exist yet; Hangul builds it on open. Writing a guess is the
        # single most reliable way to have the file refused.
        return ('<hp:p id="0" paraPrIDRef="%s" styleIDRef="%s" pageBreak="0" '
                'columnBreak="0" merged="0">%s</hp:p>' % (para_pr, style, body))

    # ---------------------------------------------------------------- blocks

    def para(self, text="", size=13.0, bold=False, color="#000000", face=None,
             align="JUSTIFY", left_mm=0.0, first_mm=0.0, prev_pt=0.0, next_pt=0.0,
             line_spacing=160, fill=None, border_edges=(),
             border_offsets=(283, 283, 142, 142), ignore_margin=True):
        pp = self.parapr(align=align, left_mm=left_mm, first_mm=first_mm,
                         prev_pt=prev_pt, next_pt=next_pt, line_spacing=line_spacing,
                         fill=fill, border_edges=border_edges,
                         border_offsets=border_offsets,
                         ignore_margin=ignore_margin)
        self._body.append(self._p(pp, self._runs(text, size, bold, color, face)))
        return self

    def heading(self, level, text, face=None):
        size = {1: 17.0, 2: 14.0, 3: 13.0}.get(level, 13.0)
        self.para(text, size=size, bold=True, face=face, align="LEFT",
                  prev_pt={1: 6, 2: 16, 3: 10}.get(level, 10),
                  next_pt={1: 14, 2: 6, 3: 3}.get(level, 3))
        return self

    def bullet(self, text, size=13.0, face=None, marker="· "):
        self.para(marker + text, size=size, face=face, left_mm=4, first_mm=-3)
        return self

    def code(self, lines, size=10.5, face="굴림체", fill_color="#F2F2F2",
             lang=None, theme=None):
        """A fenced code block, one paragraph per line.

        `lang` is "python" | "r" | "shell" | "plain", or None to guess from a
        first-line comment naming it. Pass "plain" for a data dump — colouring
        a VCF header as shell turns the header row into a comment.

        Emphasis is NOT parsed here: `**` and `*` in code are syntax.
        """
        try:
            from codelex import guess_lang, lex, THEME
        except ImportError:       # colouring is optional, the block is not
            guess_lang = lex = THEME = None

        bf = self.borderfill(face_color=fill_color)
        # Auto spacing off whatever the template says: with it on, Hangul adds
        # space at every Korean/Latin boundary and a fixed-width block stops
        # lining up even in a fixed-width face.
        pp = self.parapr(align="LEFT", left_mm=3, line_spacing=130, fill=bf,
                         auto_spacing=False)

        if lex is None or lang == "plain":
            for ln in lines:
                cp = self.charpr(size=size, face=face)
                self._body.append(self._p(
                    pp, '<hp:run charPrIDRef="%s"><hp:t>%s</hp:t></hp:run>'
                        % (cp, _esc(ln))))
            return self

        colors = dict(THEME, **(theme or {}))
        lang = lang or guess_lang(lines)

        # Lex the whole block, not line by line: a triple-quoted string or a
        # continued statement spans lines, and a per-line lexer would reopen
        # the string on every one of them.
        rows: list[list[tuple[str, str]]] = [[]]
        for text, kind in lex("\n".join(lines), lang):
            parts = text.split("\n")
            for n, part in enumerate(parts):
                if n:
                    rows.append([])
                if part:
                    rows[-1].append((part, kind))

        for row in rows:
            runs = "".join(
                '<hp:run charPrIDRef="%s"><hp:t>%s</hp:t></hp:run>'
                % (self.charpr(size=size, face=face,
                               color=colors.get(kind, colors["text"])), _esc(txt))
                for txt, kind in row)
            if not runs:   # a blank line still needs a run to hold the shading
                runs = ('<hp:run charPrIDRef="%s"><hp:t></hp:t></hp:run>'
                        % self.charpr(size=size, face=face))
            self._body.append(self._p(pp, runs))
        return self

    def quote(self, text, size=11.5, face=None, fill_color="#FFF6E5"):
        bf = self.borderfill(face_color=fill_color)
        self.para(text, size=size, face=face, left_mm=5, prev_pt=6, next_pt=6, fill=bf)
        return self

    def table(self, rows, widths_mm=None, header=True, size=10.5, face=None,
              head_fill="#EAEAEA", align="LEFT"):
        """A grid table. `rows` is a list of lists of strings.

        Without `widths_mm` the columns split `text_width` evenly. With it, the
        widths are used as given: a sum narrower than the body is a narrower
        table (place it with `align="CENTER"`), and a sum wider than the body
        raises — a table past the right margin passes every structural check
        and shows only when someone opens the file.
        """
        if not rows:
            raise HwpxBuildError("table() needs at least one row")
        ncol = max(len(r) for r in rows)
        rows = [list(r) + [""] * (ncol - len(r)) for r in rows]
        if widths_mm:
            if len(widths_mm) != ncol:
                raise HwpxBuildError(
                    f"widths_mm has {len(widths_mm)} widths for {ncol} columns")
            widths = [int(round(w * MM)) for w in widths_mm]
            over = sum(widths) - self.text_width
            if over > 0.5 * MM:          # tolerate rounding, not a real overrun
                raise HwpxBuildError(
                    "widths_mm add up to %.1f mm, %.1f mm wider than the body "
                    "(text_width %.1f mm)" % (sum(widths) / MM, over / MM,
                                              self.text_width / MM))
            if over > 0:
                widths[-1] -= over
        else:
            widths = [self.text_width // ncol] * ncol
            widths[-1] += self.text_width - sum(widths)

        grid = self.borderfill(edges=("left", "right", "top", "bottom"))
        head = self.borderfill(face_color=head_fill,
                               edges=("left", "right", "top", "bottom"))
        cell_pp = self.parapr(align="LEFT", line_spacing=130)

        trs = []
        for ri, row in enumerate(rows):
            tcs = []
            for ci, val in enumerate(row):
                bold = header and ri == 0
                body = self._runs(val, size, bold, "#000000", face)
                tcs.append(
                    '<hp:tc name="" header="{hd}" hasMargin="0" protect="0" editable="0" '
                    'dirty="0" borderFillIDRef="{bf}">'
                    '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
                    'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
                    'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">{p}</hp:subList>'
                    '<hp:cellAddr colAddr="{c}" rowAddr="{r}"/>'
                    '<hp:cellSpan colSpan="1" rowSpan="1"/>'
                    '<hp:cellSz width="{w}" height="2000"/>'
                    '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
                    "</hp:tc>".format(hd=1 if bold else 0,
                                      bf=head if bold else grid,
                                      p=self._p(cell_pp, body),
                                      c=ci, r=ri, w=widths[ci]))
            trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")

        self._instid += 1
        tbl = (
            '<hp:tbl id="{i}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
            'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" '
            'repeatHeader="1" rowCnt="{rc}" colCnt="{cc}" cellSpacing="0" '
            'borderFillIDRef="{bf}" noAdjust="0">'
            '<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="2000" heightRelTo="ABSOLUTE" '
            'protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
            'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="0" right="0" top="0" bottom="283"/>'
            # Each hp:tc says hasMargin="0", which means "use the table's
            # inMargin", not its own cellMargin. With this at 0 the text sat
            # against the cell rule. 510/141 is what Hangul gives a new table.
            '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
            "{rows}</hp:tbl>"
        ).format(i=self._instid, rc=len(rows), cc=ncol, bf=grid,
                 w=sum(widths), rows="".join(trs))

        # A table is a control object: it lives inside an hp:run, exactly like
        # a picture. Placed as a direct child of hp:p every XML check still
        # passes, hwpx_text still reads it back — and Hangul drops it on open.
        pp = self.parapr(align=align, next_pt=6)
        cp = self.charpr(size=size, face=face)
        self._body.append(self._p(pp, f'<hp:run charPrIDRef="{cp}">{tbl}</hp:run>'))
        return self

    def image(self, path, width_mm=120, align="CENTER", prev_pt=8):
        """Place a PNG inline, scaled to `width_mm` with its aspect kept."""
        p = Path(path)
        if not p.exists():
            raise HwpxBuildError(f"image not found: {p}")
        px_w, px_h = _png_size(p)
        w = int(round(width_mm * MM))
        h = int(round(w * px_h / px_w))

        item = f"image{len(self._images) + 1}"
        self._images.append((item, p))
        self._instid += 1

        pic = (
            '<hp:pic id="{i}" zOrder="0" numberingType="PICTURE" '
            'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
            'dropcapstyle="None" href="" groupLevel="0" instid="{i}" reverse="0">'
            '<hp:offset x="0" y="0"/>'
            '<hp:orgSz width="{w}" height="{h}"/>'
            '<hp:curSz width="{w}" height="{h}"/>'
            '<hp:flip horizontal="0" vertical="0"/>'
            '<hp:rotationInfo angle="0" centerX="{cx}" centerY="{cy}" rotateimage="1"/>'
            "<hp:renderingInfo>"
            '<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            "</hp:renderingInfo>"
            '<hc:img binaryItemIDRef="{item}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
            '<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/>'
            '<hc:pt2 x="{w}" y="{h}"/><hc:pt3 x="0" y="{h}"/></hp:imgRect>'
            '<hp:imgClip left="0" right="{w}" top="0" bottom="{h}"/>'
            '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            '<hp:imgDim dimwidth="{w}" dimheight="{h}"/>'
            "<hp:effects/>"
            '<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" '
            'protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
            'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            "<hp:shapeComment>그림입니다. 원본 그림의 이름: {name}</hp:shapeComment>"
            "</hp:pic>"
        ).format(i=self._instid, w=w, h=h, cx=w // 2, cy=h // 2,
                 item=item, name=_esc(p.name))

        pp = self.parapr(align=align, prev_pt=prev_pt)
        cp = self.charpr()
        self._body.append(self._p(pp, f'<hp:run charPrIDRef="{cp}">{pic}</hp:run>'))
        return self

    # ------------------------------------------------------------------ save

    def _section_xml(self) -> str:
        if not self._body:
            raise HwpxBuildError("nothing to write — add at least one paragraph")
        # secPr belongs to the FIRST paragraph's first run, not to hs:sec.
        first = self._body[0]
        cp = self.charpr()
        first = first.replace(
            ">", f'><hp:run charPrIDRef="{cp}">{self.secpr}{self.colpr}</hp:run>', 1)
        body = first + "".join(self._body[1:])
        if self._fn_refs:
            body = self._fill_footnotes(body)
        return _SECTION_HEAD + body + "</hs:sec>"

    def _header_xml(self) -> str:
        h = self.header
        for kind, plural in (("charPr", "charProperties"),
                             ("paraPr", "paraProperties"),
                             ("borderFill", "borderFills")):
            blocks = self._new[kind]
            if not blocks:
                continue
            m = re.search(r'<hh:%s itemCnt="(\d+)">' % plural, h)
            if not m:
                raise HwpxBuildError(f"template header has no hh:{plural}")
            n = int(m.group(1))
            h = h.replace(m.group(0), '<hh:%s itemCnt="%d">' % (plural, n + len(blocks)), 1)
            h = h.replace(f"</hh:{plural}>", "".join(blocks) + f"</hh:{plural}>", 1)
        return h

    def _hpf_xml(self) -> str:
        """Register our images in the package manifest, drop the template's."""
        hpf = re.sub(r'<opf:item\b[^>]*href="BinData/[^"]*"[^>]*/>', "", self.hpf)
        items = "".join(
            '<opf:item id="{i}" href="BinData/{i}.png" media-type="image/png" '
            'isEmbeded="1"/>'.format(i=item) for item, _ in self._images)
        return hpf.replace("</opf:manifest>", items + "</opf:manifest>", 1)

    def _preview_text(self, section: str, limit=1000) -> str:
        """Plain text for Preview/PrvText.txt, taken from what we just wrote."""
        parts = re.findall(r"<hp:t>(.*?)</hp:t>", section, re.S)
        txt = "\n".join(
            p.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&amp;", "&")
            for p in parts if p.strip())
        return txt[:limit]

    def save(self, out_path):
        """Write the archive: template entries, minus its BinData, plus ours.

        `Preview/PrvText.txt` is REGENERATED, not dropped. META-INF/container.xml
        declares it a rootfile, so deleting it leaves a dangling reference; and
        carrying the template's over leaves a preview describing a document that
        is no longer in the file — which is what a file manager and Hangul's own
        open dialog show.

        Never ZipFile(..., "a") onto a copy — zipfile appends a second entry
        under the same name instead of replacing it, and a reader may take the
        stale one.
        """
        out = Path(out_path)
        if out.resolve() == self.template.resolve():
            raise HwpxBuildError("refusing to overwrite the template")
        section = self._section_xml()
        header = self._header_xml()
        hpf = self._hpf_xml()
        preview = self._preview_text(section)

        # Whatever META-INF/container.xml calls a rootfile has to be present.
        with zipfile.ZipFile(self.template) as z:
            roots = re.findall(r'full-path="([^"]+)"',
                               z.read("META-INF/container.xml").decode("utf-8"))

        written = set()
        with zipfile.ZipFile(self.template) as zin, \
                zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                n = info.filename
                if n.startswith("BinData/"):
                    continue          # the template's images are not ours
                if n.startswith("Preview/"):
                    if n.endswith("PrvText.txt"):
                        zout.writestr(info, preview.encode("utf-8"))
                        written.add(n)
                    continue          # PrvImage.png depicts the template
                if n == SECTION:
                    zout.writestr(info, section.encode("utf-8"))
                elif n == HEADER:
                    zout.writestr(info, header.encode("utf-8"))
                elif n == HPF:
                    zout.writestr(info, hpf.encode("utf-8"))
                else:
                    zout.writestr(info, zin.read(n))
                written.add(n)
            for item, path in self._images:
                zout.writestr(f"BinData/{item}.png", path.read_bytes())
                written.add(f"BinData/{item}.png")
            for r in roots:
                if r not in written and r.startswith("Preview/"):
                    zout.writestr(r, preview.encode("utf-8"))
        return out

    # ---------------------------------------------------------------- verify

    @staticmethod
    def verify(path, expect_images=None, expect_tables=None):
        """Structural checks. Passing them does NOT mean Hangul will open it.

        LibreOffice cannot load .hwpx, so nothing here can render the result.
        `opens_in_hangul` is None on purpose — a person opens it and says.
        """
        path = Path(path)
        r = {"zip_ok": None, "xml_ok": None, "itemcnt_match": {},
             "dup_ids": {}, "images": None, "tables": None,
             "linesegarray": None, "binrefs_resolve": None,
             "rootfiles_present": None,
             "duplicate_zip_entries": None, "opens_in_hangul": None}
        with zipfile.ZipFile(path) as z:
            r["zip_ok"] = z.testzip() is None
            names = z.namelist()
            r["duplicate_zip_entries"] = len(names) - len(set(names))
            # A rootfile declared in container.xml and absent from the archive
            # is a dangling reference: every XML part still parses.
            roots = re.findall(r'full-path="([^"]+)"',
                               z.read("META-INF/container.xml").decode("utf-8"))
            r["rootfiles_present"] = {"declared": len(roots),
                                      "missing": sorted(set(roots) - set(names))}
            section = z.read(SECTION).decode("utf-8")
            header = z.read(HEADER).decode("utf-8")
            hpf = z.read(HPF).decode("utf-8")
            try:
                ET.fromstring(section.encode())
                ET.fromstring(header.encode())
                ET.fromstring(hpf.encode())
                r["xml_ok"] = True
            except ET.ParseError as e:
                r["xml_ok"] = f"ParseError: {e}"

        for kind, plural in (("charPr", "charProperties"), ("paraPr", "paraProperties"),
                             ("borderFill", "borderFills")):
            blocks = re.findall(r"<hh:%s\b.*?</hh:%s>" % (kind, kind), header, re.S)
            declared = int(re.search(r'<hh:%s itemCnt="(\d+)">' % plural, header).group(1))
            ids = [re.match(r'<hh:%s\b[^>]*?id="(\d+)"' % kind, b).group(1) for b in blocks]
            r["itemcnt_match"][plural] = (declared == len(blocks))
            r["dup_ids"][kind] = len(ids) - len(set(ids))

        r["images"] = len(re.findall(r"<hc:img\b", section))
        r["tables"] = len(re.findall(r"<hp:tbl\b", section))
        r["footnotes"] = len(re.findall(r"<hp:footNote\b", section))
        # A reference slot that save() did not fill is a footnote lost silently.
        r["footnote_slots_unfilled"] = len(_FNSLOT.findall(section))
        # A control object placed as a direct child of hp:p instead of inside an
        # hp:run passes every other check here, reads back from the XML, and is
        # dropped by Hangul on open. Count the ones sitting loose.
        r["controls_outside_run"] = {
            "tbl": len(re.findall(r"<hp:tbl\b", section))
                   - len(re.findall(r"<hp:run[^>]*><hp:tbl\b", section)),
            "pic": len(re.findall(r"<hp:pic\b", section))
                   - len(re.findall(r"<hp:run[^>]*><hp:pic\b", section)),
        }
        # secPr without the colPr Hangul writes beside it: the file opens, and
        # the page size and margins are replaced by Hangul's defaults.
        sp = re.search(r"</hp:secPr>\s*(<hp:ctrl>\s*<hp:colPr\b)?", section)
        r["secpr_with_colpr"] = None if not sp else bool(sp.group(1))
        # A body written here carries no line cache; Hangul builds it on open.
        r["linesegarray"] = len(re.findall(r"<hp:linesegarray>", section))
        refs = set(re.findall(r'binaryItemIDRef="([^"]+)"', section))
        declared = set(re.findall(r'<opf:item id="([^"]+)"[^>]*href="BinData/', hpf))
        r["binrefs_resolve"] = {"referenced": len(refs), "declared": len(declared),
                                "missing": sorted(refs - declared)}
        if expect_images is not None:
            r["images_ok"] = (r["images"] == expect_images)
        if expect_tables is not None:
            r["tables_ok"] = (r["tables"] == expect_tables)
        return r


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit("usage: hwpx_build.py <template.hwpx>   # list usable fonts")
    b = HwpxBuilder(sys.argv[1])
    for face, fid in b.fonts().items():
        print(f"{fid:>3}  {face}")
