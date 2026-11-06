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

Edit a `.hwpx` in place: insert paragraphs, append runs, add table rows, mark
revisions in an unused colour, attach margin memos.

```python
from hwpx_edit import Hwpx

doc = Hwpx("report.hwpx")
mark = doc.pick_unused_color()
doc.append_run("공동연구 수행중", " 후속 논문 1편을 게재하였음.", color=mark)
doc.add_memo("3,500천원", "집계표의 합계와 맞지 않습니다.", author="reviewer")
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
pull-quotes, reference lists.

```python
from hwpx_build import HwpxBuilder

b = HwpxBuilder("template.hwpx")          # a real Hangul file, for its header
b.page(margins_mm={"top": 15, "bottom": 15, "left": 25, "right": 25})
b.heading(1, "제3장 진핵생물 데이터 분석")
b.para("본문입니다. **굵게** 와 *기울임* 이 삽니다.", face="함초롬바탕")
b.table([["항목", "원핵생물", "진핵생물"], ["인트론", "없음", "있음"]])
b.image("fig3-1.png", width_mm=120)
b.save("out.hwpx")
HwpxBuilder.verify("out.hwpx", expect_images=1, expect_tables=1)
```

It does **not** write `header.xml` from scratch — that is where files that pass
every check and still refuse to open come from. A template `.hwpx` supplies the
font table, border fills and style tables, and the builder clones the character
and paragraph properties it needs out of it. Only `Contents/section0.xml` is
written fresh.

Read [`skills/hwpx-build/SKILL.md`](skills/hwpx-build/SKILL.md) — five rules,
and a note on what has *not* been verified.
[`examples/markdown_chapter.py`](skills/hwpx-build/examples/markdown_chapter.py)
converts a markdown chapter, figures and all.

Also, under `hwpx-edit/`:
- `scripts/hwpx_text.py` — `.hwpx` → body and table text, for locating anchors
  and as a diff baseline. `python3 hwpx_text.py file.hwpx`
- `scripts/hwp5_text.py` — `.hwp` 5.x → text (OLE + raw deflate + record scan)

## The limit worth knowing before you start

**Nothing here can open or render a `.hwpx`.** LibreOffice cannot load the
format (`soffice --convert-to pdf` → `source file could not be loaded`), so
there is no automated way to confirm that a file you produced opens, that a
colour looks right, or that a table did not overflow. `Hwpx.verify()` reports
structure only and returns `opens_in_hangul: None` deliberately. Treat a
structural pass as a structural pass, and have a person open the result in
Hangul.

## Skills as skills

`skills/*/SKILL.md` follows the Claude Code / Agent Skills layout, so the
directory can be dropped into `~/.claude/skills/` or a project's
`.claude/skills/` and invoked by name. It also reads fine as documentation if
you are not using an agent.

## Licence

MIT — see [LICENSE](LICENSE).
