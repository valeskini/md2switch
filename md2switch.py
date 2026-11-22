#!/usr/bin/env python3
"""md2switch - turn a Muse Dash custom chart (.mdm) into an Atmosphere LayeredFS mod
that replaces an existing song on the Nintendo Switch version.

Usage:
  md2switch.py search <romfs> <text>                 find song ids (e.g. "bamboo")
  md2switch.py build  <romfs> <chart.mdm> <song_id> [-o OUT] [--only 2,3]
  md2switch.py build  <romfs> --song ID=chart.mdm [--song ID=chart.mdm ...] [-o OUT]

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


_JSONC = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|,(?=\s*[}\]])', re.S)


def loads_lenient(s):
    """json.loads that also accepts what the game's config files contain:
    // and /* */ comments and trailing commas (strings are left untouched)."""
    return json.loads(_JSONC.sub(lambda m: m.group(0) if m.group(0).startswith('"') else '', s))


def write_text(o, d, text):
    d.m_Script = text
    d.save()


# ---------------------------------------------------------------- song lookup
def album_bundles(romfs, lang='others'):
    pat = f'config_{lang}_assets_album*.bundle'
    return [f for f in glob.glob(os.path.join(aa_dir(romfs), 'Switch', pat))
            if not os.path.basename(f).startswith(f'config_{lang}_assets_albums_')]


def find_song(romfs, song_id):
    """All album entries that use this song id (some ids appear more than once)."""
    found = []
    for f in album_bundles(romfs):
        env = UnityPy.load(f)
        for o in text_assets(env):
            _, s = read_text(o)
            if f'"{song_id}_music"' not in s: continue
            arr = loads_lenient(s)
            for i, e in enumerate(arr):
                if e.get('music') == f'{song_id}_music':
                    alb = re.match(r'config_others_assets_(album\d+)_', os.path.basename(f)).group(1)
                    found.append({'album': alb, 'index': i, 'entry': e, 'others_bundle': f})
    if not found:
        raise SystemExit(f'song id "{song_id}" not found in album configs')
    return found


def cmd_search(romfs, text):
    text = text.lower()
    eng = {}
    for f in album_bundles(romfs, 'english'):
        alb = re.match(r'config_english_assets_(album\d+)_english', os.path.basename(f)).group(1)
        for o in text_assets(UnityPy.load(f)):
            eng[alb] = loads_lenient(read_text(o)[1])
    for f in album_bundles(romfs):
        alb = re.match(r'config_others_assets_(album\d+)_', os.path.basename(f)).group(1)
        for o in text_assets(UnityPy.load(f)):
            for i, e in enumerate(loads_lenient(read_text(o)[1])):
                loc = eng.get(alb, [])
                en = loc[i] if i < len(loc) else {}
                blob = ' '.join([e.get('music', ''), e.get('name', ''), e.get('author', ''), en.get('name', ''), en.get('author', '')]).lower()
                if text in blob:
                    sid = e.get('music', '').replace('_music', '')
                    maps = [k[-1] for k in ('difficulty1', 'difficulty2', 'difficulty3', 'difficulty4') if e.get(k, '0') not in ('0', '')]
                    print(f'{sid:30s} {en.get("name", e.get("name")):35s} {en.get("author", e.get("author"))}  [uid {e.get("uid", "?")}, maps {",".join(maps)}]')


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
class ConfigStore:
    """Album config bundles are loaded once, edited in memory by every song,
    and written once at the end, so several songs in the same album don't
    overwrite each other's changes."""

    def __init__(self):
        self.items = {}  # bundle path -> (env, [(object, text asset, parsed list)])

    def arrays(self, path):
        if path not in self.items:
            env = UnityPy.load(path)
            lst = []
            for o in text_assets(env):
                d, s = read_text(o)
                lst.append((o, d, loads_lenient(s)))
            self.items[path] = (env, lst)
        return self.items[path][1]

    def save_all(self, out_sw, touched):
        for path, (env, lst) in self.items.items():
            for o, d, arr in lst:
                write_text(o, d, json.dumps(arr, ensure_ascii=False, indent='\t'))
            save_bundle(env, os.path.join(out_sw, os.path.basename(path)))
            touched.append(os.path.basename(path))


