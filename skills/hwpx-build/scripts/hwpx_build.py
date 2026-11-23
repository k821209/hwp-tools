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

    b = HwpxBuilder()                    # the shipped blank template; or a path
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
import warnings
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

__all__ = ["HwpxBuilder", "HwpxBuildError", "mono_cols", "pic_xml", "png_size"]

SECTION = "Contents/section0.xml"
HEADER = "Contents/header.xml"
HPF = "Contents/content.hpf"

#: The blank template this skill ships (header written by Hangul, no content).
BASE_TEMPLATE = Path(__file__).resolve().parent.parent / "template" / "base.hwpx"

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

#: A backslash escape, as CommonMark has it: any ASCII punctuation after a
#: backslash is that character, not markup — `\*` is an asterisk, `\[^1]`
#: is text, `\|` a bar inside a table cell.
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")

#: Superscript and subscript, written as the HTML tags: `x<sup>2</sup>`,
#: `H<sub>2</sub>O`. Not pandoc's ^…^ / ~…~ — in Korean text "~" marks a range
#: (1~8권, 2018~2025), and two ranges in a paragraph pair up into a subscript.
_SCRIPT = re.compile(r"<(sup|sub|u)>(.+?)</\1>", re.S)

# An emphasis opener is not followed by a space, a closer not preceded by one
# (CommonMark's flanking rule), so "2 * 3 * 4" stays arithmetic.
_OPEN = re.compile(r"\*\*(?=\S)|(?<!\*)\*(?![\*\s])|<(u|sup|sub)>")
_BOLD_CLOSE = re.compile(r"(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<=[^\s*])\*(?!\*)")
_TAG_LEFT = re.compile(r"</?(?:u|sup|sub)>")


def _nest(text, style):
    """Split `text` into (piece, style) with markup applied, nested.

    style is (bold, italic, underline, script). The earliest opener that has
    a closer is applied, and the text between them is parsed again with the
    style added — so **, *, <u>, <sup> and <sub> combine in any order
    (`**<u>제목<sup>RFP</sup></u>**`). An opener with no closer is text.
    Before, <u>…</u> and friends were taken as one opaque piece: markup
    inside them came out as characters, past every check.
    """
    out, pos = [], 0
    while pos < len(text):
        found = None
        for m in _OPEN.finditer(text, pos):
            tok, tag = m.group(0), m.group(1)
            if tok == "**":
                cm = _BOLD_CLOSE.search(text, m.end() + 1)
                close = (cm.start(), cm.end()) if cm else None
            elif tok == "*":
                cm = _ITALIC.search(text, m.end() + 1)
                close = (cm.start(), cm.end()) if cm else None
            else:                                # <u>, <sup>, <sub>: count nesting
                depth, i, close = 1, m.end(), None
                o_t, c_t = f"<{tag}>", f"</{tag}>"
                while depth:
                    o, c = text.find(o_t, i), text.find(c_t, i)
                    if c < 0:
                        break
                    if 0 <= o < c:
                        depth, i = depth + 1, o + len(o_t)
                    else:
                        depth, i = depth - 1, c + len(c_t)
                        if not depth:
                            close = (c, c + len(c_t))
                if close and close[0] == m.end():
                    close = None                  # nothing between: leave as text
            if close:
                found = (m, tok, tag, close)
                break
        if not found:
            out.append((text[pos:], style))
            break
        m, tok, tag, (cs, ce) = found
        if m.start() > pos:
            out.append((text[pos:m.start()], style))
        b, i, u, sc = style
        inner = (True, i, u, sc) if tok == "**" else (b, True, u, sc) if tok == "*" else \
                (b, i, True, sc) if tag == "u" else (b, i, u, tag)
        out.extend(_nest(text[m.end():cs], inner))
        pos = ce
    return [p for p in out if p[0]]


def _unparsed(pieces):
    """Recognised tags still standing in the text after parsing: an opener
    with no closer, a closer with no opener."""
    return [t for t, _ in pieces for t in _TAG_LEFT.findall(t)]


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


_MARKUP = re.compile(r"\*\*|(?<!\*)\*(?!\*)|`|</?su[bp]>|</?u>")


def _glyph_em(c: str) -> float:
    """Rough advance of one character in a proportional Korean face, in em.

    Hangul, hanja and full-width symbols are one em. Latin in 함초롬바탕 is
    proportional; 0.55 em is an average, not a measurement of any letter.
    The space is half an em, read back from where Hangul broke lines.
    """
    o = ord(c)
    if c == " ":
        return 0.5
    if o < 128:
        return 0.55 if c.isalnum() else 0.35
    return 1.0 if o >= 0x2000 else 0.55


