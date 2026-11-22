#!/usr/bin/env python3
"""md2switch - turn a Muse Dash custom chart (.mdm) into an Atmosphere LayeredFS mod
that replaces an existing song on the Nintendo Switch version.

Usage:
  md2switch.py search <romfs> <text>                 find song ids (e.g. "bamboo")
  md2switch.py build  <romfs> <chart.mdm> <song_id> [-o OUT] [--only 2,3]

<romfs> is the extracted RomFS folder (the one containing Data/).
"""
import argparse, glob, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'lib'))

import UnityPy
from UnityPy.streams import EndianBinaryWriter
from PIL import Image
from bms2stage import Transmuter, load_bms, to_records
from odin_stage import write_stage
from fsb5pcm import build_fsb5_pcm16
from catalog import Catalog

TITLE_ID = '01008E200C5C2000'
LANGS = ['english', 'chineses', 'chineset', 'japanese', 'korean']
DIALOG_TAIL = b'-\x01\x0c\x00\x00\x00' + 'dialogEvents'.encode('utf-16-le')


def aa_dir(romfs):
    return os.path.join(romfs, 'Data', 'StreamingAssets', 'aa')


def bundle_path(romfs, name):
    return os.path.join(aa_dir(romfs), 'Switch', name)


def text_assets(env):
    for o in env.objects:
        if o.type.name == 'TextAsset':
            yield o


def read_text(o):
    d = o.read(); s = d.m_Script
    return d, (s if isinstance(s, str) else s.decode('utf-8-sig'))


def write_text(o, d, text):
    d.m_Script = text
    d.save()


# ---------------------------------------------------------------- song lookup
def album_bundles(romfs, lang='others'):
    pat = f'config_{lang}_assets_album*.bundle'
    return [f for f in glob.glob(os.path.join(aa_dir(romfs), 'Switch', pat))
            if not os.path.basename(f).startswith(f'config_{lang}_assets_albums_')]


def find_song(romfs, song_id):
    for f in album_bundles(romfs):
        env = UnityPy.load(f)
        for o in text_assets(env):
            _, s = read_text(o)
            if f'"{song_id}_music"' not in s: continue
            arr = json.loads(s)
            for i, e in enumerate(arr):
                if e.get('music') == f'{song_id}_music':
                    alb = re.match(r'config_others_assets_(album\d+)_', os.path.basename(f)).group(1)
                    return {'album': alb, 'index': i, 'entry': e, 'others_bundle': f}
    raise SystemExit(f'song id "{song_id}" not found in album configs')


def cmd_search(romfs, text):
    text = text.lower()
    eng = {}
    for f in album_bundles(romfs, 'english'):
        alb = re.match(r'config_english_assets_(album\d+)_english', os.path.basename(f)).group(1)
        for o in text_assets(UnityPy.load(f)):
            eng[alb] = json.loads(read_text(o)[1])
    for f in album_bundles(romfs):
        alb = re.match(r'config_others_assets_(album\d+)_', os.path.basename(f)).group(1)
        for o in text_assets(UnityPy.load(f)):
            for i, e in enumerate(json.loads(read_text(o)[1])):
                loc = eng.get(alb, [])
                en = loc[i] if i < len(loc) else {}
                blob = ' '.join([e.get('music', ''), e.get('name', ''), e.get('author', ''), en.get('name', ''), en.get('author', '')]).lower()
                if text in blob:
                    sid = e.get('music', '').replace('_music', '')
                    maps = [k[-1] for k in ('difficulty1', 'difficulty2', 'difficulty3', 'difficulty4') if e.get(k, '0') not in ('0', '')]
                    print(f'{sid:30s} {en.get("name", e.get("name")):35s} {en.get("author", e.get("author"))}  [maps {",".join(maps)}]')


# ---------------------------------------------------------------- mdm
def load_mdm(path, tmp):
    with zipfile.ZipFile(path) as z:
        z.extractall(tmp)
    # some packers put everything inside a subfolder
    root = tmp
    entries = os.listdir(tmp)
    if 'info.json' not in entries and len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
        root = os.path.join(tmp, entries[0])
    info = json.load(open(os.path.join(root, 'info.json'), encoding='utf-8-sig'))
    def pick(base):
        for ext in ('ogg', 'wav', 'mp3', 'flac'):
            p = os.path.join(root, f'{base}.{ext}')
            if os.path.exists(p): return p
        return None
    maps = {n: os.path.join(root, f'map{n}.bms') for n in range(1, 5) if os.path.exists(os.path.join(root, f'map{n}.bms'))}
    cover = next((os.path.join(root, c) for c in ('cover.png', 'cover.jpg', 'cover.gif') if os.path.exists(os.path.join(root, c))), None)
    return {'root': root, 'info': info, 'maps': maps, 'music': pick('music'), 'demo': pick('demo'), 'cover': cover}


