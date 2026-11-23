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

b = HwpxBuilder()                                # shipped blank template; or a path
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

## The template that ships here

`template/base.hwpx` is a blank template, so a clone of this repository can
build without hunting for a Hangul file first. Its `header.xml` — fonts,
character and paragraph properties, border fills, styles — was written by
Hangul (2022, v12) for a government application form; the form's body, its
preview text and thumbnail and its images were all removed, and nothing of
its content remains in the archive. What it brings:

- 17 faces including 함초롬바탕, 함초롬돋움, 맑은 고딕, 바탕, 돋움 and the
  fixed-width 굴림체
- a Footnote style, and `footNotePr` numbering as "1)"
- A4, 20 mm sides, 20 mm top, 15 mm bottom, 10 mm header and footer (change
  with `page()`)
- `secPr` with its `colPr`

`HwpxBuilder()` with no argument uses it; `HwpxBuilder("x.hwpx")` uses yours.
`HwpxBuilder.check_template("template/base.hwpx")` reports no warnings. Use
your own template when the document has a house style; run
`check_template()` on it first.

## The six rules

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
essentially every Hangul install — but not in every Hangul *document*, and the
font table is the document's.

Two things now say so instead of the reader having to know. Before building,
`HwpxBuilder.check_template(path)` reports the template's fonts, which of them
are fixed-width, its page set-up, whether its `secPr` carries a `colPr`, and
whether it has a Footnote style, with `warnings` for anything that would make a
build quietly wrong ("no fixed-width face — code() and inline code will be set
in the body face"). While building, every face that falls back is recorded in
`builder.font_fallbacks` (`{"굴림체": ["함초롬바탕", "굴림"]}` — one entry per
substitute actually used) and raised once as a `UserWarning`. `verify()` cannot
report it: it reads the finished file, which holds the face that was used, not
the one that was asked for.

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

**6. `hp:secPr` travels with its `hp:colPr`.**
The page size and margins ride in the first paragraph's first run, and Hangul
writes the column definition (`<hp:ctrl><hp:colPr …/></hp:ctrl>`) right after
them in the same run. Borrow the one without the other and the file opens, every
check passes, and the page setup is replaced by Hangul's defaults: 30 mm sides,
20 + 15 mm top. Tables sized to `text_width` then overrun the right margin by
the difference — which is how this was found, reported as a table-margin bug
(#8). The builder borrows both; `verify()` reports `secpr_with_colpr`.

## What it writes

| call | produces |
|---|---|
| `para(text, …)` | `hp:p` with one `hp:run` per `**bold**` / `*italic*` / `` `code` `` span |
| `heading(level, text)` | a sized, bold paragraph (1→17 pt, 2→14, 3→13), kept on the page of the paragraph after it |
| `bullet(text)` | a `·` paragraph with a hanging indent |
| `table(rows, widths_mm=…, align=…, page_break=…, inline=…)` | `hp:tbl`, header row shaded and repeated on each page; columns sized to their content to keep the table short (`"equal"` splits evenly; a list of mm is used as given, wider than the body raises); splits between rows at a page boundary |
| `copy_table(src, index=… / containing=…)` | a table taken whole from another Hangul .hwpx, with the shapes it points to carried into this header |
| `grid_table(widths_mm, cells)` | a table with merged cells on a grid — a form; every position covered exactly once, checked before writing |
| `page_number(pos=…, fmt=…, side_char=…, start=…)` | one `hp:pageNum` control: "- 1 -" at the foot of every page by default |
| `page_break()` | the next block starts a new page (`pageBreak="1"` on its paragraph) |
| `hide_page_number()` / `restart_page_number(n)` | no number on the page the next block lands on / that page is numbered `n` (`hp:pageHiding`, `hp:newNum`) |
| `image(path, width_mm=…)` | inline (`treatAsChar="1"`) PNG, aspect kept, added to `BinData/` and the `content.hpf` manifest |
| `code(lines, lang=…)` | shaded, indented, fixed-width lines, syntax-coloured; 4 pt above the block and 8 pt below (`prev_pt`/`next_pt`); emphasis NOT parsed |
| `quote(text)` | shaded, indented pull-quote |
| `footnotes({id: text})` | `hp:footNote` at each `[^id]` in body text or a table cell |

Styling arguments are plain units: `size` in points, `*_mm` in millimetres
(`first_mm` negative = hanging indent), `prev_pt`/`next_pt` space around the
paragraph, `line_spacing` a percentage. `text_width` and `text_height` give
the usable body area in HWPUNIT (divide by `MM` for millimetres), read from the
page settings actually in the file — the template's, if `page()` was never
called. `text_height` subtracts the header and footer areas as well as the top
and bottom margins: Hangul's body is what is left after all four, and leaving
out the header and footer overstates it by 30 mm at 15/15.

A backslash before ASCII punctuation makes it the character itself, as in
CommonMark: `\*`, `\[^1]`, `\|`. Inside a code span a backslash is literal.

`<u>…</u>` underlines (`charpr(underline=True)`, `hh:underline type="BOTTOM"`).

Superscript and subscript are written as the HTML tags — `x<sup>2</sup>`,
`H<sub>2</sub>O` — and set with `<hh:supscript/>` / `<hh:subscript/>` in the
character shape (`charpr(script="sup"|"sub")`). Not pandoc's `^…^` / `~…~`:
Korean marks ranges with "~" (1~8권, 2018~2025), and two in one paragraph
would pair into a subscript.

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

**Text and structure cannot show formatting.** A caption that falls into the
body branch comes out at 13 pt instead of 10 with every character in place, so
the text diff passes and so does `verify()`. Read the formatting back with
`--with-style`:

```
$ hwpx_text.py out.hwpx --with-style
[13.0pt 함초롬바탕] 엑셀 시트를 회람하는 방식을 쓰지 않고 …
[10.0pt 함초롬바탕 굵게 + 10.0pt 함초롬바탕] 그림 1. 검수 화면 — …
| [10.5pt 함초롬바탕 굵게] 지표 | [10.5pt 함초롬바탕] 0.638 |
  ↳ 각주 [9.5pt 함초롬바탕] Cohen's κ 는 …
```

Each paragraph and table cell is prefixed with the size, face and weight of the
charPr its runs reference, and each footnote gets its own line. It shows and
does not judge — what the right size is belongs to the document — so diff it
against the previous build, or read the lines you meant to change.

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

## Lining up columns in a code block

A tree or a small table drawn in a `code()` block lines up when every
character is counted at its real width. In 굴림체 that is simple, and it was
measured in Hangul rather than assumed: **ASCII takes one column, and every
other character tested takes two** — Hangul syllables, hanja, box drawing
(`├ ─ │ └`), arrows (`→ ↓`), circled digits (`①`), and even the Latin-1
symbols `· × °` and the quote `‘`. `mono_cols(text)` counts that way:

```python
from hwpx_build import mono_cols
w = max(mono_cols(t) for t in rows) + 2
lines = [t + " " * (w - mono_cols(t)) + "# note" for t in rows]
```

Two things this does not cover. Unicode marks most of those symbols as
"ambiguous" width and a Western monospace face draws them one column wide, so
the same source exported elsewhere (a `.docx` in Noto Sans Mono CJK, say) can
misalign — count for the face the document is set in. **A source that is
also exported to `.docx` or `.pdf` should not align at all**: no single count
is right in both faces, and indentation with ASCII spaces is. And characters outside
the tested set — accented Latin, emoji, half-width kana — are unmeasured; if a
block depends on them, indent instead of aligning: put the note on the next
line, so no column has to agree.

`code()` also turns off Hangul's automatic space between Korean and Latin or
digits (`hh:autoSpacing`) for its paragraphs, whatever the template says. With
it on, every boundary between the two scripts gains a little space and a
fixed-width face stops lining up.

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

## Column widths

Without `widths_mm`, `table()` shares the body width out to make the table as
short as it can. Splitting it evenly gives a two-character "번호" column the
same room as a three-line description, so the description folds and every row
grows; a Korean report table, whose columns differ a lot in length, pays that
on nearly every row.

What is minimised is the table's height in lines — per row, its tallest cell —
estimated by laying each cell's text out the way Hangul does. Every column
keeps at least the width its header needs on one line. The rest starts in
proportion to each column's longest cell and moves 1 mm at a time from column
to column while the estimate drops.

The estimate encodes two things measured in Hangul, both against the grain of
the XML: **Korean breaks between any two syllables** although the paragraph
says `breakNonLatinWord="KEEP_WORD"`, while a run of Latin letters, digits
and ASCII punctuation moves to the next line whole (a URL wider than the line
is broken); and **a space is about half an em**. Read against 32 rows of
three tables in both layouts it was exact on 31 and one line high on the
last, never low. On those tables the automatic layout took 12 lines where the
even split took 22, and 10 where it took 19, with every header on one line.

A column whose body is empty — a form's "계획" or "충족 근거", left for the
writer — takes the weight of the widest filled column; sized by its header
alone it came out the narrowest, the opposite of what it is for.

`widths_mm="equal"` restores the even split; a list of millimetres is used as
given. In markdown_chapter a separator row whose dashes differ in length
(`|---|:----------------|---|`) sets the proportions.

## Forms: merged cells

`table()` takes rows of strings; a form needs cells that span rows and
columns. `grid_table(widths_mm, cells)` lays them on a grid — `widths_mm` is
every grid column, and each cell is a dict:

```python
b.grid_table([35, 55, 30, 50], [
    {"r": 0, "c": 0, "text": "연구개발과제명", "fill": "#E8EDF3", "bold": True},
    {"r": 0, "c": 1, "text": "…", "cs": 3},
    {"r": 1, "c": 0, "text": "세부사업명", "rs": 2, "fill": "#E8EDF3", "align": "CENTER"},
    ...
])
```

Only `r` and `c` are required; `rs`/`cs` merge, and `fill`, `align`, `bold`,
`size`, `valign` and `edges` (the borders the cell draws — `()` for a gap)
style one cell. A merged cell is as wide as the columns it spans, and only its
top-left position is written, as Hangul writes it.

**Before anything is written**, every grid position must be covered by exactly
one cell, no cell may run past the last column, and every row must start a
cell of its own — otherwise it raises, naming the positions. A wrong merge is
well-formed XML that every structural check passes and that shows only as a
broken table in Hangul. (No Hangul-made table here has a row with no cell of
its own — 923 tables, about 2,000 merged cells.) `verify()` reports
`table_grid`, the same coverage check run on every table in the file: holes,
overlaps, cells outside `rowCnt` × `colCnt`, rows without cells, cells whose
`rowAddr` is not their row. On those 923 Hangul-made tables it flags none.

The first row is not repeated on a new page unless `repeat_header=True`: a
form's first row is a field. Text in a cell reads the same inline markup as
anywhere else, `\n` included.

`markdown_chapter.convert(..., builder=b)` appends a markdown body to a
builder that already holds such a form, keeping its template and page set-up.

**When the form already exists as a Hangul file, copy it instead of redrawing
it.** `copy_table(src, index=2)` — or `containing="제안 사업명"` — takes a
top-level table from another `.hwpx` whole, nested tables included. The work
is in its references: the table's cells, runs and paragraphs point by number
into the source's header (border fills, character shapes, paragraph shapes,
styles, tab settings), and the character shapes point on into its font
tables. Pasted as it is, the table would point at whatever this document holds
under those numbers — other borders, other fonts — and every structural check
would pass. So each referenced entry is copied into this header under a new
id and the table's references renumbered: fonts are matched by name in each of
the seven language tables, and a face this template lacks is copied in;
paragraph shapes bring their tab settings; styles are matched by name.

What is not carried: paragraph numbering inside the table (set to none),
a style this template lacks by name (바탕글 instead), and pictures (raises).
`builder.copy_notes` lists each change. The source's line cache is dropped; a
table wider than this body raises. Fill the copied form afterwards with
hwpx-edit's `fill_cell()`.

## Tables across pages

A table that does not fit in what is left of the page either splits or jumps,
and which one depends on two settings. Both were measured in Hangul on long
tables placed to straddle a page boundary.

**`inline` — whether the table is a "character" (글자처럼 취급).** An inline
table (`treatAsChar="1"`) is laid out like one tall glyph, and Hangul never
splits it: whatever `page_break` says, it moves whole to the next page and
leaves the rest of the page empty, and a table taller than a page has nowhere
to go. `table()` therefore anchors the table to its paragraph and lets it flow
with the text (`treatAsChar="0"`, `flowWithText="1"`) unless `inline=True`. An
anchored table is placed by `horzAlign`, not by its paragraph's alignment, and
`align=` sets that.

**`page_break` — what happens at the boundary.** The OWPML names read the
opposite way to what they suggest:

| `page_break` | Hangul's name (표 → 쪽 경계에서) | measured |
|---|---|---|
| `"TABLE"` (default) | 나눔 | breaks between rows; no row is split |
| `"CELL"` | 셀 단위로 나눔 | breaks inside a cell; a row's lines land on two pages |
| `"NONE"` | 나누지 않음 | the whole table moves to the next page |

**The space under an anchored table is the table's, not the paragraphs'.**
Measured: under a table anchored to its paragraph, Hangul ignores both the
next paragraph's space-before and the space-after of the paragraph holding the
table, and leaves only the table's own bottom `outMargin`. At 1 mm a heading
band — which reaches above its text — was painted over the table's last row.
So at `save()` each anchored table's bottom margin is set from what the next
paragraph asked for (its space-before, plus its shading's top offset), never
under 3 mm: 3 mm before body text, about 5 mm before a chapter band, about 8 mm
before a section heading. An inline table is unaffected — after one, the next
paragraph's space-before is honoured.

`"CELL"` is what most Hangul-made tables carry (258 of 279 on this machine),
because it is Hangul's default for a new table — copying it is how this builder
first got it wrong. In every mode the header row repeats on each page
(`repeatHeader="1"`). `verify()` reports `table_page_break`, the count per
mode, since none of this shows in the XML checks or the text extract.

## Page numbers

`page_number()` writes one `<hp:pageNum pos="BOTTOM_CENTER" formatType="DIGIT"
sideChar="-"/>` control beside `secPr` — "쪽 번호 매기기", the way every
Hangul-made document on this machine numbers its pages (19 files, none with a
master page). No header or footer is needed. `pos` is one of the bottom/top
left/centre/right or inside/outside positions, `fmt` DIGIT, CIRCLED_DIGIT,
ROMAN_*, LATIN_*; `start` restarts the count. `verify()` reports
`page_numbering` — the position, or `"none"`, so a report that went out
unnumbered says so before anyone turns twenty pages to find out.

## Keeping a heading with its text

`para(..., keep_with_next=True)` (and `parapr(keep_with_next=True)`) sets
Hangul's "다음 문단과 함께" (`hh:breakSetting keepWithNext="1"`): the paragraph
goes to the next page rather than end one with its text on the next.
`heading()` sets it, and so does `markdown_chapter.py` for every heading,
sub-heading and caption above a table. Measured on a document of 22 short
sections built twice: without it a section heading stood alone at the foot of
page 2, with it that heading opened page 3 with its text and no page ended on
a heading. No Hangul-made file here sets it — 2807 paragraph shapes, all 0 —
so this is the only evidence it works.

## Covers, contents pages, and where numbering starts

`page_break()` puts the next block on a new page. `hide_page_number()` keeps the
number off the page the next block lands on, and `restart_page_number(1)` makes
that page "1" — the controls Hangul itself writes after a cover. A cover and a
contents page, then a body numbered from 1:

```python
b.page_number()
b.hide_page_number(); b.para("보고서 제목", size=26, align="CENTER", prev_pt=190)
b.page_break(); b.hide_page_number(); b.para("차    례", size=18, align="CENTER")
...
b.page_break(); b.restart_page_number(1); b.heading(1, "1. 개요")
```

A paragraph inside a table cell never takes a pending break or control: it is
built before the paragraph that holds the table.

A contents page cannot carry page numbers: they do not exist until Hangul lays
the document out. `markdown_chapter.py --toc` lists the headings without them.

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
With `--with-style` each note goes on its own `↳ 각주` line instead.

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
- **column widths in `code()`** — in 굴림체, a probe of twelve non-ASCII
  characters (가 漢 ├ ─ │ → ↓ ① · × ° ‘) each took exactly two columns, and
  the tree from #9 padded with `mono_cols` lined up
- **page margins from `page()`** — on a probe page set to 20 mm sides and a
  25 mm first line, Hangul laid out 20.4 mm and 25.4 mm, once `colPr` rode
  with `secPr` (rule 6). Before that every document here was laid out at
  Hangul's defaults whatever `page()` said; the items above were judged on
  their own look, which the margins do not change. The same fix made Hangul's
  table properties show the cell widths, which had read 0.
- **`table(widths_mm=…, align=…)`** — `[40, 60]` in a 160 mm body drew a
  100 mm table, left or centred as asked, and `[30, 50, 80]` drew those three
  column widths
- **tables across pages** (2026-11-16) — anchored tables split between rows
  with `page_break="TABLE"`, split a row's lines with `"CELL"`, moved whole
  with `"NONE"`; inline tables moved whole whatever the setting; header rows
  repeated; a 30-row centred table ran over three pages
- **page numbers** — "- 1 -" … "- 6 -" at the foot of each page
- **cover, contents and numbering** (2026-11-17) — a cover and a contents
  page with no page number, the body starting at "- 1 -"; a table cell
  holding two paragraphs (a `\n` in the cell string, `<br>` in markdown)
  shows them on two lines; with the bottom gap set from the next paragraph,
  a chapter band no longer covers the table above it
- **a form table copied from another document** (2026-11-23) — two tables
  of a government survey form (11 merged cells, a 44-font header) copied into
  the shipped template: borders, shading, mixed faces and weights, blue
  italic guidance, alignments, merges and underlines identical side by side
  with the source; text identical character for character
- **forms with merged cells** (2026-11-23) — two grid_table() forms: a
  3-column merge, 2-row label, a 6-row grey band, a 2×3 heading, a 3-row
  stage, a borderless gap row; underline and superscript in a title; empty
  writing columns given room; separator-row proportions 3:16:3
- **superscript and subscript** (2026-11-22) — H₂O, CO₂, x², 10⁻³ from
  pandoc HTML; the "~" ranges pandoc had made into a subscript restored
- **headings kept with their text**, and a code block clear of the table or
  text after it (see "Keeping a heading with its text")
- **automatic column widths** — three tables, even vs automatic: 22 → 12 and
  19 → 10 lines, headers on one line, Latin identifiers and a URL inside
  their cells; the line estimate matched 31 of 32 rows
- from `markdown_chapter.py`: a hard-wrapped source paragraph comes out as one
  paragraph, a `1. ` item's continuation line lines up under the hanging
  indent, and a bold-led caption is justified

## Known unverified

- **Page header and footer text.** `page()` reserves their margins and
  `page_number()` numbers the pages, but other content there (a running title,
  a logo) needs `hp:header`/`hp:footer`, which this does not write.
- **`page_number(start=…)` and the non-default positions and formats.** Only
  the default "- N -" at the bottom centre has been looked at.
- **A footnote longer than the space left on its page**, and several notes
  competing for one page foot. Only two short notes have been seen.
- **copy_table() on a table with nested tables, numbered paragraphs or a
  style this template lacks.** Handled in code, not yet looked at in Hangul.
- **Everything outside that document's shape.** Nested tables built here,
  equations and multi-section documents have not been exercised.

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
python3 markdown_chapter.py chapter.md out.hwpx ["제목"] [template.hwpx] \
        [--page-numbers] [--cover | --no-cover] [--toc]
```

A report with a front-matter block (`title:`, `subtitle:`, `org:`, `date:`
between `---` lines) gets a cover; `--toc` adds a contents page;
`--page-numbers` numbers the body from 1 with the cover and contents unnumbered.

The template defaults to `template/base.hwpx`.

`examples/html_chapter.py` — the same, from HTML. A manuscript kept elsewhere
usually leaves through pandoc, and pandoc's *markdown* does not fit the subset
above: a table whose cells wrap comes out as a multiline table (dashed rules,
columns marked by spacing measured in display width), a figure as a raw
`<figure>` block, and markdown_chapter scatters the first into body paragraphs
and drops the second. Its *HTML* has no such ambiguity, so this reads HTML,
writes the subset, and builds with `markdown_chapter.convert()`:

```
python3 html_chapter.py in.html out.hwpx [template.hwpx] [--md out.md] [--number-levels] \
        [--page-numbers] [--cover] [--toc]
```

- `data:` images are decoded and placed; they must be PNG, and anything else
  stops the build naming the figure rather than leaving a hole.
- A caption whose label is repeated (`그림 1. 그림 1. …`, as pandoc writes it
  when the caption already carries one) keeps one label.
- `--number-levels` is for exports that put every heading below the title at
  the same tag: `N.M …` becomes `##`, `N.M.K …` `###`, anything else `#`.
- Nested lists are flattened, outer item first. Paragraphs and `<br>` inside a
  table cell stay line breaks in the cell; a `|` in a cell is written `\|`.
- **Text is escaped on the way through**, since the markdown in between is
  markup: `* \` [ | <` in the HTML's text get a backslash, and a paragraph
  that opens like a block (`# `, `- `, `> `, `1. `) has its first character
  escaped. hwpx_build reads CommonMark escapes back as the character. Without
  this `a*b*c` came out italic and a literal `[^1]` stopped the build.
- **pandoc turns two "~" ranges into a subscript** (`337~368호(2017~2025)` →
  `337<sub>368호(2017</sub>2025)`, both tildes gone, and every check passes
  because the HTML has lost them already — #19). A `<sub>` holding a space,
  a Hangul syllable or a bracket is taken for that and put back as `~…~`,
  each one reported on stderr; other `<sub>`/`<sup>` stay real scripts.
  At the source, export with `pandoc -f markdown-subscript`, or write ranges
  as `∼` (U+223C) or `\~`.
- `--cover` builds a cover from pandoc's title block (`h1.title`,
  `.subtitle`, `.author`, `.date`) or `<title>`; `--toc` and
  `--page-numbers` are markdown_chapter's.
- `--md` keeps the intermediate markdown (images beside it), to diff or edit
  before building.
