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
| `para(text, …)` | `hp:p` with one `hp:run` per `**bold**` / `*italic*` / `` `code` `` span |
| `heading(level, text)` | a sized, bold paragraph (1→17 pt, 2→14, 3→13) |
| `bullet(text)` | a `·` paragraph with a hanging indent |
| `table(rows, widths_mm=…)` | `hp:tbl` sized to `text_width`, header row shaded |
| `image(path, width_mm=…)` | inline (`treatAsChar="1"`) PNG, aspect kept, added to `BinData/` and the `content.hpf` manifest |
| `code(lines, lang=…)` | shaded, indented, fixed-width lines, syntax-coloured; emphasis NOT parsed |
| `quote(text)` | shaded, indented pull-quote |
| `footnotes({id: text})` | `hp:footNote` at each `[^id]` in body text or a table cell |

Styling arguments are plain units: `size` in points, `*_mm` in millimetres
(`first_mm` negative = hanging indent), `prev_pt`/`next_pt` space around the
paragraph, `line_spacing` a percentage. `text_width` gives the usable width in
HWPUNIT for sizing tables yourself.

`**bold**`, `*italic*`, `` `code` `` and `[^id]` are read in `para`, `heading`,
`bullet`, `quote` and every table cell — everything except `code()`, where
they are syntax.

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

## Colouring code

`code()` colours its block through `scripts/codelex.py` — a small regex lexer
for the three languages a technical text actually prints, Python, R and shell.
Standard library only, so a document generator does not pull in Pygments to put
four colours on a listing.

```python
b.code(lines)                     # language guessed from the first line
b.code(lines, lang="r")           # or named
b.code(lines, lang="plain")       # a data dump: no colouring
b.code(lines, theme={"comment": "#777777"})
```

Two things make it safe to run over a listing you care about:

**`lex()` returns every character exactly once.** Concatenating the pieces
gives the source back, so highlighting cannot drop a backslash or eat an
indent. The module's `__main__` asserts the round trip; so should anything
built on it.

**A block whose first line starts with `#` but does not name a language is
treated as plain.** The convention is a first-line comment — `# python`,
`# R` — and anything else beginning with `#` is a data dump. A VCF block
lexed as shell turns its `#CHROM …` header into a comment and each data row
into a command, which looks deliberate and is wrong.

For shell, a program name is recognised at the start of a line or after a
pipe or separator — not merely at the start of an unmatched span, which is
also where an *argument* lands once the option before it has been tokenised.
`ref.fa` in `-R '…' ref.fa` is an argument, and an earlier version painted it
as a command.

## Inline code

A backtick span in running text — a column name, a file, a label value — is set
in a fixed-width face on a light shade, so it stays distinguishable from the
words around it. A document that has to be checked against a CSV needs that:
`원문대조A` without its styling could be a column or a phrase.

```python
b.para("열 `human_verdict` 는 비워 두었다.")      # 굴림체 on #F2F2F2 by default
b.inline_code(face="굴림체", shade="#EEF2F7", color="#8B1E3F")
b.inline_code(face=None, shade=None)              # backticks dropped, no styling
```

The shade is the character's own (`hh:charPr shadeColor`, 글자 모양 → 음영 색),
not a `borderFill`. The span follows markdown: what is inside the backticks is
literal, so `` `a*b*c` `` keeps its asterisks; a code span inside `**bold**`
stays bold; a lone backtick with no partner prints as a backtick; double
backticks (``` `` `x` `` ```) show a backtick inside. If the face is not in the
template it falls back to the surrounding face (rule 3) and only the shade
marks the span.

## Footnotes

Written as in markdown: a `[^id]` where the mark goes, and the text given once
to `footnotes()`, before or after — references are resolved at `save()`.

```python
b.para("검수자 간 일치도는 κ = 0.638 이었다.[^kappa]")
b.table([["지표", "값"], ["Cohen's κ[^kappa]", "0.638"]])   # a cell works too
b.footnotes({"kappa": "Cohen's κ 는 우연 일치를 뺀 일치도다. 1 이 완전 일치."})
```

