import struct, hashlib
FREQ={8000:1,11000:2,11025:3,16000:4,22050:5,24000:6,32000:7,44100:8,48000:9}
def build_fsb5_pcm16(pcm: bytes, rate: int, channels: int) -> bytes:
    assert channels in (1,2) and rate in FREQ
    nsamples=len(pcm)//(2*channels)
    data=pcm+b'\x00'*((-len(pcm))%32)
    raw=(0) | (FREQ[rate]<<1) | ((channels-1)<<5) | (0<<6) | (nsamples<<34)
    sh=struct.pack('<Q',raw)
    h=b'FSB5'+struct.pack('<6I',1,1,len(sh),0,len(data),2)  # mode 2 = PCM16
    h+=b'\x00'*8+hashlib.md5(pcm).digest()+b'\x00'*8
    return h+sh+data
