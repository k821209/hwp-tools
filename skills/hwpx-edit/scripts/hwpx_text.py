import sys, zipfile, re
import xml.etree.ElementTree as ET

NS_T = '}t'
NS_P = '}p'
NS_TBL = '}tbl'
NS_TR = '}tr'
NS_TC = '}tc'

def walk(el, out):
    tag = el.tag
    if tag.endswith(NS_TBL):
        out.append('\n[TABLE]')
        for tr in el.iter():
            pass
    for ch in list(el):
        walk(ch, out)

def text_of(el):
    parts=[]
    for n in el.iter():
        if n.tag.endswith(NS_T) and n.text:
            parts.append(n.text)
    return ''.join(parts)

def render(el, out, depth=0):
    for ch in list(el):
        t = ch.tag
        if t.endswith(NS_TBL):
            out.append('\n<<TABLE>>')
            rows = [r for r in ch if r.tag.endswith(NS_TR)]
            for r in rows:
                cells = [c for c in r if c.tag.endswith(NS_TC)]
                vals = []
                for c in cells:
                    v = ' '.join(text_of(c).split())
                    vals.append(v)
                out.append('| ' + ' | '.join(vals) + ' |')
            out.append('<</TABLE>>\n')
        elif t.endswith(NS_P):
            # paragraph may itself contain tables
            has_tbl = any(n.tag.endswith(NS_TBL) for n in ch.iter())
            if has_tbl:
                render(ch, out, depth+1)
                # also any loose text
                pass
            else:
                v = ' '.join(text_of(ch).split())
                out.append(v)
        else:
            render(ch, out, depth+1)

path = sys.argv[1]
z = zipfile.ZipFile(path)
data = z.read('Contents/section0.xml')
root = ET.fromstring(data)
out=[]
render(root, out)
lines=[]
prev_blank=False
for l in out:
    if l.strip()=='' :
        if prev_blank: continue
        prev_blank=True
    else:
        prev_blank=False
    lines.append(l)
print('\n'.join(lines))
