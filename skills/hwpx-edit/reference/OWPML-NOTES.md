# OWPML notes

Structures behind the rules in `SKILL.md`, as observed in documents produced by
Hangul (한글) 2024. Nothing here is from the published spec; it is what the
files actually contain.

## Archive

A `.hwpx` is a plain zip. A long report with embedded images had 30 entries:

```
version.xml  settings.xml  META-INF/{container,manifest}.xml
Contents/{header.xml, section0.xml, content.hpf}
BinData/*  (19 — images)     Preview/*  (2)
```

`Contents/section0.xml` is the body — several megabytes of XML and thousands of
paragraphs in a document of that size. `Contents/header.xml` holds the style
tables. Replace those two and everything else carries over untouched.

## Paragraph and run

```xml
<hp:p id="…" paraPrIDRef="85" styleIDRef="0" …>
  <hp:run charPrIDRef="93"><hp:t>text</hp:t></hp:run>
  <hp:run charPrIDRef="99"><hp:t>more</hp:t></hp:run>
  <hp:linesegarray><hp:lineseg textpos="0" vertpos="…" horzsize="…" …/></hp:linesegarray>
</hp:p>
```

A sentence is usually split across several runs — each formatting change starts
a new one. A search string spanning a run boundary will not match any single
`<hp:t>`.

`hp:p` **nests**: table cells and memo subLists each contain their own.

### `hp:linesegarray`

One `hp:lineseg` per rendered line, holding `textpos` (character offset where
the line starts), `vertpos`, `horzsize`, flags. It is a layout cache.

Edit a paragraph's text and leave this stale, and Hangul rejects the file with
the document-security dialog. It does not warn about layout; it reports possible
tampering. Observed states:

| paragraph | segs left stale | result |
|---|---|---|
| any paragraph whose text grew | fewer than it now needs | text crammed onto those lines |
| any edited paragraph | count no longer matches | **file refused** |


Remove the element and Hangul recomputes on open. Opening and saving such a
file in Hangul writes a fresh, correct `linesegarray` for every paragraph.

## Character properties

```xml
<hh:charProperties itemCnt="223">
  <hh:charPr id="0" height="1150" textColor="#000000" …>
    <hh:fontRef …/><hh:ratio hangul="100" …/><hh:spacing hangul="0" …/>…
  </hh:charPr>
```

To mark text, clone the charPr used at that spot, change only `textColor`, give
it a fresh id, append it, and bump `itemCnt`. Size and face then match the
surrounding text exactly.

**Hangul merges identical formatting on save.** If the clone's colour equals the
source's, the clone is indistinguishable and adjacent runs are concatenated into
one `<hp:t>`, after which an addition can no longer be told from the original
text it was appended to. A document that already marks earlier revisions in red
is the usual trap. Colours often free in ordinary documents: `#FF00FF`,
`#00B050`, `#7030A0`, `#00B0F0`.

Hangul also **renumbers and deduplicates** charPr ids when it saves, so
`itemCnt` can fall. Never cache ids across a user's save.

## Tables

```xml
<hp:tbl rowCnt="12" colCnt="6" …>
  <hp:tr>
    <hp:tc …><hp:subList …><hp:p …><hp:run …><hp:t>cell</hp:t></hp:run></hp:p></hp:subList>
      <hp:cellAddr colAddr="0" rowAddr="11"/><hp:cellSpan …/><hp:cellSz …/>
    </hp:tc>
    …
```

Adding a row: clone the last `hp:tr`, set each `cellAddr rowAddr` to the next
index, bump `rowCnt`. A template cell may hold **several** paragraphs — rebuild
its subList as exactly one, or the extras survive and their text is appended to
the new value.

`cellSz height` and the linesegs are layout; Hangul recomputes them.

## Memos

Not `hp:memo`. A memo is a field wrapping a span of text:

```xml
<hp:run charPrIDRef="32">
  <hp:t>before </hp:t>
  <hp:ctrl>
    <hp:fieldBegin id="1000000001" type="MEMO" editable="1" dirty="1" zorder="1"
                   fieldid="2000000001" metaTag="">
      <hp:parameters cnt="7" name="">
        <hp:integerParam name="Prop">0</hp:integerParam>
        <hp:stringParam name="Command">MEMO/65535/1/0/0/author/\;;</hp:stringParam>
        <hp:stringParam name="ID">memo1</hp:stringParam>
        <hp:integerParam name="Number">1</hp:integerParam>
        <hp:stringParam name="Author">author</hp:stringParam>
        <hp:stringParam name="MemoShapeIDRef">65535</hp:stringParam>
        <hp:stringParam name="CreateDateTime">2026-01-01T00:00:00Z</hp:stringParam>
      </hp:parameters>
      <hp:subList …>
        <hp:p id="0" paraPrIDRef="44" styleIDRef="13" …>
          <hp:run charPrIDRef="64"><hp:t>memo body</hp:t></hp:run>
        </hp:p>
      </hp:subList>
    </hp:fieldBegin>
  </hp:ctrl>
  <hp:t>anchored text</hp:t>
  <hp:ctrl><hp:fieldEnd beginIDRef="1000000001" fieldid="2000000001"/></hp:ctrl>
  <hp:t> after</hp:t>
</hp:run>
```

- `MemoShapeIDRef=65535` is the default shape. A document with memos but **no**
  `hh:memoProperties` in its header is normal; nothing has to be added there.
- `styleIDRef=13` is the built-in `메모` / `Memo` paragraph style
  (`paraPrIDRef=44`, `charPrIDRef=64`).
- `fieldid` is **not** unique — two `FORMULA` fields in one document were seen
  sharing one. Pairing is by `fieldEnd beginIDRef` → `fieldBegin id`.
- The two numbers inside `Command` appear to be instance ids; varying them, or
  writing zeros, caused no problem.

Other field types occur in the same position — `FORMULA` for table
calculations, for example. Do not assume every `hp:fieldBegin` is a memo.

## `.hwp` 5.x (the older binary format)

OLE compound file. `FileHeader[36] & 1` is the compression flag; `BodyText/*`
streams are raw-deflate (`zlib.decompress(data, -15)`). Records are
`tag | level<<10 | size<<20`, with `size == 0xFFF` meaning a following uint32
holds the real size. `HWPTAG_PARA_TEXT` is tag 67; its payload is UTF-16LE with
control characters 1–31, several of which occupy 8 words rather than one.
`scripts/hwp5_text.py` implements the minimum needed to read text out.
