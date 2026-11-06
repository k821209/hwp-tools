# OWPML notes — writing a body

What `hwpx-build` had to get right, as observed in documents produced by Hangul
2024 and by reading back what this library writes. Nothing here is from the
published spec. `../../hwpx-edit/reference/OWPML-NOTES.md` covers the structures
you meet when *editing*; these are the ones you only meet when *building*.

## Which parts must agree

Four places describe the same document and all four have to say the same thing:

```
Contents/section0.xml   body — references charPr/paraPr/borderFill ids and
                        binaryItemIDRef image ids
Contents/header.xml     the tables those ids index into, each wrapped in a
                        container carrying itemCnt
Contents/content.hpf    the package manifest — one opf:item per BinData file
BinData/*               the image bytes
```

A `binaryItemIDRef` with no matching `opf:item` is the quiet failure: the XML
parses, the structure checks out, and the figure is simply absent when the file
opens. `verify()` resolves every reference against the manifest for that reason.

`itemCnt` is not decorative either. Append a `charPr` without bumping
`<hh:charProperties itemCnt="…">` and the trailing entries are ignored, so a run
pointing at one falls back to something else — a size and colour you did not ask
for, with no error.

## `hp:secPr` lives in the first paragraph

Page size and margins are not a child of `hs:sec`. They sit inside the first
run of the first paragraph of the section:

```xml
<hs:sec …>
  <hp:p …><hp:run charPrIDRef="…"><hp:secPr …>…</hp:secPr></hp:run>
     <hp:run …><hp:t>first line of text</hp:t></hp:run></hp:p>
```

So a document cannot be empty: there has to be a paragraph for the section
properties to ride in. `_section_xml()` injects it into whatever the first block
turns out to be.

`hp:pagePr` width/height and `hp:margin` are HWPUNIT (1/7200 inch; 1 mm =
7200/25.4 ≈ 283.46). A4 is `59528 × 84186`.

## `hh:paraPr` states its margins twice

```xml
<hh:paraPr id="23" …>
  <hh:align horizontal="CENTER" vertical="BASELINE"/>
  …
  <hp:switch>
    <hp:case hp:required-namespace="…/HwpUnitChar">
      <hh:margin><hc:intent …/><hc:left …/><hc:right …/><hc:prev …/><hc:next …/></hh:margin>
      <hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/>
    </hp:case>
    <hp:default>
      … the same two elements again …
    </hp:default>
  </hp:switch>
  <hh:border borderFillIDRef="1" offsetLeft="0" … />
```

The `hp:switch` picks a branch by namespace support. Replace only the first
`hh:margin` — the obvious thing a `re.sub(..., count=1)` does — and a reader
taking the `hp:default` branch sees the template's values. The symptom is an
indent or line spacing that "did not take", with nothing wrong in the file.

`hc:intent` is the first-line indent; negative gives a hanging indent.
`hc:prev` / `hc:next` are space before/after in 1/100 pt.

## `hh:charPr`

`height` is points × 100. Bold and italic are empty elements, not attributes,
and they sit between `hh:offset` and `hh:underline`:

```xml
<hh:charPr id="8" height="2000" textColor="#000000" … borderFillIDRef="1">
  <hh:fontRef hangul="4" latin="4" …/><hh:ratio …/><hh:spacing …/>
  <hh:relSz …/><hh:offset …/>
  <hh:bold/><hh:italic/>
  <hh:underline …/><hh:strikeout …/><hh:outline …/><hh:shadow …/>
```

`fontRef` values are ids into `hh:fontfaces`, which is per language
(`HANGUL`, `LATIN`, …) with its own id space per `hh:fontface`. Setting all
seven slots to the same id works when the two tables list the same faces in the
same order, which is what Hangul writes.

`borderFillIDRef` on a `charPr` is character shading; on a `paraPr`'s
`hh:border` it is paragraph shading and borders.

## `hh:borderFill`

One element carries both the four edges and the fill:

```xml
<hh:borderFill id="2" …>
  <hh:slash …/><hh:backSlash …/>
  <hh:leftBorder type="NONE" width="0.1 mm" color="#000000"/>
  … right, top, bottom …
  <hh:diagonal …/>
  <hc:fillBrush><hc:winBrush faceColor="none" hatchColor="#999999" alpha="0"/></hc:fillBrush>
```

`faceColor="none"` is no fill. A table needs at least two: one with all four
edges `SOLID` for the grid, one with a `faceColor` for the header row.

## Tables

`hp:tbl` is placed inside a run, like a picture, and with `treatAsChar="1"` it
flows as a character in the paragraph — far less to get right than anchoring it.

```xml
<hp:tbl rowCnt="3" colCnt="4" cellSpacing="0" borderFillIDRef="…" …>
  <hp:sz width="…" widthRelTo="ABSOLUTE" height="…" heightRelTo="ABSOLUTE" protect="0"/>
  <hp:pos treatAsChar="1" …/><hp:outMargin …/><hp:inMargin …/>
  <hp:tr>
    <hp:tc … borderFillIDRef="…">
      <hp:subList …><hp:p …>…</hp:p></hp:subList>
      <hp:cellAddr colAddr="0" rowAddr="0"/>
      <hp:cellSpan colSpan="1" rowSpan="1"/>
      <hp:cellSz width="…" height="…"/>
      <hp:cellMargin left="510" right="510" top="141" bottom="141"/>
```

`cellAddr` is zero-based and must be dense and correct — it is the grid, not a
hint. `cellSz height` and the column widths are layout; widths should sum to the
table's `hp:sz width`, and Hangul adjusts heights on open.

## Images

Inline is much simpler than floating. `treatAsChar="1"` on `hp:pos`, and the
offset/wrap/rendering matrices can all be identity:

```xml
<hp:run charPrIDRef="…">
  <hp:pic id="…" zOrder="0" numberingType="PICTURE" …>
    <hp:offset x="0" y="0"/>
    <hp:orgSz width="W" height="H"/><hp:curSz width="W" height="H"/>
    <hp:flip …/><hp:rotationInfo …/>
    <hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>…</hp:renderingInfo>
    <hc:img binaryItemIDRef="image1" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>
    <hp:imgRect>… four corners at 0,0 → W,H …</hp:imgRect>
    <hp:imgClip left="0" right="W" top="0" bottom="H"/>
    <hp:inMargin …/><hp:imgDim dimwidth="W" dimheight="H"/><hp:effects/>
    <hp:sz width="W" height="H" …/>
    <hp:pos treatAsChar="1" …/><hp:outMargin …/><hp:shapeComment>…</hp:shapeComment>
```

`W`/`H` are HWPUNIT, so the pixel size of the file only matters for keeping the
aspect ratio. PNG dimensions come out of the IHDR at bytes 16–24 — no imaging
library needed.

The manifest entry is separate and easy to forget:

```xml
<opf:item id="image1" href="BinData/image1.png" media-type="image/png" isEmbeded="1"/>
```

(`isEmbeded`, with one `d` — that is how Hangul spells it.)

## Starting from a template

Dropping the template's own `BinData/` and `Preview/` matters. Carrying its
images over leaves files nothing references, and carrying its `Preview/` leaves
a thumbnail and preview text describing a document that no longer exists —
which is what a file manager and Hangul's own open dialog show.
