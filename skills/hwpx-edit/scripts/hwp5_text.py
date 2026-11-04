import sys, zlib, struct, olefile
f = olefile.OleFileIO(sys.argv[1])
# compression flag
hdr = f.openstream('FileHeader').read()
compressed = bool(hdr[36] & 1)
streams = sorted([n for n in f.listdir() if n[0]=='BodyText'], key=lambda n:n[1])
out=[]
for s in streams:
    d = f.openstream(s).read()
    if compressed:
        d = zlib.decompress(d, -15)
    i=0
    while i < len(d)-4:
        h = struct.unpack('<I', d[i:i+4])[0]
        tag = h & 0x3ff; lvl=(h>>10)&0x3ff; size=(h>>20)&0xfff
        i+=4
        if size==0xfff:
            size = struct.unpack('<I', d[i:i+4])[0]; i+=4
        body = d[i:i+size]; i+=size
        if tag==67:  # HWPTAG_PARA_TEXT
            chars=[]
            j=0
            while j < len(body)-1:
                c = struct.unpack('<H', body[j:j+2])[0]
                if c in (0,10,13):
                    chars.append('\n'); j+=2
                elif 1 <= c <= 31:
                    # control char: inline(8..) occupies 8 words for some
                    if c in (1,2,3,11,12,14,15,16,17,18,21,22,23):
                        j+=16
                    else:
                        j+=2
                    chars.append(' ')
                else:
                    chars.append(chr(c)); j+=2
            out.append(''.join(chars))
print('\n'.join(out))
