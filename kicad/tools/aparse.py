import olefile,struct,collections
def records(path,stream='FileHeader'):
    o=olefile.OleFileIO(path);d=o.openstream(stream).read();i=0;out=[]
    while i+4<=len(d):
        n=struct.unpack('<I',d[i:i+4])[0]&0xFFFFFF;i+=4
        b=d[i:i+n];i+=n
        if b[:1]==b'\x01' or not b.startswith(b'|'):
            if not b.startswith(b'|'): continue
        s=b.rstrip(b'\0').decode('latin-1');r={}
        for kv in s.split('|'):
            if '=' in kv:
                k,v=kv.split('=',1);r[k.upper()]=v
        out.append(r)
    return out
