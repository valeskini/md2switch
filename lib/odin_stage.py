"""Minimal Odin binary writer for Muse Dash StageInfo.musicDatas (+ delay, dialogEvents tail)."""
import struct
from decimal import Decimal

def dec_bytes(d) -> bytes:
    d=Decimal(d)
    sign,digits,exp=d.as_tuple()
    coef=int(''.join(map(str,digits))) if digits else 0
    scale=-exp if exp<0 else 0
    if exp>0: coef*=10**exp
    while coef>=1<<96:  # reduce precision if needed
        coef//=10; scale-=1
    assert 0<=scale<=28
    flags=(scale<<16)|(0x80000000 if sign else 0)
    lo=coef&0xFFFFFFFF; mid=(coef>>32)&0xFFFFFFFF; hi=(coef>>64)&0xFFFFFFFF
    return struct.pack('<IIII',flags,hi,lo,mid)

class W:
    def __init__(s): s.b=bytearray(); s.types={}
    def str_(s,t): s.b+=b'\x01'+struct.pack('<i',len(t))+t.encode('utf-16-le')
    def name(s,tag,n): s.b.append(tag); s.str_(n)
    def type_(s,tn):
        if tn in s.types: s.b.append(0x30); s.b+=struct.pack('<i',s.types[tn])
        else:
            i=len(s.types); s.types[tn]=i; s.b.append(0x2F); s.b+=struct.pack('<i',i); s.str_(tn)
    def i16(s,n,v): s.name(0x13,n); s.b+=struct.pack('<h',v)
    def i32(s,n,v): s.name(0x17,n); s.b+=struct.pack('<i',v)
    def dec(s,n,v): s.name(0x23,n); s.b+=dec_bytes(v)
    def bool_(s,n,v): s.name(0x2B,n); s.b.append(1 if v else 0)
    def null(s,n): s.name(0x2D,n)
    def strn(s,n,v):
        if v is None: s.null(n)
        else: s.name(0x27,n); s.str_(v)
    def end(s): s.b.append(0x05)

T_LIST='System.Collections.Generic.List`1[[GameLogic.MusicData, Assembly-CSharp]], mscorlib'
T_MD='GameLogic.MusicData, Assembly-CSharp'
T_CFG='GameLogic.MusicConfigData, Assembly-CSharp'

def write_stage(records, delay, tail_after_delay: bytes) -> bytes:
    """records: list of dicts with MusicData fields; configData dict inside."""
    w=W()
    w.name(0x01,'musicDatas'); w.type_(T_LIST); w.b+=struct.pack('<i',0)
    w.b.append(0x06); w.b+=struct.pack('<q',len(records))
    for r in records:
        w.b.append(0x04); w.type_(T_MD)
        w.i16('objId',r['objId']); w.dec('tick',r['tick'])
        c=r['configData']
        w.name(0x03,'configData'); w.type_(T_CFG)
        w.i32('id',c['id']); w.dec('time',c['time']); w.strn('note_uid',c['note_uid'])
        w.dec('length',c['length']); w.bool_('blood',c['blood']); w.i32('pathway',c['pathway']); w.end()
        w.bool_('isLongPressing',r['isLongPressing']); w.i32('doubleIdx',r['doubleIdx'])
        w.null('sameTickNoteIdx'); w.bool_('isDouble',r['isDouble']); w.bool_('isLongPressEnd',r['isLongPressEnd'])
        w.dec('longPressPTick',r['longPressPTick']); w.i32('endIndex',r['endIndex']); w.dec('dt',r['dt'])
        w.i32('longPressNum',r['longPressNum']); w.dec('showTick',r['showTick'])
        w.end()
    w.b.append(0x07); w.end()
    w.dec('delay',delay)
    w.b+=tail_after_delay
    return bytes(w.b)
