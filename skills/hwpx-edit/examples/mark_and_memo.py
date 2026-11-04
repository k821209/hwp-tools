#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A full pass over a .hwpx: mark revisions in colour, attach memos, verify.

    python3 mark_and_memo.py report.hwpx report_revised.hwpx

Everything is driven by anchor strings you would find with:

    python3 ../scripts/hwpx_text.py report.hwpx | less

Adapt EDITS and MEMOS to the document. The ordering below is the part to keep:
colour audit first, strip_linesegarray last, memos before the strip.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from hwpx_edit import Hwpx  # noqa: E402

# Anchor strings must each lie inside a single <hp:t>. If a call raises, run
# doc.runs_in(anchor) — the string probably straddles two runs.
EDITS = [
    # (anchor, text appended to the end of that paragraph)
    ("공동연구 수행중", " 후속 성과로 공동저술 논문 1편을 게재하였음."),
]

NEW_PARAGRAPHS = [
    # (anchor, text, paraPrIDRef, styleIDRef, charPrIDRef) — copy the three ids
    # from a sibling paragraph so the new one inherits its look.
    ("향후 계획", "신규 협약 체결을 추진 중임.", "231", "20", "67"),
]

MEMOS = [
    # (anchor, memo body)
    ("3,500천원", "집계표의 합계와 맞지 않습니다. 어느 수치가 맞는지 확인이 필요합니다."),
]


def main(src, dst):
    doc = Hwpx(src)

    print("colours already in the document:")
    for color, use in doc.color_usage().items():
        print(f"  {color:10s} runs {use['runs']:5d}  chars {use['chars']:7d}")
    mark = doc.pick_unused_color()
    print(f"marking revisions in {mark}\n")

    for anchor, text in EDITS:
        doc.append_run(anchor, text, color=mark)
        print(f"  + run after  {anchor!r}")
    for anchor, text, pp, sid, cp in NEW_PARAGRAPHS:
        doc.insert_paragraph_after(anchor, text, pp, sid, cp, color=mark)
        print(f"  + paragraph after  {anchor!r}")
    for anchor, body in MEMOS:
        doc.add_memo(anchor, body, author="reviewer")
        print(f"  + memo on  {anchor!r}")

    # Last, and after the memos: a memo makes its paragraph a touched one.
    print(f"\nstripped line cache from {doc.strip_linesegarray()} paragraphs")
    doc.save(dst)

    print(f"\nwrote {dst}")
    for k, v in Hwpx.verify(dst, expect_memos=len(MEMOS)).items():
        print(f"  {k:24s} {v}")
    print("\nStructural checks only. Open it in Hangul to confirm it loads,")
    print("and to see the colour and memos — nothing here can render .hwpx.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
