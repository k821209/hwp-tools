---
name: hwpx-edit
description: Edit a Hangul .hwpx document in place without Hangul or LibreOffice — insert paragraphs, append runs, add table rows, mark revisions in colour, attach margin memos. Use when asked to revise, annotate or mark up a .hwpx/.hwp file, when a Korean report or form has to be updated programmatically, or when a hwpx you produced is rejected by Hangul as "문서가 손상되었거나 변조되었을 가능성이 있습니다".
---

# hwpx-edit

A `.hwpx` is a zip of OWPML XML. Copy the archive, swap only
`Contents/section0.xml` (and `header.xml` when styles change), and formatting
plus BinData images survive byte-for-byte — no Hangul, no LibreOffice, no
conversion loss.

`scripts/hwpx_edit.py` is the library. Standard library only, Python 3.8+.

## The five rules

Each one is a failure that shipped before it was understood. Breaking any of
them produces a file that passes every structural check and still fails.

**1. Strip `hp:linesegarray` from every paragraph you touch, and strip it last.**
It caches where each line of a paragraph breaks. Edit the text and leave it, and
Hangul refuses the file: *"문서가 손상되었거나 변조되었을 가능성이 있습니다. 이
문서를 불러오려면 [문서 보안 설정]을 [낮음]으로 설정해야 합니다."* Filling the
gap with a copy from a sibling paragraph is the same error. Remove it and Hangul
lays the paragraph out afresh on open, giving the paragraph as many lines as its
text now needs. Adding a memo makes its paragraph a touched one,
so `strip_linesegarray()` runs **after** `add_memo()`, never before.
([Hancom forum](https://forum.developer.hancom.com/t/hwpx-section0-xml/2414))

**2. Find the innermost paragraph, not the outermost.**
`hp:p` nests — table cells and memo subLists hold their own — so
`<hp:p .*?</hp:p>` cannot find a boundary, and a depth scan that returns the
*outermost* match makes a paragraph holding a table swallow the entire table —
stripping the line cache from every cell in it. `_para_span()` returns
the innermost, and only the tail `linesegarray` (after all runs, before
`</hp:p>`) is removed.

**3. Count the colours already in the document before choosing one.**
`color_usage()` first, then `pick_unused_color()`. Reusing a colour the file
already carries does more than blur the distinction: if the charPr you clone is
already that colour, the clone is a no-op and Hangul merges your run into the
neighbouring original on its next save, concatenating both into a single
`<hp:t>` — after which the two cannot be separated.

**4. Rebuild a cloned table cell as exactly one paragraph.**
A template cell may hold several. Replace only the first and the rest survive,
their text running straight on from the value you wrote. `fill_cell()` follows
the same rule when it writes into an empty form cell: the cell's subList is
rebuilt as exactly one paragraph per item you pass, whatever empty paragraphs
the form left in it.

**5. Never append to a copied archive with `ZipFile(..., "a")`.**
zipfile appends a *second* entry under the same name rather than replacing, and
a reader may take the stale one. `save()` rewrites the archive, carrying every
untouched entry over with its original `ZipInfo`.

## Use

```python
from hwpx_edit import Hwpx

doc = Hwpx("report.hwpx")
doc.color_usage()                      # {'#000000': {...}, '#FF0000': {...}}
mark = doc.pick_unused_color()         # '#FF00FF'

doc.runs_in("공동연구 수행중")          # see run boundaries before anchoring
doc.append_run("공동연구 수행중", " 후속 논문 1편을 게재하였음.", color=mark)
doc.insert_paragraph_after("향후 계획", "신규 항목.", "231", "20", "67", color=mark)
doc.split_run("111", "논문 ", "10", "편임. ", color=mark)   # recolour one value
doc.add_table_row("기존 셀 텍스트", ["11", "값", ...], color=mark)
doc.fill_cell("온라인프로그램 대체 가능 여부",       # a form: the cell beside a label
              ["○ 대체 불가함. …", "○ 포스터 세션 …"])  # one paragraph per item
doc.insert_image_after("결과 요약", "fig1.png", width_mm=150)   # a picture after a paragraph
doc.fill_cell_image("첨부 그림", "fig2.png", col=1)             # an empty cell, at its width
doc.add_memo("3,500천원", "집계표의 합계와 맞지 않습니다.", author="reviewer")

doc.strip_linesegarray()               # REQUIRED, last
doc.save("report_revised.hwpx")
Hwpx.verify("report_revised.hwpx", expect_memos=1)
```

An anchor must lie inside a single `<hp:t>`. Call `runs_in()` when a match
fails — the string probably straddles two runs.

## Filling a form

A form is filled, not extended: its cells exist and are empty, and what it
needs is the cell beside a label. `fill_cell(label, text, col=1)` finds the row
whose cell holds `label` and writes into the cell at column `col`.

- **The column is the cell's `hp:cellAddr colAddr`**, not its position among
  the row's cells, so a row with a merged cell still resolves. A cell's own
  address is the *last* `cellAddr` inside it — any earlier one belongs to a
  table nested in the cell — and its subList closes last for the same reason.
- **A list is one paragraph per item**, each taking the paragraph and character
  properties of the cell's first paragraph, so the form's own alignment and
  face carry over.
- **A cell that already has text is refused** unless `overwrite=True`. A form
  is filled once; quietly replacing an answer is worse than failing.
- Rows and cells are found by depth, not by regex: a cell can hold a table, so
  `<hp:tr>.*?</hp:tr>` finds the wrong boundary.

Confirmed in Hangul 2024 by a person (2026-11-16): a 6 × 3 form
(「공무국외출장 관련 자료 제출」) with five cells filled, two to four `○`
items each — opens without a dialog, every item on its own line, the form's
justified 11 pt 맑은 고딕 kept, borders and shading unchanged.

## Pictures

`insert_image_after(needle, png, width_mm=…)` puts a PNG in a new paragraph
after the one holding `needle` — inside a table cell if that paragraph is in
one — taking the anchor's paragraph, style and character properties unless
given (pass a centred paragraph's `para_pr` to centre it).
`fill_cell_image(label, png, col=…)` is fill_cell() for a picture: the cell is
found by `colAddr`, refused if it has text or a picture unless `overwrite`, and
the picture is as wide as the cell inside its margins unless `width_mm` says.

A picture is three things, and these keep them together:

- **the `hp:pic`**, inside an `hp:run` (hwpx-build's rule 4) — the same XML
  `hwpx_build.pic_xml()` writes, imported rather than copied, so hwpx-build has
  to sit beside hwpx-edit;
- **the file, `BinData/imageN.png`**, with an id no existing item uses (a form
  with `image1`–`image4` gets `image5`);
- **the manifest item in `Contents/content.hpf`**.

`save()` writes the files and the manifest in the same rewrite as the section,
so nothing has to reopen the saved archive — which is where rule 5's
`ZipFile(..., "a")` failure waits. Each picture's object id is one above every
object id in the section; Hangul's own are large and a duplicate is the kind of
thing it resolves by dropping one. PNG only — the size is read from the IHDR.

`verify()` reports `images`: files, manifest items, referenced ids, pictures,
any reference not in the manifest, any manifest item without its file, any
picture outside a run, and `ok`. A picture missing one of its three parts
passes every other check here.

Confirmed in Hangul 2024 by a person (2026-11-22): a 2:1 PNG put after a body
paragraph, under the text in a table cell, and into an empty merged cell at the
cell's width — the form opened without a dialog, all three at the right
proportions, the tables intact.

## Memos

A memo is not an `hp:memo` element. It is `hp:fieldBegin type="MEMO"` carrying
its text in a subList, with `fieldBegin … anchor … fieldEnd` wrapping the span,
all inside one `hp:run`. With `MemoShapeIDRef=65535` (the default shape) no
`hh:memoProperties` is needed, so header styles stay untouched. The memo body
paragraph uses `paraPrIDRef=44 / styleIDRef=13 ("메모") / charPrIDRef=64`.

### Reading them

`memos()` is the other half: every memo in document order, as a dict —
`number`, `author`, `created`, `body`, `anchor` (the text it marks), `para` (the
paragraph it sits in), `id`, `problem`. `python3 hwpx_edit.py file.hwpx --memos`
prints them as JSON. Both `anchor` and `para` are there because a short anchor
("세 라벨") can occur in many places and a paragraph alone does not say which
part is meant.

Three things about Hangul's own memo markup that a hand-written reader gets
wrong, each found on a reviewer's file:

- **The span is paired by id.** `fieldEnd` carries `beginIDRef` and no `id` of
  its own, and every memo in a file may share one `fieldid`. A pattern
  expecting `<hp:fieldEnd id="…" beginIDRef=…` matches nothing — and because the
  bodies still read fine, every span silently came back empty.
- **Memos nest.** One memo's span can hold another memo whole; the outer
  anchor leaves out the inner memo's body and keeps the text the inner marks.
- **Text in a memo body is not the document.** A note quoting the phrase it
  marks would be found first by a plain search; `_find()` — and so every edit
  that takes a needle — skips memo bodies, and `runs_in()` and `append_run()`
  look only at the paragraph's own runs.

A memo whose span cannot be read gets `anchor: None` and the reason in
`problem` (no fieldEnd, two, or one before its fieldBegin) — never an empty
string, which would read as "a memo on nothing" when the reading failed.

Needles are matched as the reader types them: `"R&D"` finds `R&amp;D` in the
XML, and `add_memo`, `split_run` and the rest write text back escaped.

## Verifying

`Hwpx.verify()` checks: zip integrity, both XML parse, `charProperties itemCnt`
against the real count, duplicate charPr ids, duplicate zip entries, memo
field/end pairing, stale `linesegarray` on memo paragraphs, and that every
picture has its file, its manifest item and a run around it. Diff the
extracted text against a baseline too — `scripts/hwpx_text.py` renders body and
tables, so `diff` shows exactly which lines moved.

**Passing all of it does not mean the file opens.** LibreOffice cannot load
`.hwpx` (`soffice --convert-to pdf` → `source file could not be loaded`), so
nothing here can render or open the result. `verify()` returns
`opens_in_hangul: None` on purpose — that question is answered by a person
opening it in Hangul, and a structural pass must never be reported as "it
opens".

## Also here

- `scripts/hwpx_text.py` — `.hwpx` → body and table text (tables as pipe rows).
  For locating anchors and as a diff baseline. Also runnable: `hwpx_text.py f.hwpx`.
  `--with-style` prefixes each paragraph and cell with its size, face,
  weight and super/subscript (`[13.0pt 함초롬바탕]`) and puts footnotes on their own lines, so an
  edit that changed formatting but not text shows up in the diff. Without the
  flag the output is byte-for-byte what it was.
- `scripts/hwp5_text.py` — `.hwp` 5.x → text (olefile + zlib, `HWPTAG_PARA_TEXT`).
  Needs `olefile`; the rest of this skill needs nothing.
- `reference/OWPML-NOTES.md` — the structures these rules come from.