Each reference becomes `hp:run > hp:ctrl > hp:footNote > hp:subList > hp:p`,
the note opening with an `hp:autoNum numType="FOOTNOTE"`. The number format and
the rule above the notes are the template's own (`hp:footNotePr` in the borrowed
`secPr`), and the note paragraph takes the template's `Footnote` style when it
has one. Notes are numbered in the order their references appear, table cells
included; a note referenced twice is written twice, since OWPML has no shared
footnote.

**A reference with no definition makes `save()` raise.** The alternative is a
literal `[^kappa]` in the document, or a mark with nothing under it, and either
one passes every structural check. `verify()` reports `footnotes` and
`footnote_slots_unfilled`. Inside a code span `[^x]` is text, and so is a `[^x]`
in the text of a footnote — notes do not nest.

`hwpx_text.py` prints a note's text inline, right after its mark, so a diff
against the markdown source shows it in the paragraph rather than at the end.

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

**Two margins, and they are not interchangeable.**

| | what it is | set with |
|---|---|---|
| inner | text → box edge, i.e. the box's padding | `border_offsets=(l, r, t, b)`, HWPUNIT |
| outer | box edge → the paragraph next door | `prev_pt` / `next_pt` |

Raising the inner margin to push a heading off the body text does the opposite
of what you want: the box grows to meet the text instead of backing away from
it. Keep the inside tight — 85 HWPUNIT (0.3 mm) under a wash, ~150 under a
solid band so the white type is not cramped — and buy the separation outside.

The two do interact: the box extends `bottom` past the text, so the gap a
reader sees is `next_pt × 100 − bottom`. That is why the default 142 against a
6 pt space-after leaves 1.6 mm and reads as the box sitting on the first body
line. Fix it from the outer side.

## Confirmed in Hangul

Opened in Hangul 2024 by a person — which is the only way any of this gets
confirmed, since nothing here can render a `.hwpx`. A four-chapter textbook,
each chapter ~30 pages with tables, figures, fenced code and a reference list:

- the file opens, with no document-security or repair dialog
- tables render (once they are inside an `hp:run` — rule 4)
- inline images render at the requested width
- paragraph shading, the chapter band and the section rule render
- **leading spaces in `code()` are preserved**, so the U+00A0 workaround that
  was being held in reserve is not needed

And a check document (2026-11-14), opened by a person in Hangul:

- **footnotes** — in body text and in a table cell, each dropped to the foot
  of the page under the template's rule and numbered `1)`, `2)` in order of
  appearance; inline code inside the note text keeps its shading
- **inline code** — 굴림체 on the `shadeColor` shade, in body text and table
  cells; `a*b*c` keeps its asterisks
- **`ignore_margin=True`** — the chapter band and the section wash sit apart
  from the body text below them instead of growing into the space after
- **table cell padding** — text sits clear of the cell rule once the table's
  `inMargin` is 510/141 (it was 0, and every cell, at `hasMargin="0"`, follows
  the table's value rather than its own `cellMargin`)
- from `markdown_chapter.py`: a hard-wrapped source paragraph comes out as one
  paragraph, a `1. ` item's continuation line lines up under the hanging
  indent, and a bold-led caption is justified

## Known unverified

- **Page headers and footers.** `page()` sets the margins reserved for them;
  writing their content needs `hp:masterPage`, which this does not do.
- **A footnote longer than the space left on its page**, and several notes
  competing for one page foot. Only two short notes have been seen.
- **Everything outside that document's shape.** Long tables broken across
  pages, nested tables, equations and multi-section documents have not been
  exercised.

## Example

`examples/markdown_chapter.py` — a textbook chapter in markdown to `.hwpx`:
headings, tables, figures with captions, fenced code, pull-quotes and a
hanging-indent reference list. Reads the same markdown subset a python-docx
builder does, so one source yields both formats.

Two things about that subset you would otherwise learn from the output:

- **A paragraph ends at a blank line**, not at a line break. Markdown wrapped
  at 80 columns gives one `hp:p` per paragraph; writing one per source line
  puts paragraph spacing between every line and justifies each line on its
  own, and the body comes out scattered. List items and `>` quotes take their
  continuation lines the same way.
- **`1. ` numbered items are read; nested lists are not.** An indented item
  under another one starts its own item at the outer level.

```
python3 markdown_chapter.py chapter.md out.hwpx "제3장 …" template.hwpx
```