def norm_scene(s):
    m = re.match(r'scene_(\d+)', s or '')
    return f'scene_{int(m.group(1)):02d}' if m else s


# ---------------------------------------------------------------- audio
def to_pcm(path, seconds=None):
    cmd = ['ffmpeg', '-v', 'error', '-i', path]
    if seconds: cmd += ['-t', str(seconds)]
    cmd += ['-f', 's16le', '-ac', '2', '-ar', '48000', '-']
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def replace_audio(env, clip_name_suffix, audio_path, seconds=None):
    bf = env.file
    clips = [o for o in env.objects if o.type.name == 'AudioClip']
    for o in clips:
        tt = o.read_typetree()
        if not tt['m_Name'].endswith(clip_name_suffix): continue
        pcm = to_pcm(audio_path, seconds)
        fsb = build_fsb5_pcm16(pcm, 48000, 2)
        src = tt['m_Resource']['m_Source'].split('/')[-1]
        users = [c for c in clips if c.read_typetree()['m_Resource']['m_Source'].split('/')[-1] == src]
        old = bf.files[src]; old_bytes = bytes(old.bytes)
        if len(users) == 1:
            new_bytes, off = fsb, 0
        else:
            pad = (-len(old_bytes)) % 16
            off = len(old_bytes) + pad; new_bytes = old_bytes + b'\x00' * pad + fsb
        w = EndianBinaryWriter(); w.write_bytes(new_bytes); w.flags = getattr(old, 'flags', 0)
        bf.files[src] = w
        tt.update(m_CompressionFormat=0, m_Frequency=48000, m_Channels=2, m_BitsPerSample=16,
                  m_Length=float(len(pcm) / 4 / 48000))
        tt['m_Resource']['m_Offset'] = off; tt['m_Resource']['m_Size'] = len(fsb)
        o.save_typetree(tt)
        return len(pcm) / 4 / 48000
    raise RuntimeError(f'no AudioClip *{clip_name_suffix} in bundle')


def save_bundle(env, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'wb') as f:
        f.write(env.file.save(packer='lz4'))