def build_song(ctx, song_id, matches, mdm_path):
    romfs, out_sw, T, cfg, touched, only = (ctx[k] for k in ('romfs', 'out_sw', 'T', 'cfg', 'touched', 'only'))
    tmp = tempfile.mkdtemp(prefix='md2switch_')
    try:
        mdm = load_mdm(mdm_path, tmp)
        info = mdm['info']
        if not mdm['maps']: raise SystemExit(f'{mdm_path}: no mapN.bms in .mdm')
        if not mdm['music']: raise SystemExit(f'{mdm_path}: no music file in .mdm')

        # ---- which slot gets which chart (a song id can have several entries: use all their slots)
        target_slots = [n for n in range(1, 5)
                        if any(m['entry'].get(f'difficulty{n}', '0') not in ('0', '') for m in matches)]
        avail = sorted(mdm['maps'])
        plan = {}
        for n in target_slots:
            if only and n not in only: continue
            src = n if n in avail else min(avail, key=lambda a: (abs(a - n), a))
            plan[n] = src
        where = ', '.join(f'{m["album"]}#{m["index"]} ({m["entry"].get("uid", "?")})' for m in matches)
        print(f'Replacing "{song_id}" [{where}]: ' +
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

        # ---- album configs (edited in memory, written at the end by ConfigStore)
        name = info.get('name', song_id); author = info.get('author', '')
        def lvl_designer(n):
            return info.get(f'levelDesigner{n}') or info.get('levelDesigner') or ''
        def diff_for(n, entry):
            v = str(info.get(f'difficulty{plan[n]}', '0')).strip()
            # only plain numbers are safe: the game can't parse values like "?" in the song list
            return v if v.isdigit() and v != '0' else entry.get(f'difficulty{n}', '0')
        for m in matches:
            orig = m['entry']
            for _, _, arr in cfg.arrays(m['others_bundle']):
                e = arr[m['index']]
                e['name'] = name; e['author'] = author
                if bpm_val is not None: e['bpm'] = info.get('bpm', str(bpm_val))
                if info.get('scene'): e['scene'] = norm_scene(info['scene'])
                for n in plan:
                    if orig.get(f'difficulty{n}', '0') in ('0', ''): continue  # this entry doesn't have the slot
                    # the chart doesn't have this difficulty: hide the slot (like the game does with "0")
                    # instead of showing a copy of another map
                    val = diff_for(n, orig) if n in avail else '0'
                    if n in avail: e[f'levelDesigner{n}'] = lvl_designer(n)
                    e[f'difficulty{n}'] = val
                    if f'callBackDifficulty{n}' in e: e[f'callBackDifficulty{n}'] = val
            for lang in LANGS:
                fs = glob.glob(bundle_path(romfs, f'config_{lang}_assets_{m["album"]}_{lang}_*.bundle'))
                if not fs: continue
                for _, _, arr in cfg.arrays(fs[0]):
                    if m['index'] < len(arr):
                        arr[m['index']]['name'] = name; arr[m['index']]['author'] = author
        print(f'  titles set to "{name}" by "{author}"')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build(romfs, jobs, out, only=None):
    """jobs: list of (song_id, mdm_path). All songs end up in one output folder."""
    ids = [j[0] for j in jobs]
    if len(set(ids)) != len(ids):
        raise SystemExit('the same song id was given twice')
    # look everything up first so a typo fails before any slow work
    found = [(sid, find_song(romfs, sid), mdm) for sid, mdm in jobs]
    for _, _, mdm in found:
        if not os.path.isfile(mdm): raise SystemExit(f'chart not found: {mdm}')

    data = os.path.join(HERE, 'data')
    pd = json.load(open(os.path.join(data, 'prefab_data.json')))
    bd = json.load(open(os.path.join(data, 'boss_durations.json')))
    nd_bundle = glob.glob(bundle_path(romfs, 'config_others_assets_notedata_*.bundle'))[0]
    notedata = loads_lenient(read_text(next(text_assets(UnityPy.load(nd_bundle))))[1])
    ctx = {
        'romfs': romfs, 'only': only, 'touched': [], 'cfg': ConfigStore(),
        'T': Transmuter(notedata, pd['startDelay'], pd['boss'], bd),
        'out_sw': os.path.join(out, 'atmosphere', 'contents', TITLE_ID, 'romfs', 'Data', 'StreamingAssets', 'aa', 'Switch'),
    }
    for sid, matches, mdm in found:
        build_song(ctx, sid, matches, mdm)

    ctx['cfg'].save_all(ctx['out_sw'], ctx['touched'])
    # ---- catalog CRCs (one catalog for all songs)
    cat = Catalog(os.path.join(aa_dir(romfs), 'catalog.json'))
    for b in ctx['touched']:
        if cat.zero_crc(b): print(f'catalog: CRC check disabled for {b}')
    if cat.changed:
        cat.save(os.path.join(os.path.dirname(ctx['out_sw']), 'catalog.json'))
    print(f'\nDone ({len(jobs)} song{"s" if len(jobs) != 1 else ""}) -> {os.path.join(out, "atmosphere")}')
    print('Copy that "atmosphere" folder to the root of your SD card (merge).')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('search'); s.add_argument('romfs'); s.add_argument('text')
    b = sub.add_parser('build'); b.add_argument('romfs')
    b.add_argument('mdm', nargs='?'); b.add_argument('song_id', nargs='?')
    b.add_argument('--song', action='append', default=[], metavar='ID=CHART.mdm',
                   help='replace a song; repeat for several songs in one build')
    b.add_argument('-o', '--out', default=None, help='output folder (default: ./<song_id>_mod, or ./md2switch_mod for several songs)')
    b.add_argument('--only', default=None, help='only replace these difficulty slots (all songs), e.g. 2 or 1,2,3')
    a = ap.parse_args()
    if a.cmd == 'search':
        cmd_search(a.romfs, a.text)
        return
    jobs = []
    for spec in a.song:
        sid, sep, path = spec.partition('=')
        if not sep or not sid or not path: ap.error(f'--song needs ID=CHART.mdm, got "{spec}"')
        jobs.append((sid, path))
    if a.mdm or a.song_id:
        if not (a.mdm and a.song_id): ap.error('give both <chart.mdm> and <song_id>, or use --song ID=CHART.mdm')
        jobs.insert(0, (a.song_id, a.mdm))
    if not jobs: ap.error('nothing to build: give <chart.mdm> <song_id> or --song ID=CHART.mdm')
    only = {int(x) for x in a.only.split(',')} if a.only else None
    out = a.out or (f'./{jobs[0][0]}_mod' if len(jobs) == 1 else './md2switch_mod')
    build(a.romfs, jobs, out, only)


if __name__ == '__main__':
    main()
