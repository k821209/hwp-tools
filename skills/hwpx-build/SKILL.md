---
name: hwpx-build
description: Build a new Hangul .hwpx document — headings, paragraphs, tables, images, code blocks, pull-quotes, reference lists — without Hangul or LibreOffice, by deriving styles from a template .hwpx rather than writing header.xml from scratch. Use when a report, chapter, manual or form body has to be generated programmatically as .hwpx, or when a .hwpx you wrote from nothing is rejected by Hangul.
---

# hwpx-build

`hwpx-edit` revises a document someone else made. This writes a new body:
a chapter, a report, a manual — from a structured description.

`scripts/hwpx_build.py` is the library. Standard library only, Python 3.8+.

```python
from hwpx_build import HwpxBuilder

b = HwpxBuilder("template.hwpx")                 # a real Hangul file, for its header
b.page(margins_mm={"top": 15, "bottom": 15, "left": 25, "right": 25})
b.heading(1, "제3장 진핵생물 데이터 분석")
b.para("본문입니다. **굵게** 와 *기울임* 이 삽니다.", face="함초롬바탕")
b.table([["항목", "원핵생물", "진핵생물"], ["인트론", "없음", "있음"]])
b.image("fig3-1.png", width_mm=120)
b.para("그림 3-1. 설명.", size=10, align="CENTER")
b.code(["fastp -i R1.fq.gz -o R1.clean.fq.gz"], face="굴림체")
b.quote("함정 — 배수성을 확인하지 않고 시작하는 것.")
b.save("out.hwpx")
HwpxBuilder.verify("out.hwpx", expect_images=1, expect_tables=1)
```

## The five rules

**1. Never write `header.xml` from scratch. Start from a template.**
A header carries the font table, border fills, character and paragraph
property tables, numbering and styles, and every id in the body is a reference
into it. Inventing one is the fastest route to a file that passes every
structural check and still will not open. `HwpxBuilder` takes a `.hwpx` made by
Hangul, *clones* the entries it needs (`charpr()`, `parapr()`, `borderfill()`
each derive from the template's first one), appends them, and bumps `itemCnt` —
the same discipline `hwpx-edit` uses for a single marked colour, applied to a
whole document. Only `Contents/section0.xml` is written fresh.

**2. Write no `hp:linesegarray`.**
`hwpx-edit`'s first rule is to strip the line cache from paragraphs you touch.
Here every paragraph is new, so there is nothing to strip and nothing to write:
the cache describes where lines of text break, and that text has never been laid
out. Hangul builds it on open. `verify()` reports the count so a `0` is visible
rather than assumed.

**3. A font you name must already be in the template's table.**
`fonts()` lists what is available; `font_id()` falls back rather than inventing
an id. Adding a face to `hh:fontfaces` does not install it — Hangul substitutes
on open, and you get a document whose code blocks are set in the body face
without any error to tell you. Pick from the template, or pick a template that
carries the face you want. `굴림체` is the fixed-width face present in
essentially every Hangul install.

**4. A table or a picture goes inside an `hp:run`, never straight into `hp:p`.**
`hp:tbl` and `hp:pic` are control objects and live where a run of text would.
Place one as a direct child of the paragraph and everything still agrees with
you: the XML parses, `verify()` counts the table, `hwpx_text.py` prints its
rows. Hangul opens the document and the table is not there — no warning, no
repair dialog, just a missing table. `verify()` now reports
`controls_outside_run`, because this is invisible from every other angle.

**5. `hh:paraPr` holds its margins TWICE.**
Inside `<hp:switch>` there is an `<hp:case>` and an `<hp:default>`, each with a
full `<hh:margin>` and `<hh:lineSpacing>`. Set only the first and Hangul reads
the other: the indent, the hanging indent for a reference list, the line spacing
all silently revert. `parapr()` replaces every occurrence, not the first.

## What it writes

| call | produces |
|---|---|
| `para(text, …)` | `hp:p` with one `hp:run` per `**bold**` / `*italic*` span |
| `heading(level, text)` | a sized, bold paragraph (1→17 pt, 2→14, 3→13) |
| `bullet(text)` | a `·` paragraph with a hanging indent |
| `table(rows, widths_mm=…)` | `hp:tbl` sized to `text_width`, header row shaded |
| `image(path, width_mm=…)` | inline (`treatAsChar="1"`) PNG, aspect kept, added to `BinData/` and the `content.hpf` manifest |
| `code(lines, face=…)` | shaded, indented, fixed-width lines, emphasis NOT parsed |
| `quote(text)` | shaded, indented pull-quote |

Styling arguments are plain units: `size` in points, `*_mm` in millimetres
(`first_mm` negative = hanging indent), `prev_pt`/`next_pt` space around the
paragraph, `line_spacing` a percentage. `text_width` gives the usable width in
HWPUNIT for sizing tables yourself.

Images must be **PNG** — the size is read from the IHDR so Pillow is not needed.

## Verifying

`HwpxBuilder.verify()` checks: zip integrity, all three XML parts parse,
`itemCnt` against the real block count for charPr/paraPr/borderFill, duplicate
ids, duplicate zip entries, image count, table count, that no `linesegarray`
was written, that every `binaryItemIDRef` in the body resolves to an `opf:item`
in the manifest, that every rootfile `META-INF/container.xml` declares is
actually in the archive, and that no `hp:tbl`/`hp:pic` sits outside an
`hp:run`. The last three are each a way to lose content with every XML part
still parsing cleanly.

Cross-check the text too: `../hwpx-edit/scripts/hwpx_text.py out.hwpx` renders
body and tables, so a diff against the source shows anything dropped.

**Passing all of it does not mean the file opens.** LibreOffice cannot load
`.hwpx`, so nothing here can render the result. `verify()` returns
`opens_in_hangul: None` on purpose. A person opens it in Hangul and says.

## Decoration

Paragraph shading and paragraph borders are one `borderFill`, referenced from
`hh:paraPr`'s `hh:border`. So a chapter title set white on a solid band, and a
section title on a wash with a rule under it, are both just a `fill=`:

```python
b.para("제3장 …", size=17, bold=True, color="#FFFFFF", left_mm=3,
       fill=b.borderfill(face_color="#1F3A5F"))
b.para("3.1 …", size=14, bold=True, color="#1F3A5F", left_mm=2,
       fill=b.borderfill(face_color="#E8EDF3", edges=("bottom",),
                         width="0.4 mm", color="#1F3A5F"))
```

`parapr(border_edges=…)` merges edges into the fill for you; passing `fill=`
directly is clearer when you want both.

## Known unverified

- **Leading spaces in `code()`.** They are written literally into `<hp:t>` and
  survive a re-read, but whether Hangul preserves or collapses them on open has
  not been checked. If it collapses them, substitute U+00A0 for the indent.
- **Page headers and footers.** `page()` sets the margins reserved for them;
  writing their content needs `hp:masterPage`, which this does not do.

## Example

`examples/markdown_chapter.py` — a textbook chapter in markdown to `.hwpx`:
headings, tables, figures with captions, fenced code, pull-quotes and a
hanging-indent reference list. Reads the same markdown subset a python-docx
builder does, so one source yields both formats.

```
python3 markdown_chapter.py chapter.md out.hwpx "제3장 …" template.hwpx
```
