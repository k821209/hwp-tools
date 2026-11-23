"""hwpx_text — print the body text of a .hwpx, tables as | a | b | rows.

    python3 hwpx_text.py doc.hwpx                # text only
    python3 hwpx_text.py doc.hwpx --with-style   # each paragraph prefixed by
                                                 # the size and face it is set in

Text alone cannot show a formatting regression: a caption that fell into the
body branch and came out at 13 pt instead of 10 has every character in place,
so a diff of the text passes. `--with-style` puts what each paragraph is SET
IN in front of it — `[13.0pt 함초롬바탕]`, or `[13.0pt 함초롬바탕 + 13.0pt
굴림체]` when runs differ — read from the charPr each run references. It does
not judge: which size is right depends on the document, so it only shows.

With `--with-style`, table cells get the same prefix, and a footnote is
printed on its own indented line under its paragraph (`  ↳ 각주 […] text`)
instead of being run into the paragraph text, so its 9.5 pt does not mix into
the paragraph's style. Without the flag the output is unchanged.
"""
import sys, zipfile
import xml.etree.ElementTree as ET

NS_T = '}t'
NS_P = '}p'
NS_TBL = '}tbl'
NS_TR = '}tr'
NS_TC = '}tc'
NOTES = ('}footNote', '}endNote')


def local(tag):
    return tag.rsplit('}', 1)[-1]


def text_of(el):
    parts = []
    for n in el.iter():
        if n.tag.endswith(NS_T) and n.text:
            parts.append(n.text)
    return ''.join(parts)


# ----------------------------------------------------------------- styles

class Styles:
    """charPr id -> 'size face', from header.xml."""

    def __init__(self, header_xml):
        root = ET.fromstring(header_xml)
        faces = {}
        for ff in root.iter():
            if local(ff.tag) == 'fontface' and ff.get('lang') == 'HANGUL':
                for f in ff:
                    if local(f.tag) == 'font':
                        faces[f.get('id')] = f.get('face')
        self.label = {}
        for cp in root.iter():
            if local(cp.tag) != 'charPr':
                continue
            size = int(cp.get('height', '0')) / 100
            face = '?'
            bold = False
            script = ''
            for ch in cp:
                if local(ch.tag) == 'fontRef':
                    face = faces.get(ch.get('hangul'), '?')
                elif local(ch.tag) == 'bold':
                    bold = True
                elif local(ch.tag) == 'supscript':
                    script = ' 위첨자'
                elif local(ch.tag) == 'subscript':
                    script = ' 아래첨자'
                elif local(ch.tag) == 'underline' and ch.get('type', 'NONE') != 'NONE':
                    script = ' 밑줄' + script
            self.label[cp.get('id')] = '%.1fpt %s%s%s' % (size, face, ' 굵게' if bold else '', script)

    def of(self, runs):
        seen = []
        for r in runs:
            lab = self.label.get(r.get('charPrIDRef'), 'charPr %s?' % r.get('charPrIDRef'))
            if lab not in seen:
                seen.append(lab)
        return '[' + ' + '.join(seen) + ']' if seen else '[]'


def split_notes(el):
    """(runs carrying text outside notes, text outside notes, [note elements])."""
    runs, parts, notes = [], [], []

    def go(n):
        if n is not el and n.tag.endswith(NOTES):
            notes.append(n)
            return
        if local(n.tag) == 'run' and any(c.tag.endswith(NS_T) and c.text for c in n):
            runs.append(n)
        if n.tag.endswith(NS_T) and n.text:
            parts.append(n.text)
        for c in n:
            go(c)
    go(el)
    return runs, ''.join(parts), notes


def styled(el, styles, out, indent=''):
    """Append '[style] text' for el, then one line per note it holds."""
    runs, text, notes = split_notes(el)
    v = ' '.join(text.split())
    out.append(indent + (styles.of(runs) + ' ' + v if v else ''))
    for n in notes:
        nruns, ntext, _ = split_notes(n)
        kind = '각주' if n.tag.endswith('}footNote') else '미주'
        out.append('  ↳ %s %s %s' % (kind, styles.of(nruns), ' '.join(ntext.split())))


# ----------------------------------------------------------------- render

def render(el, out, styles=None, depth=0):
    for ch in list(el):
        t = ch.tag
        if t.endswith(NS_TBL):
            out.append('\n<<TABLE>>')
            rows = [r for r in ch if r.tag.endswith(NS_TR)]
            notes = []
            for r in rows:
                cells = [c for c in r if c.tag.endswith(NS_TC)]
                vals = []
                for c in cells:
                    if styles is None:
                        vals.append(' '.join(text_of(c).split()))
                    else:
                        runs, text, cn = split_notes(c)
                        v = ' '.join(text.split())
                        vals.append(styles.of(runs) + ' ' + v if v else '')
                        notes.extend(cn)
                out.append('| ' + ' | '.join(vals) + ' |')
            for n in notes:
                nruns, ntext, _ = split_notes(n)
                out.append('  ↳ 각주 %s %s' % (styles.of(nruns), ' '.join(ntext.split())))
            out.append('<</TABLE>>\n')
        elif t.endswith(NS_P):
            # paragraph may itself contain tables
            has_tbl = any(n.tag.endswith(NS_TBL) for n in ch.iter())
            if has_tbl:
                render(ch, out, styles, depth + 1)
            elif styles is None:
                out.append(' '.join(text_of(ch).split()))
            else:
                styled(ch, styles, out)
        else:
            render(ch, out, styles, depth + 1)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    if len(args) != 1 or any(a not in ('--with-style',) for a in argv[1:] if a.startswith('--')):
        sys.exit('usage: hwpx_text.py <file.hwpx> [--with-style]')
    z = zipfile.ZipFile(args[0])
    root = ET.fromstring(z.read('Contents/section0.xml'))
    styles = Styles(z.read('Contents/header.xml')) if '--with-style' in argv else None
    out = []
    render(root, out, styles)
    lines = []
    prev_blank = False
    for l in out:
        if l.strip() == '':
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        lines.append(l)
    print('\n'.join(lines))


if __name__ == '__main__':
    main(sys.argv)
