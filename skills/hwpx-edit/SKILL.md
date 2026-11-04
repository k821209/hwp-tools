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
their text running straight on from the value you wrote.

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
doc.add_memo("3,500천원", "집계표의 합계와 맞지 않습니다.", author="reviewer")

doc.strip_linesegarray()               # REQUIRED, last
doc.save("report_revised.hwpx")
Hwpx.verify("report_revised.hwpx", expect_memos=1)
```

An anchor must lie inside a single `<hp:t>`. Call `runs_in()` when a match
fails — the string probably straddles two runs.

## Memos

A memo is not an `hp:memo` element. It is `hp:fieldBegin type="MEMO"` carrying
its text in a subList, with `fieldBegin … anchor … fieldEnd` wrapping the span,
all inside one `hp:run`. With `MemoShapeIDRef=65535` (the default shape) no
`hh:memoProperties` is needed, so header styles stay untouched. The memo body
paragraph uses `paraPrIDRef=44 / styleIDRef=13 ("메모") / charPrIDRef=64`.

## Verifying

`Hwpx.verify()` checks: zip integrity, both XML parse, `charProperties itemCnt`
against the real count, duplicate charPr ids, duplicate zip entries, memo
field/end pairing, and stale `linesegarray` on memo paragraphs. Diff the
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
- `scripts/hwp5_text.py` — `.hwp` 5.x → text (olefile + zlib, `HWPTAG_PARA_TEXT`).
  Needs `olefile`; the rest of this skill needs nothing.
- `reference/OWPML-NOTES.md` — the structures these rules come from.
