# hwp-tools

Tools for working with Hangul (한글) documents — `.hwpx` and `.hwp` — from
Python, without Hangul or LibreOffice installed.

Standard library only (except `olefile`, needed just to read the older `.hwp`
binary format).

## Why

A `.hwpx` is a zip of OWPML XML, so it can be edited directly: copy the
archive, swap `Contents/section0.xml`, and formatting and embedded images
survive byte-for-byte. No conversion, no round-trip loss.

The catch is that several of the structures are undocumented, and getting one
wrong produces a file that passes every structural check and still fails — most
memorably by making Hangul refuse to open it as possibly tampered with. The
rules in each skill are those failures, written down.

## Contents

### `skills/hwpx-edit/`

Edit a `.hwpx` in place: insert paragraphs, append runs, add table rows, fill
the empty cells of a form with text or a picture, insert pictures, mark
revisions in an unused colour, attach margin memos and read the ones a
reviewer left (`memos()`).

```python
from hwpx_edit import Hwpx

doc = Hwpx("report.hwpx")
mark = doc.pick_unused_color()
doc.append_run("공동연구 수행중", " 후속 논문 1편을 게재하였음.", color=mark)
doc.add_memo("3,500천원", "집계표의 합계와 맞지 않습니다.", author="reviewer")
doc.fill_cell("소명 내용", ["○ 첫째 항목", "○ 둘째 항목"])      # a form cell beside its label
doc.insert_image_after("결과 요약", "fig1.png", width_mm=150)   # BinData + manifest handled
doc.strip_linesegarray()          # required, and last
doc.save("report_revised.hwpx")
Hwpx.verify("report_revised.hwpx", expect_memos=1)
```

Read [`skills/hwpx-edit/SKILL.md`](skills/hwpx-edit/SKILL.md) before using it —
five rules, each one a real failure.
[`reference/OWPML-NOTES.md`](skills/hwpx-edit/reference/OWPML-NOTES.md) records
the structures behind them: paragraphs and runs, the `hp:linesegarray` line
cache, character properties and how Hangul merges them on save, table rows, and
how a memo is actually stored (`hp:fieldBegin type="MEMO"`, not `hp:memo`).

### `skills/hwpx-build/`

Write a new `.hwpx`: headings, paragraphs, tables, images, code blocks,
pull-quotes, reference lists, footnotes, page numbers, a cover and contents
page. Tables size their columns to their content and split between rows
across pages; inline code, super/subscript and footnote marks are read in
running text.

```python
from hwpx_build import HwpxBuilder

b = HwpxBuilder()                         # shipped blank template, or HwpxBuilder("yours.hwpx")
b.page(margins_mm={"top": 15, "bottom": 15, "left": 25, "right": 25})
b.heading(1, "제3장 진핵생물 데이터 분석")
b.para("본문입니다. **굵게** 와 *기울임* 이 삽니다.", face="함초롬바탕")
b.table([["항목", "원핵생물", "진핵생물"], ["인트론", "없음", "있음"]])
b.para("검수자 간 일치도는 κ = 0.638 이었다.[^kappa]")
b.footnotes({"kappa": "Cohen's κ 는 우연 일치를 뺀 일치도다."})
b.image("fig3-1.png", width_mm=120)
b.page_number()                           # "- 1 -" at the foot of each page
b.save("out.hwpx")
HwpxBuilder.verify("out.hwpx", expect_images=1, expect_tables=1)
```

It does **not** write `header.xml` from scratch — that is where files that pass
every check and still refuse to open come from. A template `.hwpx` supplies the
font table, border fills and style tables, and the builder clones the character
and paragraph properties it needs out of it. Only `Contents/section0.xml` is
written fresh. A blank one ships as
[`template/base.hwpx`](skills/hwpx-build/template/base.hwpx) and is used when
none is named; `HwpxBuilder.check_template()` tells you whether another is fit
to use.

Read [`skills/hwpx-build/SKILL.md`](skills/hwpx-build/SKILL.md) — six rules,
and a note on what has *not* been verified.
[`examples/markdown_chapter.py`](skills/hwpx-build/examples/markdown_chapter.py)
converts a markdown chapter or report, figures and all — with a front-matter
title it makes a cover, and `--toc` / `--page-numbers` add a contents page and
page numbers;
[`examples/html_chapter.py`](skills/hwpx-build/examples/html_chapter.py) does
the same from HTML (pandoc's, say), whose tables and figures markdown_chapter
cannot read as pandoc writes them in markdown.

```
python3 markdown_chapter.py report.md out.hwpx --page-numbers --toc
```

Also, under `hwpx-edit/`:
- `scripts/hwpx_text.py` — `.hwpx` → body and table text, for locating anchors
  and as a diff baseline. `python3 hwpx_text.py file.hwpx`; with
  `--with-style` each paragraph and cell is prefixed by the size, face, weight
  and super/subscript it is set in, so a formatting regression shows in a diff.
- `scripts/hwp5_text.py` — `.hwp` 5.x → text (OLE + raw deflate + record scan)

## The limit worth knowing before you start

**Nothing here can open or render a `.hwpx`.** LibreOffice cannot load the
format (`soffice --convert-to pdf` → `source file could not be loaded`), so
there is no automated way to confirm that a file you produced opens, that a
colour looks right, or that a table did not overflow. `Hwpx.verify()` reports
structure only and returns `opens_in_hangul: None` deliberately. Treat a
structural pass as a structural pass, and have a person open the result in
Hangul.

What the checks do report is the failures that are otherwise silent: a table
or picture outside its run, a page setup Hangul would ignore
(`secpr_with_colpr`), no page numbers, pictures missing their file or manifest
entry, and — from the builder — every font the template lacked
(`font_fallbacks`). Each skill's SKILL.md lists what a person has confirmed in
Hangul and what has not been looked at yet.

## Skills as skills

`skills/*/SKILL.md` follows the Claude Code / Agent Skills layout, so the
directory can be dropped into `~/.claude/skills/` or a project's
`.claude/skills/` and invoked by name. Copy both skills together:
hwpx-edit's picture insertion uses hwpx-build's picture code from the
directory beside it. It also reads fine as documentation if
you are not using an agent.

## Licence

MIT — see [LICENSE](LICENSE).