def _cell_lines(text: str, cap_em: float, bold: bool = False) -> int:
    """Lines `text` takes in a cell `cap_em` wide.

    As measured in Hangul (#14): Korean breaks between any two syllables —
    despite breakNonLatinWord="KEEP_WORD" in the paragraph, the name reads the
    other way round — while a run of Latin letters, digits and ASCII
    punctuation moves to the next line whole, and is broken only when it is
    wider than the line by itself (a long URL).

    Checked against 32 table rows read off Hangul: 31 exact, one a line too
    many, none too few — so it errs toward a taller table, not a clipped one.
    """
    if cap_em <= 0:
        return 10 ** 6
    cap = cap_em
    text = _FNREF.sub("¹", _MARKUP.sub("", text))
    k = 1.04 if bold else 1.0
    total = 0
    for para in text.split("\n"):
        units = re.findall(r" +|[!-~]+|[^ !-~]", para)
        lines, used = 1, 0.0
        for u in units:
            w = sum(_glyph_em(c) for c in u) * k
            if u[0] == " ":
                if used:
                    used += w                    # dropped if the line breaks here
                continue
            if used and used + w > cap:
                lines, used = lines + 1, 0.0
            if w > cap:                          # wider than a line on its own
                extra = int(w // cap)
                lines += extra
                w -= extra * cap
            used += w
        total += lines
    return total


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def png_size(path) -> tuple:
    """(width, height) in pixels, from the PNG IHDR. No Pillow needed."""
    return _png_size(Path(path))


def pic_xml(inst: int, item: str, w: int, h: int, name: str) -> str:
    """An inline (treatAsChar) `hp:pic` showing BinData item `item` at w×h HWPUNIT.

    It must go inside an `hp:run` (rule 4). hwpx_edit uses this too, so a
    picture inserted into an existing document is the one built here.
    """
    return (
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
    ).format(i=inst, w=w, h=h, cx=w // 2, cy=h // 2, item=item, name=_esc(name))


def _png_size(path: Path):
    """(width, height) in pixels, from the PNG IHDR. No Pillow needed."""
    with open(path, "rb") as f:
        head = f.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise HwpxBuildError(f"{path.name}: not a PNG (only PNG is supported)")
    return struct.unpack(">II", head[16:24])


class HwpxBuilder:
    """Assemble a section body against a Hangul-made template header."""

    def __init__(self, template=None):
        # A template is not optional in principle (rule 1: the header must be
        # one Hangul wrote). Without one named, use the blank one shipped here.
        self.template = Path(template) if template else BASE_TEMPLATE
        if not self.template.exists():
            raise HwpxBuildError(f"template not found: {self.template}")
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

        self._new = {"charPr": [], "paraPr": [], "borderFill": [], "tabPr": []}
        self._new_fonts: dict = {}    # fontface lang → <hh:font> blocks added
        #: What copy_table() could not carry over exactly, one line each.
        self.copy_notes: list = []
        self._cache: dict = {}
        self._fill_color: dict = {}   # borderFill id → faceColor, to merge fill+edges
        self._body: list[str] = []
        self._images: list[tuple[str, Path]] = []
        self._instid = 1000000
        self._fn_defs: dict = {}      # footnote id → text
        self._fn_refs: list = []      # footnote id per reference, in order written
        self._fn_style = (9.5, None)
        self._pagenum = ""            # hp:pageNum ctrl, written beside secPr
        self._break_next = False      # next paragraph starts a new page
        self._ctrl_next: list = []    # controls the next paragraph carries
        self._space_before: dict = {} # paraPr id → HWPUNIT it wants above it
        #: face asked for → the faces actually used in its place, for every
        #: face the template lacks. Hangul would substitute silently and so would this builder;
        #: this is the record that it happened.
        self.font_fallbacks: dict = {}
        #: Text in which a <u>/<sup>/<sub> had no partner and was printed as
        #: characters — a typo that every structural check passes.
        self.unparsed_markup: list = []

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
        if face in self._fonts:
            return self._fonts[face]
        used = next((f for f, i in self._fonts.items() if i == default), f"font id {default}")
        self._note_fallback(face, used)
        return default

    def _note_fallback(self, face, used):
        seen = self.font_fallbacks.setdefault(face, [])
        if used in seen:
            return
        seen.append(used)
        warnings.warn(f"font {face!r} is not in the template; {used!r} is used instead "
                      f"(see check_template())", stacklevel=3)

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
               face=None, fill=None, shade=None, script=None, underline=False) -> str:
        """Character properties id. Cloned from the template's first charPr.

        `size` in points, `face` a font name from `fonts()`, `fill` a
        borderFill id for character borders/background, `shade` a colour for
        the character shade (Hangul's 글자 모양 → 음영 색), `script` "sup" or
        "sub" for 위 첨자 / 아래 첨자 (`<hh:supscript/>` / `<hh:subscript/>`,
        last in the charPr, where Hangul writes them), `underline` True for a
        solid underline (`hh:underline type="BOTTOM"`, Hangul's 밑줄).
        """
        if script not in (None, "sup", "sub"):
            raise HwpxBuildError('script is "sup", "sub" or None')
        key = ("c", size, bold, italic, color.upper(), face, fill, shade, script, underline)
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
            inner = re.sub(r'(<hh:underline\b[^>]*\btype=")\w+(" shape=")\w+',
                           r"\g<1>%s\g<2>SOLID" % ("BOTTOM" if underline else "NONE"),
                           inner, count=1)
            inner = inner.replace("<hh:supscript/>", "").replace("<hh:subscript/>", "")
            if script:
                inner += "<hh:%sscript/>" % script
            return tag, inner

        cid = self._clone("charPr", self._base_charpr, mutate)
        self._cache[key] = cid
        return cid

    def parapr(self, align="JUSTIFY", left_mm=0.0, right_mm=0.0, first_mm=0.0,
               prev_pt=0.0, next_pt=0.0, line_spacing=160, fill=None,
               border_edges=(), border_offsets=(283, 283, 142, 142),
               ignore_margin=True, auto_spacing=None, keep_with_next=False) -> str:
        """Paragraph properties id.

        `keep_with_next` keeps the paragraph on the same page as the one after
        it (문단 모양 → 다음 문단과 함께) — for a heading, so it is never the
        last line of a page with its text on the next.

        `first_mm` negative gives a hanging indent: the first line starts at
        `left_mm + first_mm` and the lines after it at `left_mm`, as in Word.
        Hangul encodes 내어쓰기 the other way (left margin = where the first
        line starts, the rest |intent| further in), and this translates. `fill` shades the paragraph,
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
               ignore_margin, auto_spacing, keep_with_next)
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
        ).format(i=int(round(first_mm * MM)),
                 # Hangul reads a negative intent as "first line at the left
                 # margin, the others |intent| further in" (measured: 내어쓰기),
                 # not as Word's "first line pulled out". Callers here mean
                 # Word's — first line at left+first, the rest at left — so the
                 # left margin Hangul is given is where the first line starts.
                 l=int(round((left_mm + min(first_mm, 0.0)) * MM)),
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
            inner = re.sub(r'(<hh:breakSetting\b[^>]*\bkeepWithNext=")[01]',
                           r"\g<1>" + ("1" if keep_with_next else "0"), inner)
            if auto_spacing is not None:
                v = "1" if auto_spacing else "0"
                inner = re.sub(r"<hh:autoSpacing\b[^>]*/>",
                               f'<hh:autoSpacing eAsianEng="{v}" eAsianNum="{v}"/>', inner)
            return tag, inner

        pid = self._clone("paraPr", self._base_parapr, mutate)
        self._cache[key] = pid
        # What this paragraph wants above it, shading included: a shaded box
        # reaches `top offset` above the text. An anchored table ignores the
        # paragraph's own space-before (see _table_gaps), so it is kept here.
        shaded = fill is not None or bool(border_edges)
        self._space_before[pid] = int(round(prev_pt * 100)) + (
            int(border_offsets[2]) if shaded else 0)
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

    _PAGENUM_POS = ("BOTTOM_CENTER", "BOTTOM_LEFT", "BOTTOM_RIGHT", "TOP_CENTER",
                    "TOP_LEFT", "TOP_RIGHT", "OUTSIDE_BOTTOM", "OUTSIDE_TOP",
                    "INSIDE_BOTTOM", "INSIDE_TOP")
    _PAGENUM_FMT = ("DIGIT", "CIRCLED_DIGIT", "ROMAN_CAPITAL", "ROMAN_SMALL",
                    "LATIN_CAPITAL", "LATIN_SMALL")

    def page_number(self, pos="BOTTOM_CENTER", fmt="DIGIT", side_char="-",
                    start=None):
        """Number the pages: "- 1 -" centred at the foot by default.

        Hangul's own documents do this with one `hp:pageNum` control in the
        first paragraph (쪽 → 쪽 번호 매기기) — no master page, header or
        footer needed — and that is what this writes. `side_char` is drawn
        either side of the number ("" for a bare number). `start` restarts
        the count at that page number; None continues it (1 for a new file).
        """
        if pos not in self._PAGENUM_POS:
            raise HwpxBuildError(f"pos must be one of {self._PAGENUM_POS}")
        if fmt not in self._PAGENUM_FMT:
            raise HwpxBuildError(f"fmt must be one of {self._PAGENUM_FMT}")
        self._pagenum = ('<hp:ctrl><hp:pageNum pos="%s" formatType="%s" sideChar="%s"/>'
                         '</hp:ctrl>' % (pos, fmt, _esc(side_char)))
        if start is not None:
            self.secpr = re.sub(r'(<hp:startNum\b[^>]*\bpage=")\d+', r"\g<1>%d" % int(start),
                                self.secpr, count=1)
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

        def hold_escape(m):
            ph = next(free, None)
            if ph is None:
                return m.group(1)
            spans[ph] = ("lit", m.group(1))
            return ph

        held = _CODE.sub(hold, text) if "`" in text else text
        # Escapes after code spans (inside one a backslash is literal), before
        # footnotes and emphasis (so "\[^1]" and "\*" stay text).
        if "\\" in held:
            held = _ESCAPE.sub(hold_escape, held)

        if notes and "[^" in held:
            held = _FNREF.sub(hold_note, held)

        pieces = _nest(held, (bold, False, False, None))
        left = _unparsed(pieces)
        if left:
            self.unparsed_markup.append(text)
            warnings.warn(f"markup left as text (no partner): {left} in {text[:60]!r}",
                          stacklevel=3)

        out = []          # (text, style, kind) — style is (bold, italic, underline, script)
        for t, st in pieces:
            last = 0
            for k, ch in enumerate(t) if spans else ():
                if ch in spans:
                    if k > last:
                        out.append((t[last:k], st, None))
                    kind, val = spans[ch]
                    out.append((val, st, kind))
                    last = k + 1
            if last < len(t):
                out.append((t[last:], st, None))
        if not out:
            out = [("", (bold, False, False, None), None)]

        cface, cshade, ccolor = getattr(self, "_code_style", ("굴림체", "#F2F2F2", None))
        if cface and cface not in self.fonts():
            self._note_fallback(cface, face or "the surrounding face")
            cface = None          # rule 3: never name a face the header lacks

        def run(t, st, kind):
            b, i, u, sc = st
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
                                  face=cface or face, shade=cshade, script=sc, underline=u)
            else:                                # plain text, or an escaped character
                cid = self.charpr(size=size, bold=b, italic=i, color=color, face=face,
                                  script=sc, underline=u)
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

    # ------------------------------------------------------------- pagination

    def page_break(self):
        """Start the next block on a new page (the paragraph's pageBreak="1")."""
        self._break_next = True
        return self

    def hide_page_number(self):
        """No page number on the page the next block lands on — a cover, a TOC.

        Hangul's own documents do it with an `hp:pageHiding` control in a
        paragraph on that page; this puts one in the next paragraph written.
        """
        self._ctrl_next.append(
            '<hp:ctrl><hp:pageHiding hideHeader="0" hideFooter="0" hideMasterPage="0" '
            'hideBorder="0" hideFill="0" hidePageNum="1"/></hp:ctrl>')
        return self

    def restart_page_number(self, num=1):
        """Number the page the next block lands on `num`, and count on from it.

        An `hp:newNum` control, as Hangul writes after a cover so the body
        starts at 1.
        """
        self._ctrl_next.append(
            '<hp:ctrl><hp:newNum num="%d" numType="PAGE"/></hp:ctrl>' % int(num))
        return self

    def _p(self, para_pr, body, style="0", inner=False) -> str:
        # No hp:linesegarray is written. It is a layout cache keyed to text that
        # does not exist yet; Hangul builds it on open. Writing a guess is the
        # single most reliable way to have the file refused.
        # A paragraph inside a table cell is built before the paragraph that
        # holds the table; it must not take the pending break or controls.
        if inner:
            return ('<hp:p id="0" paraPrIDRef="%s" styleIDRef="%s" pageBreak="0" '
                    'columnBreak="0" merged="0">%s</hp:p>' % (para_pr, style, body))
        brk = "1" if self._break_next else "0"
        if self._ctrl_next:
            body = ('<hp:run charPrIDRef="%s">%s</hp:run>'
                    % (self.charpr(), "".join(self._ctrl_next))) + body
        self._break_next, self._ctrl_next = False, []
        return ('<hp:p id="0" paraPrIDRef="%s" styleIDRef="%s" pageBreak="%s" '
                'columnBreak="0" merged="0">%s</hp:p>' % (para_pr, style, brk, body))

    # ---------------------------------------------------------------- blocks

    def para(self, text="", size=13.0, bold=False, color="#000000", face=None,
             align="JUSTIFY", left_mm=0.0, first_mm=0.0, prev_pt=0.0, next_pt=0.0,
             line_spacing=160, fill=None, border_edges=(),
             border_offsets=(283, 283, 142, 142), ignore_margin=True,
             keep_with_next=False):
        pp = self.parapr(align=align, left_mm=left_mm, first_mm=first_mm,
                         prev_pt=prev_pt, next_pt=next_pt, line_spacing=line_spacing,
                         fill=fill, border_edges=border_edges,
                         border_offsets=border_offsets,
                         ignore_margin=ignore_margin, keep_with_next=keep_with_next)
        self._body.append(self._p(pp, self._runs(text, size, bold, color, face)))
        return self

    def heading(self, level, text, face=None):
        size = {1: 17.0, 2: 14.0, 3: 13.0}.get(level, 13.0)
        self.para(text, size=size, bold=True, face=face, align="LEFT",
                  prev_pt={1: 6, 2: 16, 3: 10}.get(level, 10),
                  next_pt={1: 14, 2: 6, 3: 3}.get(level, 3), keep_with_next=True)
        return self

    def bullet(self, text, size=13.0, face=None, marker="· "):
        self.para(marker + text, size=size, face=face, left_mm=4, first_mm=-3)
        return self

    def code(self, lines, size=10.5, face="굴림체", fill_color="#F2F2F2",
             lang=None, theme=None, prev_pt=4.0, next_pt=8.0):
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
        # Space above the first line and below the last, none between lines:
        # the block's own paragraphs carry it, so whatever follows — text or
        # an anchored table, which honours the space-after of the paragraph
        # before it — does not sit on the shading.
        def pp_at(k, n):
            return self.parapr(align="LEFT", left_mm=3, line_spacing=130, fill=bf,
                               auto_spacing=False, prev_pt=prev_pt if k == 0 else 0,
                               next_pt=next_pt if k == n - 1 else 0)

        if lex is None or lang == "plain":
            for k, ln in enumerate(lines):
                cp = self.charpr(size=size, face=face)
                self._body.append(self._p(
                    pp_at(k, len(lines)), '<hp:run charPrIDRef="%s"><hp:t>%s</hp:t></hp:run>'
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

        for k, row in enumerate(rows):
            runs = "".join(
                '<hp:run charPrIDRef="%s"><hp:t>%s</hp:t></hp:run>'
                % (self.charpr(size=size, face=face,
                               color=colors.get(kind, colors["text"])), _esc(txt))
                for txt, kind in row)
            if not runs:   # a blank line still needs a run to hold the shading
                runs = ('<hp:run charPrIDRef="%s"><hp:t></hp:t></hp:run>'
                        % self.charpr(size=size, face=face))
            self._body.append(self._p(pp_at(k, len(rows)), runs))
        return self

    def quote(self, text, size=11.5, face=None, fill_color="#FFF6E5"):
        bf = self.borderfill(face_color=fill_color)
        self.para(text, size=size, face=face, left_mm=5, prev_pt=6, next_pt=6, fill=bf)
        return self

    _CELL_PAD = 1020        # inMargin left + right, HWPUNIT

    def _table_lines(self, rows, widths, size, header=True):
        """Estimated height of a table in lines: per row, its tallest cell."""
        em = size * 100.0                      # 1 pt = 100 HWPUNIT
        return sum(max(_cell_lines(v, (w - self._CELL_PAD) / em, header and ri == 0)
                       for v, w in zip(row, widths))
                   for ri, row in enumerate(rows))

    def _auto_widths(self, rows, size, header=True):
        """Column widths (HWPUNIT, summing to text_width) that keep the table short.

        Each column gets at least the width its header needs to stay on one
        line, or its longest word if that is narrower — a header broken a
        syllable per line cannot be read. What is left starts out shared in
        proportion to each column's longest cell, and then 1 mm at a time is
        moved from one column to another while that lowers the estimated
        total of lines. The estimate is a model of Hangul's layout, not
        Hangul's layout; its errors fall where it guesses glyph widths.
        """
        total, ncol = self.text_width, len(rows[0])
        if ncol == 1:
            return [total]
        em = size * 100.0
        pad = self._CELL_PAD

        def need(text, bold):                  # em width of text on one line
            return sum(_glyph_em(c) for c in _MARKUP.sub("", text)) * (1.04 if bold else 1)

        floors = []
        for j in range(ncol):
            head = need(rows[0][j], True) if header else 0
            longest = max((need(w, False) for r in rows[1 if header else 0:]
                           for w in r[j].split()), default=0)
            floors.append(int(max(head, min(longest, 6), 2) * em) + pad)
        if sum(floors) > total:                 # cannot honour them all: scale
            floors = [f * total // sum(floors) for f in floors]
        free = total - sum(floors)
        weight = [max(need(r[j], False) for r in rows) or 1 for j in range(ncol)]
        # A column whose body is empty is a column to be written in (a form's
        # "계획" or "충족 근거"). Sized by its header alone it came out the
        # narrowest, the opposite of what it is for; it gets the widest
        # filled column's weight instead.
        body = rows[1:] if header else rows
        filled = [w for j, w in enumerate(weight) if any(str(r[j]).strip() for r in body)]
        for j in range(ncol):
            if body and not any(str(r[j]).strip() for r in body) and filled:
                weight[j] = max(filled)
        w = [floors[j] + int(free * weight[j] / sum(weight)) for j in range(ncol)]
        w[-1] += total - sum(w)

        best = self._table_lines(rows, w, size, header)
        for step in (int(4 * MM), int(MM), int(MM / 4)):
            improved = True
            while improved:
                improved = False
                for a in range(ncol):
                    for b_ in range(ncol):
                        if a == b_ or w[a] - step < floors[a]:
                            continue
                        w[a] -= step; w[b_] += step
                        t = self._table_lines(rows, w, size, header)
                        if t < best:
                            best, improved = t, True
                        else:
                            w[a] += step; w[b_] -= step
        return w

    def table(self, rows, widths_mm=None, header=True, size=10.5, face=None,
              head_fill="#EAEAEA", align="LEFT", page_break="TABLE", inline=False):
        """A grid table. `rows` is a list of lists of strings.

        Without `widths_mm` (or with "auto") the body width is shared out to
        make the table as short as it can: columns are sized to their content
        so that the sum over rows of each row's tallest cell, in lines, is as
        small as a local search finds — see `_auto_widths`. "equal" splits it
        evenly, as before. With a list of mm the widths are used as given: a sum narrower than the body is a narrower
        table (place it with `align="CENTER"`), and a sum wider than the body
        raises — a table past the right margin passes every structural check
        and shows only when someone opens the file.

        `page_break` is what Hangul does at a page boundary (표 → 쪽 경계에서),
        as measured in Hangul — the names read the other way round:
        "TABLE" (나눔) breaks only between rows, so no row is split — the
        default; "CELL" (셀 단위로 나눔) breaks inside a cell, so a row's
        lines land on two pages; "NONE" (나누지 않음) moves the whole table to
        the next page. The header row repeats on each page.

        `inline=True` sets the table "as a character" (글자처럼 취급). Hangul
        never splits such a table: it moves whole to the next page whatever
        `page_break` says, and a table taller than a page has nowhere to go.
        The default is a table anchored to its paragraph and flowing with the
        text, which is the one `page_break` applies to.
        """
        if page_break not in ("TABLE", "CELL", "NONE"):
            raise HwpxBuildError('page_break must be "TABLE", "CELL" or "NONE"')
        if not rows:
            raise HwpxBuildError("table() needs at least one row")
        ncol = max(len(r) for r in rows)
        rows = [list(r) + [""] * (ncol - len(r)) for r in rows]
        if widths_mm is None or widths_mm == "auto":
            widths = self._auto_widths(rows, size, header)
        elif widths_mm == "equal":
            widths = [self.text_width // ncol] * ncol
            widths[-1] += self.text_width - sum(widths)
        elif isinstance(widths_mm, str):
            raise HwpxBuildError('widths_mm is a list of mm, "auto" or "equal"')
        else:
            widths = self._widths_from_mm(widths_mm, ncol)

        grid = self.borderfill(edges=("left", "right", "top", "bottom"))
        head = self.borderfill(face_color=head_fill,
                               edges=("left", "right", "top", "bottom"))
        cell_pp = self.parapr(align="LEFT", line_spacing=130)

        trs = []
        for ri, row in enumerate(rows):
            bold = header and ri == 0
            trs.append("<hp:tr>" + "".join(
                self._tc([(ln, cell_pp, size, bold) for ln in str(val).split("\n")],
                         ri, ci, 1, 1, widths[ci], 2000,
                         head if bold else grid, face, header=bold)
                for ci, val in enumerate(row)) + "</hp:tr>")
        tbl = self._tbl(trs, len(rows), ncol, sum(widths), grid, page_break, inline,
                        align, repeat_header=True)
        return self._place_table(tbl, align, size, face)

    # ------------------------------------------------------------ copy_table

    @staticmethod
    def _top_tables(section):
        """(start, end) of each top-level hp:tbl — a table in a cell is part of
        its outer table, so a non-greedy regex would cut the outer one short."""
        out, depth, i, start = [], 0, 0, 0
        while True:
            o, c = section.find("<hp:tbl ", i), section.find("</hp:tbl>", i)
            if c < 0:
                return out
            if 0 <= o < c:
                if depth == 0:
                    start = o
                depth, i = depth + 1, o + 8
            else:
                depth, i = depth - 1, c + 9
                if depth == 0:
                    out.append((start, i))

    def _fontfaces(self):
        """lang → {face: id} for the target header, fonts added by copies included."""
        out = {}
        for lang, body in re.findall(r'<hh:fontface lang="(\w+)" fontCnt="\d+">(.*?)</hh:fontface>',
                                     self.header, re.S):
            out[lang] = {face: fid for fid, face in re.findall(r'<hh:font id="(\d+)" face="([^"]+)"', body)}
            for blk in self._new_fonts.get(lang, []):
                fid, face = re.search(r'<hh:font id="(\d+)" face="([^"]+)"', blk).groups()
                out[lang][face] = fid
        return out

    def copy_table(self, src, index=0, containing=None, align="LEFT"):
        """Copy a table from another Hangul .hwpx — a form, as its author drew it.

        `index` counts the top-level tables of `src` from 0; `containing`
        picks the first one whose text holds that string instead. The table's
        XML is taken whole, and everything it points into its own header is
        carried over under new ids: border fills, character shapes (fonts
        matched by name per language, a face the target lacks copied in),
        paragraph shapes (with their tab settings), styles matched by name.
        Pasted with the source's ids, the table would point at whatever the
        target happens to hold under those numbers — other borders, other
        fonts — and every structural check would still pass.

        Not carried: paragraph numbering inside the table (set to none, and
        noted), styles the target lacks by name (바탕글 instead, noted), and
        pictures (raises). The source's line cache is dropped. A table wider
        than the target's body raises. `copy_notes` lists what was changed.
        Fill the copied form afterwards with hwpx-edit's fill_cell().
        """
        with zipfile.ZipFile(src) as z:
            sh = z.read(HEADER).decode("utf-8")
            secs = [z.read(n).decode("utf-8") for n in sorted(
                n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n))]
        tables = [sec[a:b] for sec in secs for a, b in self._top_tables(sec)]
        if containing is not None:
            pick = [t for t in tables if containing in "".join(re.findall(r"<hp:t>([^<]*)", t))]
            if not pick:
                raise HwpxBuildError(f"no table in {Path(src).name} contains {containing!r}")
            tbl = pick[0]
        else:
            if not 0 <= index < len(tables):
                raise HwpxBuildError(f"{Path(src).name} has {len(tables)} tables; no index {index}")
            tbl = tables[index]
        if "binaryItemIDRef" in tbl:
            raise HwpxBuildError("the table holds a picture; copying pictures is not supported")
        tbl = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", tbl, flags=re.S)
        w = int(re.search(r'<hp:sz width="(\d+)"', tbl).group(1))
        if w > self.text_width + 0.5 * MM:
            raise HwpxBuildError(
                "the table is %.1f mm wide and the body %.1f mm — widen the body with "
                "page() first" % (w / MM, self.text_width / MM))

        def block(kind, i):
            # An entry is either self-closing (<hh:tabPr id="0" .../>) or has a
            # closing tag; searching straight for "</hh:kind>" from a
            # self-closing one runs on into the next entries.
            m = re.search(r'<hh:%s\b[^>]*?\bid="%s"[^>]*?(/?)>' % (kind, i), sh)
            if not m:
                raise HwpxBuildError(f"{kind} {i} not in the source header")
            if m.group(1):
                return m.group(0)
            end = sh.index(f"</hh:{kind}>", m.end()) + len(f"</hh:{kind}>")
            return sh[m.start():end]

        def renumber(blk, kind):
            new = self._next_id(kind)
            blk = re.sub(r'\bid="\d+"', f'id="{new}"', blk, count=1)
            self._new[kind].append(blk)
            return new

        maps = {"borderFill": {}, "tabPr": {}, "charPr": {}, "paraPr": {}}

        def note(msg):
            if msg not in self.copy_notes:
                self.copy_notes.append(msg)

        def bf(i):
            if i not in maps["borderFill"]:
                maps["borderFill"][i] = renumber(block("borderFill", i), "borderFill")
            return maps["borderFill"][i]

        def tab(i):
            if i not in maps["tabPr"]:
                maps["tabPr"][i] = renumber(block("tabPr", i), "tabPr")
            return maps["tabPr"][i]

        src_fonts = {lang: dict(re.findall(r'<hh:font id="(\d+)" face="([^"]+)"', body))
                     for lang, body in re.findall(
                         r'<hh:fontface lang="(\w+)" fontCnt="\d+">(.*?)</hh:fontface>', sh, re.S)}
        langs = {"hangul": "HANGUL", "latin": "LATIN", "hanja": "HANJA",
                 "japanese": "JAPANESE", "other": "OTHER", "symbol": "SYMBOL", "user": "USER"}

        def font(lang, i):
            face = src_fonts[lang][i]
            have = self._fontfaces().get(lang, {})
            if face in have:
                return have[face]
            body = re.search(r'<hh:fontface lang="%s".*?</hh:fontface>' % lang, sh, re.S).group(0)
            blk = re.search(r'<hh:font id="%s" .*?</hh:font>' % i, body, re.S).group(0)
            new = str(max([int(x) for x in have.values()] + [-1]) + 1)
            self._new_fonts.setdefault(lang, []).append(re.sub(r'id="\d+"', f'id="{new}"', blk, count=1))
            if lang == "HANGUL":
                self._fonts[face] = new
            note(f"font {face!r} ({lang}) added to the header")
            return new

        def cp(i):
            if i not in maps["charPr"]:
                blk = block("charPr", i)
                blk = re.sub(r'borderFillIDRef="(\d+)"', lambda m: f'borderFillIDRef="{bf(m.group(1))}"', blk)
                blk = re.sub(r'<hh:fontRef ([^>]*)/>', lambda m: "<hh:fontRef %s/>" % " ".join(
                    f'{k}="{font(langs[k], v)}"' for k, v in re.findall(r'(\w+)="(\d+)"', m.group(1))), blk)
                maps["charPr"][i] = renumber(blk, "charPr")
            return maps["charPr"][i]

        def pp(i):
            if i not in maps["paraPr"]:
                blk = block("paraPr", i)
                blk = re.sub(r'tabPrIDRef="(\d+)"', lambda m: f'tabPrIDRef="{tab(m.group(1))}"', blk)
                blk = re.sub(r'borderFillIDRef="(\d+)"', lambda m: f'borderFillIDRef="{bf(m.group(1))}"', blk)
                if re.search(r'<hh:heading type="(?!NONE)', blk):
                    blk = re.sub(r'<hh:heading type="\w+" idRef="\d+"', '<hh:heading type="NONE" idRef="0"', blk)
                    note(f"paragraph numbering in paraPr {i} not carried; set to none")
                maps["paraPr"][i] = renumber(blk, "paraPr")
            return maps["paraPr"][i]

        src_styles = dict(re.findall(r'<hh:style id="(\d+)" type="PARA" name="([^"]*)"', sh))
        dst_styles = {name: sid for sid, name in
                      re.findall(r'<hh:style id="(\d+)" type="PARA" name="([^"]*)"', self.header)}

        def style(i):
            name = src_styles.get(i)
            if name in dst_styles:
                return dst_styles[name]
            note(f"style {name!r} not in the target; 바탕글 used")
            return "0"

        tbl = re.sub(r'borderFillIDRef="(\d+)"', lambda m: f'borderFillIDRef="{bf(m.group(1))}"', tbl)
        tbl = re.sub(r'charPrIDRef="(\d+)"', lambda m: f'charPrIDRef="{cp(m.group(1))}"', tbl)
        tbl = re.sub(r'paraPrIDRef="(\d+)"', lambda m: f'paraPrIDRef="{pp(m.group(1))}"', tbl)
        tbl = re.sub(r'styleIDRef="(\d+)"', lambda m: f'styleIDRef="{style(m.group(1))}"', tbl)

        def fresh(m):                          # new object ids for every table in it
            self._instid += 1
            return re.sub(r'\b(id|instid|instId)="\d+"', lambda a: f'{a.group(1)}="{self._instid}"', m.group(0))
        tbl = re.sub(r"<hp:tbl\b[^>]*>", fresh, tbl)
        # Anchored, it takes its bottom gap from the next paragraph, as ours do.
        head_end = tbl.index("<hp:tr>")
        if re.search(r'treatAsChar="0"', tbl[:head_end]):
            tbl = re.sub(r'(<hp:outMargin [^>]*bottom=")\d+', r"\g<1>@@GAP@@", tbl[:head_end], count=1) + tbl[head_end:]
        return self._place_table(tbl, align, 10.5, None)

    def _widths_from_mm(self, widths_mm, ncol):
        """HWPUNIT widths from a list of mm: as given, never wider than the body."""
        if len(widths_mm) != ncol:
            raise HwpxBuildError(f"widths_mm has {len(widths_mm)} widths for {ncol} columns")
        widths = [int(round(w * MM)) for w in widths_mm]
        over = sum(widths) - self.text_width
        if over > 0.5 * MM:          # tolerate rounding, not a real overrun
            raise HwpxBuildError(
                "widths_mm add up to %.1f mm, %.1f mm wider than the body "
                "(text_width %.1f mm)" % (sum(widths) / MM, over / MM,
                                          self.text_width / MM))
        if over > 0:
            widths[-1] -= over
        return widths

    _EDGES = ("left", "right", "top", "bottom")

    #: Line heads that hang: the second line of "○ …" starts under the text.
    BULLETS = "○◦●•▪■□◇◆※*-·ㅇ◎▶▷➢✓☐☑①②③④⑤⑥⑦⑧⑨⑩"

    def _cell_lines(self, c, size):
        """(text, paraPr, size, bold) per line of a grid_table cell."""
        base = {"align": c.get("align", "LEFT"), "size": c.get("size", size),
                "bold": c.get("bold", False), "left_mm": 0.0, "first_mm": 0.0,
                "prev_pt": 0.0, "next_pt": 0.0}
        raw = c.get("lines")
        if raw is None:
            raw = str(c.get("text", "")).split("\n")
        out = []
        for ln in raw:
            spec = dict(base, **(ln if isinstance(ln, dict) else {"t": ln}))
            t = str(spec.get("t", ""))
            if c.get("line_style") == "bullets" and not isinstance(ln, dict) or \
                    (isinstance(ln, dict) and spec.get("hang") == "auto"):
                t, spec = self._hang(t, spec)
            pp = self.parapr(align=spec["align"], line_spacing=130,
                             left_mm=spec["left_mm"], first_mm=spec["first_mm"],
                             prev_pt=spec["prev_pt"], next_pt=spec["next_pt"])
            out.append((t, pp, spec["size"], spec["bold"]))
        return out

    def _hang(self, t, spec):
        """Indent a line by its leading spaces, and hang it after a bullet: the
        width of the head ("○ ", "※ ", "  * ") becomes the left margin and a
        negative first-line indent, so a wrapped second line starts under the
        text, as in a form."""
        lead = len(t) - len(t.lstrip(" "))
        body = t[lead:]
        head = ""
        if body and body[0] in self.BULLETS:
            j = 1
            while j < len(body) and body[j] == " ":
                j += 1
            head = body[:j]
        pt = 0.3528 * spec["size"]               # 1 pt in mm, times the size
        indent = lead * _glyph_em(" ") * pt
        hang = sum(_glyph_em(ch) for ch in head) * pt
        spec = dict(spec, left_mm=spec["left_mm"] + indent + hang,
                    first_mm=spec["first_mm"] - hang)
        return body, spec

    def grid_table(self, widths_mm, cells, size=10.5, face=None, align="LEFT",
                   page_break="TABLE", inline=False, repeat_header=False,
                   row_height_mm=None, row_heights_mm=None):
        """A table with merged cells, laid out on a grid — the shape of a form.

        `widths_mm` gives every grid column (the columns a merged cell spans
        add up). `cells` is a list of dicts, one per cell that is written:

            {"r": 0, "c": 0, "text": "세부사업명", "rs": 2, "cs": 1,
             "fill": "#E8EDF3", "align": "CENTER", "bold": True, "size": 10,
             "edges": ("left", "right", "top", "bottom"), "valign": "CENTER"}

        Only r and c are required. Every grid position must be covered by
        exactly one cell, and every row must start at least one cell of its
        own — checked before anything is written, because a wrong merge
        leaves well-formed XML that every structural check passes and that
        only shows as a broken table in Hangul. (No Hangul-made table here has
        a row with no cell of its own; such a row is two rows merged.)

        `edges` are the borders the cell draws; a neighbour still draws its
        own, so a borderless gap needs its neighbours' edges too. The header
        row is not repeated on a new page unless `repeat_header` — a form's
        first row is a field, not a heading.

        Lines in a cell are paragraphs, each styleable: `"lines"` instead of
        `"text"`, each a string or a dict {"t", "align", "size", "bold",
        "left_mm", "first_mm", "prev_pt", "next_pt", "hang": "auto"}.
        `"line_style": "bullets"` hangs every line that opens with a head in
        BULLETS (○ ※ □ - * …) so its second line starts under its text, and
        indents it by its leading spaces.

        Rows are 7 mm unless `row_height_mm` (all) or `row_heights_mm` (per
        row) say otherwise. It is a minimum: Hangul grows a row to its text.
        """
        if page_break not in ("TABLE", "CELL", "NONE"):
            raise HwpxBuildError('page_break must be "TABLE", "CELL" or "NONE"')
        ncol = len(widths_mm)
        widths = self._widths_from_mm(widths_mm, ncol)
        cells = [dict(c) for c in cells]
        if not cells:
            raise HwpxBuildError("grid_table() needs at least one cell")
        nrow = max(c["r"] + c.get("rs", 1) for c in cells)
        owner = {}
        problems = []
        for k, c in enumerate(cells):
            r, cc, rs, cs = c["r"], c["c"], c.get("rs", 1), c.get("cs", 1)
            if rs < 1 or cs < 1 or r < 0 or cc < 0 or cc + cs > ncol:
                problems.append(f"cell at ({r},{cc}) span {rs}x{cs} does not fit {ncol} columns")
                continue
            for y in range(r, r + rs):
                for x in range(cc, cc + cs):
                    if (y, x) in owner:
                        o = cells[owner[(y, x)]]
                        problems.append(f"({y},{x}) covered by the cells at "
                                        f"({o['r']},{o['c']}) and ({r},{cc})")
                    owner[(y, x)] = k
        holes = [(y, x) for y in range(nrow) for x in range(ncol) if (y, x) not in owner]
        if holes:
            problems.append(f"{len(holes)} grid position(s) not covered: {holes[:8]}")
        starts = {c["r"] for c in cells}
        empty = [y for y in range(nrow) if y not in starts]
        if empty:
            problems.append(f"row(s) {empty} start no cell of their own — merge them "
                            "into the row above")
        if problems:
            raise HwpxBuildError("grid_table: " + "; ".join(problems))

        if row_heights_mm is not None and len(row_heights_mm) != nrow:
            raise HwpxBuildError(f"row_heights_mm has {len(row_heights_mm)} heights for {nrow} rows")
        heights = ([int(round(h * MM)) for h in row_heights_mm] if row_heights_mm else
                   [int(round(row_height_mm * MM)) if row_height_mm else 2000] * nrow)
        table_bf = self.borderfill()
        trs = []
        for y in range(nrow):
            tcs = []
            for c in sorted((c for c in cells if c["r"] == y), key=lambda c: c["c"]):
                rs, cs = c.get("rs", 1), c.get("cs", 1)
                edges = tuple(c.get("edges", self._EDGES))
                bf = self.borderfill(face_color=c.get("fill"), edges=edges)
                tcs.append(self._tc(self._cell_lines(c, size), y, c["c"], rs, cs,
                                    sum(widths[c["c"]:c["c"] + cs]), sum(heights[y:y + rs]),
                                    bf, face, header=repeat_header and y == 0,
                                    valign=c.get("valign", "CENTER")))
            trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
        tbl = self._tbl(trs, nrow, ncol, sum(widths), table_bf, page_break, inline,
                        align, repeat_header=repeat_header)
        return self._place_table(tbl, align, size, face)

    def _tc(self, lines, r, c, rs, cs, width, height, border_fill, face,
            header=False, valign="CENTER"):
        """One hp:tc holding `lines`: (text, paraPr id, size, bold) per line.

        A cell holding several lines is one paragraph per line: a single
        paragraph cannot break where the author meant it to, since hp:t has
        no line break — "\n" would print as nothing and the lines would run
        together with no separator at all. One paragraph per line is also
        what lets each line have its own indent and size."""
        cell = "".join(
            self._p(pp, self._runs(t, sz, bd, "#000000", face), inner=True)
            for t, pp, sz, bd in lines)
        return ('<hp:tc name="" header="{hd}" hasMargin="0" protect="0" editable="0" '
                'dirty="0" borderFillIDRef="{bf}">'
                '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
                'vertAlign="{va}" linkListIDRef="0" linkListNextIDRef="0" '
                'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">{p}</hp:subList>'
                '<hp:cellAddr colAddr="{c}" rowAddr="{r}"/>'
                '<hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/>'
                '<hp:cellSz width="{w}" height="{h}"/>'
                '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
                "</hp:tc>").format(hd=1 if header else 0, bf=border_fill, va=valign,
                                   p=cell, c=c, r=r, cs=cs, rs=rs, w=width, h=height)

    def _tbl(self, trs, nrows, ncols, width, border_fill, page_break, inline, align,
             repeat_header=True):
        self._instid += 1
        return (
            '<hp:tbl id="{i}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
            'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="{pb}" '
            'repeatHeader="{rh}" rowCnt="{rc}" colCnt="{cc}" cellSpacing="0" '
            'borderFillIDRef="{bf}" noAdjust="0">'
            '<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="2000" heightRelTo="ABSOLUTE" '
            'protect="0"/>'
            '<hp:pos treatAsChar="{tac}" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
            'horzAlign="{ha}" vertOffset="0" horzOffset="0"/>'
            # An anchored table's bottom outMargin is the only space Hangul
            # leaves under it; _table_gaps() sets it from the next paragraph.
            '<hp:outMargin left="0" right="0" top="0" bottom="{omb}"/>'
            # Each hp:tc says hasMargin="0", which means "use the table's
            # inMargin", not its own cellMargin. With this at 0 the text sat
            # against the cell rule. 510/141 is what Hangul gives a new table.
            '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
            "{rows}</hp:tbl>"
        ).format(i=self._instid, rc=nrows, cc=ncols, bf=border_fill, pb=page_break,
                 rh=1 if repeat_header else 0,
                 tac=1 if inline else 0,
                 omb=283 if inline else "@@GAP@@",
                 # an inline table is placed by its paragraph's alignment; an
                 # anchored one ignores that and is placed by horzAlign
                 ha="LEFT" if inline else {"JUSTIFY": "LEFT"}.get(align, align),
                 w=width, rows="".join(trs))

    def _place_table(self, tbl, align, size, face):
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

        pic = pic_xml(self._instid, item, w, h, p.name)

        pp = self.parapr(align=align, prev_pt=prev_pt)
        cp = self.charpr()
        self._body.append(self._p(pp, f'<hp:run charPrIDRef="{cp}">{pic}</hp:run>'))
        return self

    # ------------------------------------------------------------------ save

    #: The least space left under an anchored table, HWPUNIT (3 mm).
    TABLE_GAP_MIN = 850

    def _table_gaps(self, parts):
        """Fill in each anchored table's bottom outMargin.

        Measured in Hangul: under a table anchored to its paragraph, the next
        paragraph's space-before is ignored, and so is the space-after of the
        paragraph holding the table — the only gap Hangul leaves is the
        table's own bottom outMargin. At 1 mm a heading band, which reaches
        above its text, was painted over the table's last row. So the gap is
        taken from what the next paragraph asked for, never under 3 mm.
        """
        out = []
        for k, part in enumerate(parts):
            if "@@GAP@@" in part:
                gap = self.TABLE_GAP_MIN
                if k + 1 < len(parts):
                    m = re.match(r'<hp:p id="0" paraPrIDRef="(\d+)"', parts[k + 1])
                    if m:
                        gap = max(gap, self._space_before.get(m.group(1), 0))
                part = part.replace("@@GAP@@", str(gap))
            out.append(part)
        return out

    def _section_xml(self) -> str:
        if not self._body:
            raise HwpxBuildError("nothing to write — add at least one paragraph")
        body_parts = self._table_gaps(self._body)
        # secPr belongs to the FIRST paragraph's first run, not to hs:sec.
        first = body_parts[0]
        cp = self.charpr()
        pagenum = f'<hp:run charPrIDRef="{cp}">{self._pagenum}</hp:run>' if self._pagenum else ""
        first = first.replace(
            ">", f'><hp:run charPrIDRef="{cp}">{self.secpr}{self.colpr}</hp:run>{pagenum}', 1)
        body = first + "".join(body_parts[1:])
        if self._fn_refs:
            body = self._fill_footnotes(body)
        return _SECTION_HEAD + body + "</hs:sec>"

    def _header_xml(self) -> str:
        h = self.header
        for lang, blocks in self._new_fonts.items():
            m = re.search(r'<hh:fontface lang="%s" fontCnt="(\d+)">(.*?)</hh:fontface>' % lang,
                          h, re.S)
            h = h.replace(m.group(0), '<hh:fontface lang="%s" fontCnt="%d">%s%s</hh:fontface>'
                          % (lang, int(m.group(1)) + len(blocks), m.group(2), "".join(blocks)), 1)
        for kind, plural in (("charPr", "charProperties"),
                             ("paraPr", "paraProperties"),
                             ("borderFill", "borderFills"),
                             ("tabPr", "tabProperties")):
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

    # -------------------------------------------------------------- template

    #: Fixed-width faces a Korean Hangul install commonly carries.
    MONO_FACES = ("굴림체", "바탕체", "돋움체", "궁서체", "D2Coding", "나눔고딕코딩",
                  "Consolas", "Courier New", "HY견명조체")

    @staticmethod
    def check_template(path):
        """What a template offers, before anything is built on it.

        Rule 1 makes the template a precondition and rule 3 makes its font
        table a limit: a face it lacks falls back to another without a word.
        This reports the fonts, which of them are fixed-width, the page set-up
        it brings, whether its secPr travels with a colPr, and whether it has
        a Footnote style — and lists as `warnings` whatever would make a build
        on it quietly wrong. Each check that could not be made is None.
        """
        path = Path(path)
        r = {"template": str(path), "fonts": None, "mono_faces": None,
             "secpr": None, "secpr_with_colpr": None, "page_mm": None,
             "footnote_style": None, "warnings": []}
        try:
            b = HwpxBuilder(path)
        except (HwpxBuildError, zipfile.BadZipFile, KeyError) as e:
            r["warnings"].append(f"not usable as a template: {e}")
            return r
        fonts = b.fonts()
        r["fonts"] = sorted(fonts)
        r["mono_faces"] = [f for f in fonts if f in HwpxBuilder.MONO_FACES]
        r["secpr"] = True
        with zipfile.ZipFile(path) as z:
            sec = z.read(SECTION).decode("utf-8")
        sp = re.search(r"</hp:secPr>\s*(<hp:ctrl>\s*<hp:colPr\b)?", sec)
        r["secpr_with_colpr"] = bool(sp and sp.group(1))
        g = b._page_geom()
        r["page_mm"] = {k: round(v / MM, 1) for k, v in g.items()}
        r["page_mm"]["text_width"] = round(b.text_width / MM, 1)
        r["footnote_style"] = bool(re.search(
            r'<hh:style\b[^>]*type="PARA"[^>]*engName="Footnote"', b.header))
        if not r["mono_faces"]:
            r["warnings"].append("no fixed-width face — code() and inline code will "
                                 "be set in the body face")
        elif "굴림체" not in fonts:
            r["warnings"].append(f"굴림체 missing — pass face={r['mono_faces'][0]!r} to "
                                 "code() and inline_code(); mono_cols() was measured "
                                 "on 굴림체 only")
        if "함초롬바탕" not in fonts:
            r["warnings"].append("함초롬바탕 missing — the example's body face falls back")
        if not r["footnote_style"]:
            r["warnings"].append("no Footnote style — footnotes take the base style")
        return r

    # ---------------------------------------------------------------- verify

    @staticmethod
    def _grid_report(section_xml):
        """Each table's cells against its own grid: holes, overlaps, cells
        outside rowCnt × colCnt, rows with no cell of their own, and cells
        whose rowAddr is not the row they are written in. A table nested in a
        cell is checked on its own grid. All of these pass the XML checks."""
        try:
            root = ET.fromstring(section_xml.encode())
        except ET.ParseError:
            return None
        loc = lambda e: e.tag.rsplit("}", 1)[-1]
        out = {"tables": 0, "bad": []}
        for k, tbl in enumerate(e for e in root.iter() if loc(e) == "tbl"):
            out["tables"] += 1
            nr, nc = int(tbl.get("rowCnt", 0)), int(tbl.get("colCnt", 0))
            rows = [e for e in tbl if loc(e) == "tr"]
            seen, over, outside, wrong_row, bare = set(), 0, 0, 0, []
            for y, tr in enumerate(rows):
                tcs = [e for e in tr if loc(e) == "tc"]
                if not tcs:
                    bare.append(y)
                for tc in tcs:
                    addr = next((e for e in tc if loc(e) == "cellAddr"), None)
                    span = next((e for e in tc if loc(e) == "cellSpan"), None)
                    if addr is None:
                        continue
                    c0, r0 = int(addr.get("colAddr")), int(addr.get("rowAddr"))
                    cs = int(span.get("colSpan", 1)) if span is not None else 1
                    rs = int(span.get("rowSpan", 1)) if span is not None else 1
                    wrong_row += r0 != y
                    for yy in range(r0, r0 + rs):
                        for xx in range(c0, c0 + cs):
                            if not (0 <= yy < nr and 0 <= xx < nc):
                                outside += 1
                            elif (yy, xx) in seen:
                                over += 1
                            seen.add((yy, xx))
            holes = nr * nc - len({p for p in seen if 0 <= p[0] < nr and 0 <= p[1] < nc})
            if holes or over or outside or wrong_row or bare or len(rows) != nr:
                out["bad"].append({"table": k, "rowCnt": nr, "colCnt": nc, "rows": len(rows),
                                   "holes": holes, "overlaps": over, "outside": outside,
                                   "rowAddr_mismatch": wrong_row, "rows_without_cells": bare})
        return out

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
        # Page numbers and table page breaks are invisible to every other check
        # here and to the text extract; say what the file asks for.
        pn = re.search(r'<hp:pageNum\b[^>]*pos="(\w+)"', section)
        r["page_numbering"] = pn.group(1) if pn else "none"
        r["table_grid"] = HwpxBuilder._grid_report(section)
        # Markup that reached the page as characters. Text the author escaped
        # on purpose ("\<u>") counts too; it is a pointer, not a verdict.
        body = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", section))
        r["markup_left"] = {"tags": len(re.findall(r"&lt;/?(?:u|sup|sub)&gt;", body)),
                            "double_asterisks": body.count("**")}
        pbs = re.findall(r'<hp:tbl\b[^>]*\bpageBreak="(\w+)"', section)
        r["table_page_break"] = {v: pbs.count(v) for v in sorted(set(pbs))}
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