# ---------------------------------------------------------------- build
def build(romfs, mdm_path, song_id, out, only=None):
    data = os.path.join(HERE, 'data')
    pd = json.load(open(os.path.join(data, 'prefab_data.json')))
    bd = json.load(open(os.path.join(data, 'boss_durations.json')))
    nd_bundle = glob.glob(bundle_path(romfs, 'config_others_assets_notedata_*.bundle'))[0]
    notedata = json.loads(read_text(next(text_assets(UnityPy.load(nd_bundle))))[1])
    T = Transmuter(notedata, pd['startDelay'], pd['boss'], bd)

    tmp = tempfile.mkdtemp(prefix='md2switch_')
    try:
        mdm = load_mdm(mdm_path, tmp)
        info = mdm['info']
        if not mdm['maps']: raise SystemExit('no mapN.bms in .mdm')
        if not mdm['music']: raise SystemExit('no music file in .mdm')
        song = find_song(romfs, song_id)
        entry = song['entry']
        out_sw = os.path.join(out, 'atmosphere', 'contents', TITLE_ID, 'romfs', 'Data', 'StreamingAssets', 'aa', 'Switch')
        cat = Catalog(os.path.join(aa_dir(romfs), 'catalog.json'))
        touched = []

        # ---- which slot gets which chart
        target_slots = [n for n in range(1, 5) if entry.get(f'difficulty{n}', '0') not in ('0', '')]
        avail = sorted(mdm['maps'])
        plan = {}
        for n in target_slots:
            if only and n not in only: continue
            src = n if n in avail else min(avail, key=lambda a: (abs(a - n), a))
            plan[n] = src
        print(f'Replacing "{song_id}" ({song["album"]}, #{song["index"]}): ' +
              ', '.join(f'map{n} <- map{s}.bms' for n, s in plan.items()))

        # ---- music bundle (charts + music)
        mb = f'music_{song_id}_assets_all.bundle'
        env = UnityPy.load(bundle_path(romfs, mb))
        bpm_val = None
        for o in env.objects:
            if o.type.name != 'MonoBehaviour': continue
            tt = o.read_typetree()
            m = re.search(r'_map(\d)$', tt.get('m_Name', ''))
            if not m or int(m.group(1)) not in plan: continue
            bms_path = mdm['maps'][plan[int(m.group(1))]]
            raw_bytes = open(bms_path, 'rb').read()
            binfo, raw = load_bms(raw_bytes.decode('utf-8-sig'))
            T.warnings = []
            L, delay = T.run(binfo, raw)
            payload = write_stage(to_records(L), delay, DIALOG_TAIL)
            tt['serializationData']['SerializedBytes'] = list(payload)
            tt['scene'] = binfo['genre']; tt['bpm'] = float(binfo['bpm'])
            tt['md5'] = hashlib.md5(raw_bytes).hexdigest(); tt['sceneEvents'] = []
            o.save_typetree(tt)
            bpm_val = binfo['bpm']
            print(f'  {tt["m_Name"]}: {len(L)} objects, delay {delay}' + (f'  ({"; ".join(sorted(set(T.warnings)))})' if T.warnings else ''))
        secs = replace_audio(env, '_music', mdm['music'])
        print(f'  music: {secs:.1f}s')
        save_bundle(env, os.path.join(out_sw, mb)); touched.append(mb)

        # ---- song bundle (demo + cover)
        sb = [os.path.basename(f) for f in glob.glob(bundle_path(romfs, f'song_{song_id}_assets_all_*.bundle'))]
        if sb:
            sb = sb[0]; env = UnityPy.load(bundle_path(romfs, sb)); changed = False
            demo_src = mdm['demo'] or mdm['music']
            try:
                replace_audio(env, '_demo', demo_src, seconds=None if mdm['demo'] else 20); changed = True
                print('  demo replaced' + ('' if mdm['demo'] else ' (first 20s of music)'))
            except RuntimeError as e:
                print('  demo:', e)
            if mdm['cover']:
                for o in env.objects:
                    if o.type.name == 'Texture2D':
                        d = o.read()
                        img = Image.open(mdm['cover']).convert('RGBA').resize((d.m_Width, d.m_Height), Image.LANCZOS)
                        d.set_image(img, target_format=4)  # RGBA32
                        d.save(); changed = True
                        print(f'  cover replaced ({d.m_Width}x{d.m_Height})')
            if changed:
                save_bundle(env, os.path.join(out_sw, sb)); touched.append(sb)

        # ---- album configs (titles, designers, difficulty numbers)
        name = info.get('name', song_id); author = info.get('author', '')
        def lvl_designer(n):
            return info.get(f'levelDesigner{n}') or info.get('levelDesigner') or ''
        def diff_for(n):
            s = plan[n]; v = str(info.get(f'difficulty{s}', '0'))
            return v if v not in ('0', '') else entry.get(f'difficulty{n}', '0')
        ob = os.path.basename(song['others_bundle'])
        env = UnityPy.load(song['others_bundle'])
        for o in text_assets(env):
            d, s = read_text(o); arr = json.loads(s); e = arr[song['index']]
            e['name'] = name; e['author'] = author
            if bpm_val is not None: e['bpm'] = info.get('bpm', str(bpm_val))
            if info.get('scene'): e['scene'] = norm_scene(info['scene'])
            for n in plan:
                e[f'levelDesigner{n}'] = lvl_designer(n)
                e[f'difficulty{n}'] = diff_for(n)
                if f'callBackDifficulty{n}' in e: e[f'callBackDifficulty{n}'] = diff_for(n)
            write_text(o, d, json.dumps(arr, ensure_ascii=False, indent='\t'))
        save_bundle(env, os.path.join(out_sw, ob)); touched.append(ob)
        for lang in LANGS:
            fs = glob.glob(bundle_path(romfs, f'config_{lang}_assets_{song["album"]}_{lang}_*.bundle'))
            if not fs: continue
            env = UnityPy.load(fs[0])
            for o in text_assets(env):
                d, s = read_text(o); arr = json.loads(s)
                if song['index'] < len(arr):
                    arr[song['index']]['name'] = name; arr[song['index']]['author'] = author
                write_text(o, d, json.dumps(arr, ensure_ascii=False, indent='\t'))
            save_bundle(env, os.path.join(out_sw, os.path.basename(fs[0]))); touched.append(os.path.basename(fs[0]))
        print(f'  titles set to "{name}" by "{author}"')

        # ---- catalog CRCs
        for b in touched:
            if cat.zero_crc(b): print(f'  catalog: CRC check disabled for {b}')
        if cat.changed:
            cat.save(os.path.join(os.path.dirname(out_sw), 'catalog.json'))
        print(f'\nDone -> {os.path.join(out, "atmosphere")}')
        print('Copy that "atmosphere" folder to the root of your SD card (merge).')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('search'); s.add_argument('romfs'); s.add_argument('text')
    b = sub.add_parser('build'); b.add_argument('romfs'); b.add_argument('mdm'); b.add_argument('song_id')
    b.add_argument('-o', '--out', default=None, help='output folder (default: ./<song_id>_mod)')
    b.add_argument('--only', default=None, help='only replace these difficulty slots, e.g. 2 or 1,2,3')
    a = ap.parse_args()
    if a.cmd == 'search':
        cmd_search(a.romfs, a.text)
    else:
        only = {int(x) for x in a.only.split(',')} if a.only else None
        build(a.romfs, a.mdm, a.song_id, a.out or f'./{a.song_id}_mod', only)


if __name__ == '__main__':
    main()
