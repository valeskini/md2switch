"""Addressables 1.21 compact JSON catalog: read bundle CRCs and zero them out."""
import base64, json, struct


def _parse_extra(ex):
    objs = []  # (old_offset, header_bytes, json_obj)
    p = 0
    while p < len(ex):
        start = p
        assert ex[p] == 7, f'unexpected extra tag {ex[p]} at {p}'
        p += 1
        l = ex[p]; p += 1 + l
        l = ex[p]; p += 1 + l
        hdr = ex[start:p]
        n = struct.unpack_from('<i', ex, p)[0]; p += 4
        objs.append((start, hdr, json.loads(ex[p:p + n].decode('utf-16le'))))
        p += n
    return objs


class Catalog:
    def __init__(self, path):
        self.path = path
        self.c = json.load(open(path, encoding='utf-8'))
        self.ids = self.c['m_InternalIds']
        self.extra = _parse_extra(base64.b64decode(self.c['m_ExtraDataString']))
        self.by_off = {o[0]: o[2] for o in self.extra}
        ed = base64.b64decode(self.c['m_EntryDataString'])
        self.entries = []
        n = struct.unpack_from('<i', ed, 0)[0]
        for i in range(n):
            self.entries.append(list(struct.unpack_from('<7i', ed, 4 + i * 28)))
        self.changed = False

    def bundle_names(self):
        return [i.split('/')[-1] for i in self.ids if i.endswith('.bundle')]

    def options(self, bundle_name):
        for e in self.entries:
            if self.ids[e[0]].split('/')[-1] == bundle_name and e[4] in self.by_off:
                return self.by_off[e[4]]
        return None

    def zero_crc(self, bundle_name):
        opt = self.options(bundle_name)
        if opt and opt.get('m_Crc'):
            opt['m_Crc'] = 0
            self.changed = True
            return True
        return False

    def save(self, out_path):
        buf = bytearray(); remap = {}
        for off, hdr, obj in self.extra:
            remap[off] = len(buf)
            js = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-16le')
            buf += hdr + struct.pack('<i', len(js)) + js
        ed = bytearray(struct.pack('<i', len(self.entries)))
        for e in self.entries:
            e = list(e)
            if e[4] in remap: e[4] = remap[e[4]]
            ed += struct.pack('<7i', *e)
        self.c['m_ExtraDataString'] = base64.b64encode(bytes(buf)).decode()
        self.c['m_EntryDataString'] = base64.b64encode(bytes(ed)).decode()
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.c, f, separators=(',', ':'), ensure_ascii=False)
