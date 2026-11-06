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
    b.para("본문 문단. **굵게** 와 *기울임* 이 산다.")
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

__all__ = ["HwpxBuilder", "HwpxBuildError"]

SECTION = "Contents/section0.xml"
HEADER = "Contents/header.xml"
HPF = "Contents/content.hpf"

#: 1 mm in HWPUNIT (1/7200 inch).
MM = 7200 / 25.4

_EMPH = re.compile(r"\*\*(.+?)\*\*|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")

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

        self._base_charpr = self._first_id("charPr")
        self._base_parapr = self._first_id("paraPr")
        self._base_borderfill = self._first_id("borderFill")
        self._fonts = self._font_table()

        self._new = {"charPr": [], "paraPr": [], "borderFill": []}
        self._cache: dict = {}
        self._body: list[str] = []
        self._images: list[tuple[str, Path]] = []
        self._instid = 1000000

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
               face=None, fill=None) -> str:
        """Character properties id. Cloned from the template's first charPr.

        `size` in points, `face` a font name from `fonts()`, `fill` a
        borderFill id for character shading.
        """
        key = ("c", size, bold, italic, color.upper(), face, fill)
        if key in self._cache:
            return self._cache[key]

        def mutate(tag, inner):
            tag = self._set_attr(tag, "height", str(int(round(size * 100))))
            tag = self._set_attr(tag, "textColor", color)
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
               border_edges=()) -> str:
        """Paragraph properties id.

        `first_mm` negative gives a hanging indent. `fill` shades the paragraph,
        `border_edges` is any of ("left","right","top","bottom").
        """
        key = ("p", align, left_mm, right_mm, first_mm, prev_pt, next_pt,
               line_spacing, fill, tuple(border_edges))
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

        bf = fill if fill is not None else self._base_borderfill
        border = ('<hh:border borderFillIDRef="{bf}" offsetLeft="283" offsetRight="283" '
                  'offsetTop="142" offsetBottom="142" connect="0" ignoreMargin="0"/>'
                  ).format(bf=bf)

        def mutate(tag, inner):
            inner = re.sub(r"<hh:align\b[^>]*/>",
                           f'<hh:align horizontal="{align}" vertical="BASELINE"/>',
                           inner, count=1)
            # Margins appear twice — inside hp:case AND hp:default. Setting only
            # one leaves Hangul reading the other and the indent silently lost.
            inner = re.sub(r"<hh:margin>.*?</hh:margin>"
                           r'<hh:lineSpacing\b[^>]*/>', margin, inner, flags=re.S)
            inner = re.sub(r"<hh:border\b[^>]*/>", border, inner, count=1)
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

    @property
    def text_width(self) -> int:
        """Usable text width in HWPUNIT, for sizing tables and images."""
        w, _, m = getattr(self, "_page", (210, 297, {"left": 30, "right": 30}))
        return int(round((w - m["left"] - m["right"]) * MM))

    # ------------------------------------------------------------------ runs

    def _runs(self, text, size, bold, color, face) -> str:
        """Split on **bold** / *italic*, one hp:run per span."""
        out, pos = [], 0
        for m in _EMPH.finditer(text):
            if m.start() > pos:
                out.append((text[pos:m.start()], bold, False))
            if m.group(1) is not None:
                out.append((m.group(1), True, False))
            else:
                out.append((m.group(2), bold, True))
            pos = m.end()
        if pos < len(text):
            out.append((text[pos:], bold, False))
        if not out:
            out = [("", bold, False)]
        return "".join(
            '<hp:run charPrIDRef="%s"><hp:t>%s</hp:t></hp:run>'
            % (self.charpr(size=size, bold=b, italic=i, color=color, face=face), _esc(t))
            for t, b, i in out)

    def _p(self, para_pr, body, style="0") -> str:
        # No hp:linesegarray is written. It is a layout cache keyed to text that
        # does not exist yet; Hangul builds it on open. Writing a guess is the
        # single most reliable way to have the file refused.
        return ('<hp:p id="0" paraPrIDRef="%s" styleIDRef="%s" pageBreak="0" '
                'columnBreak="0" merged="0">%s</hp:p>' % (para_pr, style, body))

    # ---------------------------------------------------------------- blocks

    def para(self, text="", size=13.0, bold=False, color="#000000", face=None,
             align="JUSTIFY", left_mm=0.0, first_mm=0.0, prev_pt=0.0, next_pt=0.0,
             line_spacing=160, fill=None, border_edges=()):
        pp = self.parapr(align=align, left_mm=left_mm, first_mm=first_mm,
                         prev_pt=prev_pt, next_pt=next_pt, line_spacing=line_spacing,
                         fill=fill, border_edges=border_edges)
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

    def code(self, lines, size=10.5, face="굴림체", fill_color="#F2F2F2"):
        bf = self.borderfill(face_color=fill_color)
        pp = self.parapr(align="LEFT", left_mm=3, line_spacing=130, fill=bf)
        for ln in lines:
            # Code is literal: ** and * in it are syntax, not emphasis.
            cp = self.charpr(size=size, face=face)
            self._body.append(self._p(
                pp, '<hp:run charPrIDRef="%s"><hp:t>%s</hp:t></hp:run>'
                    % (cp, _esc(ln) or "")))
        return self

    def quote(self, text, size=11.5, face=None, fill_color="#FFF6E5"):
        bf = self.borderfill(face_color=fill_color)
        self.para(text, size=size, face=face, left_mm=5, prev_pt=6, next_pt=6, fill=bf)
        return self

    def table(self, rows, widths_mm=None, header=True, size=10.5, face=None,
              head_fill="#EAEAEA"):
        """A grid table. `rows` is a list of lists of strings."""
        if not rows:
            raise HwpxBuildError("table() needs at least one row")
        ncol = max(len(r) for r in rows)
        rows = [list(r) + [""] * (ncol - len(r)) for r in rows]
        if widths_mm:
            widths = [int(round(w * MM)) for w in widths_mm]
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
            '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            "{rows}</hp:tbl>"
        ).format(i=self._instid, rc=len(rows), cc=ncol, bf=grid,
                 w=self.text_width, rows="".join(trs))

        pp = self.parapr(align="LEFT", next_pt=6)
        self._body.append(self._p(pp, tbl))
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
        first = first.replace(">", f'><hp:run charPrIDRef="{cp}">{self.secpr}</hp:run>', 1)
        return _SECTION_HEAD + first + "".join(self._body[1:]) + "</hs:sec>"

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
