"""Port of MDMods/CustomAlbums BmsLoader/Bms (MIT) to offline Python.
Produces Muse Dash MusicData records ready for odin_stage.write_stage."""
import json, math, re
import numpy as np
from decimal import Decimal, getcontext, ROUND_HALF_EVEN, ROUND_DOWN

getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN
f32 = np.float32

def D(x):  # (Decimal)float  -> 7 significant digits like .NET
    x = float(f32(x))
    if x == 0: return Decimal(0)
    return Decimal(format(x, '.7g')).normalize() if 'e' not in format(x, '.7g') else Decimal(format(x, '.7g'))

def r3(d): return d.quantize(Decimal('0.001'), rounding=ROUND_HALF_EVEN)
def r2(d): return d.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)

# NoteType
NONE, MONSTER, BLOCK, PRESS, HIDE, BOSS, HP, MUSIC, MUL, SCENECHANGE = range(10)

CHANNELS = {
 '01':'Scene','02':'Timesig','03':'BpmDirect','08':'BpmLookup',
 '15':'E','16':'E','18':'E',
 '11':'AE','13':'AE','31':'AEB','33':'AEB','51':'AE','53':'AE','D1':'AET','D3':'AET',
 '12':'GE','14':'GE','32':'GEB','34':'GEB','52':'GE','54':'GE','D2':'GET','D4':'GET',
 'SC':'Scroll'}
SPEED = {'0O':('b',1),'0P':('b',2),'0Q':('b',3),'0R':('g',1),'0S':('g',2),'0T':('g',3),'0U':('a',1),'0V':('a',2),'0W':('a',3)}

ANIM_LEFT = {"in":0,"out":1,"boss_close_atk_1":1,"boss_close_atk_2":1,"multi_atk_48":1,"multi_atk_48_end":1,
 "boss_far_atk_1_L":2,"boss_far_atk_1_R":2,"boss_far_atk_2":3,"boss_far_atk_1_start":1,"boss_far_atk_2_start":1,
 "boss_far_atk_1_end":2,"boss_far_atk_2_end":3,"atk_1_to_2":2,"atk_2_to_1":3}
ANIM_RIGHT = {"in":1,"out":0,"boss_close_atk_1":1,"boss_close_atk_2":1,"multi_atk_48":1,"multi_atk_48_end":0,
 "boss_far_atk_1_L":2,"boss_far_atk_1_R":2,"boss_far_atk_2":3,"boss_far_atk_1_start":2,"boss_far_atk_2_start":3,
 "boss_far_atk_1_end":1,"boss_far_atk_2_end":1,"atk_1_to_2":3,"atk_2_to_1":2}
TRANSFER = {0:{0:"0",1:"in",2:"boss_far_atk_1_start",3:"boss_far_atk_2_start"},
 1:{0:"out",1:"0",2:"boss_far_atk_1_start",3:"boss_far_atk_2_start"},
 2:{0:"out",1:"boss_far_atk_1_end",2:"0",3:"atk_1_to_2"},
 3:{0:"out",1:"boss_far_atk_2_end",2:"atk_2_to_1",3:"0"}}
ALIGN = {"in":1,"out":-1,"boss_far_atk_1_start":1,"boss_far_atk_2_start":1,"boss_far_atk_1_end":-1,
 "boss_far_atk_2_end":-1,"atk_1_to_2":1,"atk_2_to_1":1}

class Note(dict):
    __getattr__ = dict.__getitem__

def ntype(n): return int(n['type'])

# ---------------- BMS parse ----------------
def calc_time(tick, bpms, timesig):
    rel = sorted([e for e in bpms if e[0] < tick], key=lambda e: e[0])
    if not rel: return f32(0)
    acc = f32(0); total = f32(0)
    for j, (t, freq) in enumerate(rel):
        seg = f32(rel[j+1][0] - t) if j < len(rel)-1 else f32(tick - t)
        prev = total; total = f32(total + seg)
        fb = math.floor(prev); cb = math.ceil(total)
        for k in range(fb, cb):
            frac = f32(1)
            if k == fb: frac = f32(f32(k + 1) - prev)
            if k == cb - 1: frac = f32(total - f32(cb - 1))
            if cb == fb + 1: frac = f32(total - prev)
            mult = f32(timesig.get(k, 1.0))
            val = f32(f32(f32(frac * mult) * freq) / f32(1e-6))
            acc = f32(acc + f32(f32(np.rint(np.float64(val))) * f32(1e-6)))
    return acc

def load_bms(text):
    info = {'bpm':0.0,'player':1,'genre':None}
    bpmdict = {}; timesig = {}; bpms = []; raw = []
    for line in text.splitlines():
        line = line.strip().lstrip('﻿')
        if not line.startswith('#'): continue
        line = line[1:]
        if ' ' in line:
            k, v = line.split(' ', 1)
            if k.startswith('WAV'): continue
            if k.startswith('BPM'):
                try: b = f32(float(v))
                except ValueError: continue
                key = k[3:] if len(k) > 3 else '00'
                bpmdict[key] = b
                if key == '00':
                    info['bpm'] = b; bpms.append((f32(0), f32(f32(f32(60) / b) * f32(4))))
            elif k == 'PLAYER': info['player'] = int(v)
            elif k == 'GENRE': info['genre'] = v.strip()
            else: info[k.lower()] = v
        elif ':' in line:
            ci = line.index(':')
            if ci < 5: continue
            key, val = line[:ci], line[ci+1:]
            beat = int(key[:3]); code = key[3:5]
            if code not in CHANNELS: continue
            ty = CHANNELS[code]
            if ty == 'Timesig':
                timesig[beat] = float(val); continue
            if len(val) % 2: continue
            n = len(val) // 2
            for i in range(n):
                note = val[2*i:2*i+2]
                if note == '00': continue
                tick = f32(f32(f32(i) / f32(n)) + f32(beat))
                if ty in ('BpmDirect', 'BpmLookup'):
                    if ty == 'BpmLookup' and note in bpmdict: fd = bpmdict[note]
                    else: fd = f32(int(note, 16))
                    bpms.append((tick, f32(f32(f32(60) / f32(fd)) * f32(4))))
                else:
                    raw.append({'tick':tick, 'value':note, 'tone':code})
    for r in raw: r['time'] = calc_time(r['tick'], bpms, timesig)
    raw.sort(key=lambda r: int(f32(r['time'] * f32(1000000))) * 10 + (0 if r['tone'] == '15' else 1))
    return info, raw

# ---------------- note data ----------------
def build_keys(notedata):
    keys = {}
    for c in notedata:
        if c['prefab_name'].endswith('_fool'): continue
        if ntype(c) == BLOCK and c['boss_action'].endswith('_atk_2'): continue
        any_scene = c['scene'] == '0'
        any_path = c['pathway'] == '0' and c['score'] == '0' and c['fever'] == '0' and c['damage'] == '0'
        any_speed = ntype(c) in (BOSS, NONE) or c['ibms_id'] in ('16', '17')
        speeds = [1, 2, 3] if any_speed else [int(c['speed'])]
        if any_scene:
            scenes = [f'scene_{s:02d}' for s in range(1, 16) if not (ntype(c) in (HP, MUSIC) and s == 8)]
        else: scenes = [c['scene']]
        paths = [0, 1] if any_path else [int(c['pathway'])]
        for p in paths:
            for sp in speeds:
                for sc in scenes:
                    keys.setdefault(f"{c['ibms_id']}-{p}-{sp}-{sc}", c)
    return keys

def get_note_data(info, raw, keys):
    out = []; speed_a = speed_g = info['player']; oid = 1; consumed = set()
    for i, n in enumerate(raw):
        if i in consumed: continue
        key = n['value']; ch = n['tone']; ct = CHANNELS.get(ch, '')
        if 'A' in ct: path = 1
        elif 'G' in ct or 'E' in ct: path = 0
        else: continue
        if key in SPEED:
            w, s = SPEED[key]
            if w in 'bg': speed_g = s
            if w in 'ba': speed_a = s
        speed = speed_a if path == 1 else speed_g
        cfg = keys.get(f"{key}-{path}-{speed}-{info['genre']}")
        if cfg is None: continue
        time = D(n['time'])
        hold = Decimal(0)
        is_hold = ntype(cfg) in (PRESS, MUL)
        if is_hold:
            if 'T' in ct: hold = Decimal('0.001')
            else:
                for j in range(i+1, len(raw)):
                    if j in consumed: continue
                    e = raw[j]
                    if e['value'] != key or e['tone'] != ch: continue
                    hold = D(e['time']) - time; consumed.add(j); break
        out.append({'id':oid, 'time':time, 'uid':cfg['uid'], 'length':hold, 'pathway':path,
                    'blood': (not is_hold) and ('B' in ct)})
        oid += 1
    return out

# ---------------- transmute ----------------
def new_md(**kw):
    md = {'objId':0,'tick':Decimal(0),'cfg':{'id':0,'time':Decimal(0),'note_uid':None,'length':Decimal(0),'blood':False,'pathway':0},
          'isLongPressing':False,'doubleIdx':-1,'isDouble':False,'isLongPressEnd':False,'longPressPTick':Decimal(0),
          'endIndex':0,'dt':Decimal(0),'longPressNum':0,'showTick':Decimal(0),'nd':None}
    md.update(kw); return md

class Transmuter:
    def __init__(self, notedata, start_delay, boss_info, boss_dur):
        self.notedata = notedata
        self.by_uid = {}
        self.boss = {}
        for c in notedata:
            self.by_uid.setdefault(c['uid'], c)
            if ntype(c) != NONE or not c['boss_action'] or c['boss_action'] == '0': continue
            self.boss.setdefault(c['scene'], {}).setdefault(c['boss_action'], c)
        self.keys = build_keys(notedata)
        self.start_delay = start_delay; self.boss_info = boss_info; self.boss_dur = boss_dur
        self.scene_info = {}
        for c in notedata:
            if ntype(c) == SCENECHANGE: self.scene_info[c['ibms_id']] = int(c['des'].split('_')[-1])
        self.warnings = []

    def sd(self, prefab):
        if prefab not in self.start_delay:
            self.warnings.append(f'missing startDelay {prefab}'); return 0.0
        return self.start_delay[prefab]

    def anim_dur(self, scene, anim):
        idx = scene.split('_')[1]
        bname = f'{idx}01_boss'
        name = anim
        for a, acts in self.boss_info[bname]['actionData']:
            if a == anim:
                if acts: name = acts[0]
                break
        return self.boss_dur[idx + '01'][name]

    def sort(self, L):
        head, rest = L[0], L[1:]
        rest.sort(key=lambda m: m['tick'] - m['dt'])
        L[:] = [head] + rest
        for i in range(1, len(L)):
            L[i]['tick'] = r3(L[i]['tick']); L[i]['objId'] = i

    def is_boss(self, m): return m['nd'] is not None and m['nd']['boss_action'] not in ('0', '')

    def run(self, info, raw):
        notes = get_note_data(info, raw, self.keys)
        L = [new_md()]
        for n in notes:
            cfg = {'id':n['id'], 'time':D(n['time']), 'note_uid':n['uid'], 'length':D(n['length']),
                   'blood':n['blood'], 'pathway':n['pathway']}
            if cfg['time'] < 0: continue
            nd = self.by_uid[n['uid']]
            m = new_md(objId=len(L), tick=r3(cfg['time']), cfg=cfg, nd=nd)
            L.append(m)
            if not (ntype(nd) == PRESS and cfg['length'] > 0): continue
            end_index = int((r3(m['tick'] + cfg['length'] - Decimal(nd['left_great_range']) - Decimal(nd['left_perfect_range'])) / Decimal('0.001')).to_integral_value(rounding=ROUND_DOWN))
            cnt = math.ceil(cfg['length'] / Decimal('0.1'))
            for i in range(1, cnt + 1):
                hc = dict(cfg); hc['length'] = Decimal(0)
                L.append(new_md(objId=len(L), tick=(m['tick'] + cfg['length']) if i == cnt else m['tick'] + Decimal('0.1') * i,
                                cfg=hc, isLongPressing=i != cnt, isLongPressEnd=i == cnt, nd=nd,
                                longPressPTick=cfg['time'], endIndex=end_index))
        self.sort(L)
        self.process_boss(info, L)
        delay = self.process_delay(info, L)
        self.sort(L)
        self.process_geminis(L)
        for m in L:
            m['tick'] -= delay
            m['showTick'] = r2(m['tick'] - m['dt'])
            if m['nd'] is not None and ntype(m['nd']) == PRESS:
                m['endIndex'] -= int((delay / Decimal('0.001')).to_integral_value(rounding=ROUND_DOWN))
        return L, delay

    def process_boss(self, info, L):
        scene = info['genre']
        bd = [m for m in L if self.is_boss(m)]
        if not bd: return
        fin = bd[-1]
        if ANIM_RIGHT[fin['nd']['boss_action']] != 0:
            last = L[-1]; tick = last['tick']
            if self.is_boss(last):
                sdl = r3(D(self.sd(last['nd']['prefab_name'])))
                dur = D(self.anim_dur(scene, last['nd']['boss_action']))
                tick += r3(-sdl + dur)
            tick = r3(tick + Decimal('0.1'))
            ex = self.boss[scene]['out']
            em = new_md(objId=0, tick=tick, cfg={'id':0,'time':tick,'note_uid':ex['uid'],'length':Decimal(0),'blood':False,'pathway':0}, nd=ex)
            L.append(em); bd.append(em)
            self.warnings.append(f'added boss exit at {tick}')
        cached = None
        for i in range(len(bd)):
            d = bd[i]
            if ntype(d['nd']) != BLOCK: continue
            ahead = next((x for x in bd[i+1:] if ntype(x['nd']) != BLOCK), None)
            before = bd[i-1] if i > 0 else None
            MIN = Decimal('-79228162514264337593543950335')
            at = ahead['cfg']['time'] if ahead else MIN
            bt = before['cfg']['time'] if before else MIN
            da = abs(float(f32(float(d['cfg']['time']))) - float(f32(float(at))))
            db = abs(float(f32(float(d['cfg']['time']))) - float(f32(float(bt))))
            is_ahead = da < db
            sb = ANIM_RIGHT[before['nd']['boss_action']] if before else 0
            sa = ANIM_LEFT.get(ahead['nd']['boss_action'], 0) if ahead else 0
            used = sa if is_ahead else sb
            if used not in (2, 3):
                is_ahead = not is_ahead; used = sa if is_ahead else sb
            if used not in (2, 3): continue
            if (is_ahead and ANIM_LEFT[d['nd']['boss_action']] == used) or (not is_ahead and ANIM_RIGHT[d['nd']['boss_action']] == used):
                continue
            phase = 1 if used == 2 else 2
            n = d['nd']
            ok = lambda c: (c['ibms_id'] == n['ibms_id'] and c['pathway'] == n['pathway'] and c['scene'] == n['scene']
                            and c['speed'] == n['speed'] and c['boss_action'].startswith(f'boss_far_atk_{phase}'))
            if cached is None or not ok(cached):
                cached = next((c for c in self.notedata if ok(c)), cached)
            if cached is None: continue
            cfg = dict(d['cfg']); cfg['note_uid'] = cached['uid']
            fixed = new_md(objId=d['objId'], tick=d['tick'], cfg=cfg, isLongPressEnd=d['isLongPressEnd'],
                           isLongPressing=d['isLongPressing'], nd=cached)
            L[fixed['objId']] = fixed; bd[i] = fixed
            self.warnings.append(f"fixed gear at {d['tick']}")
        state = 0; i = 0
        while i < len(bd):
            anim = bd[i]['nd']['boss_action']; nxt = ANIM_LEFT[anim]
            if state != nxt:
                tr = TRANSFER[state][nxt]; tnd = self.boss[scene][tr]; al = ALIGN[tr]
                ad = bd[i] if al == 1 else bd[i-1]
                rd = D(self.sd(bd[i]['nd']['prefab_name']))
                ld = Decimal(0) if i == 0 else D(self.sd(bd[i-1]['nd']['prefab_name']))
                adl = ld if al == -1 else rd
                dur = D(self.anim_dur(scene, tr))
                t = r3(ad['tick'] - adl - dur * al)
                md = new_md(tick=t, cfg={'id':0,'time':t,'note_uid':tnd['uid'],'length':Decimal(0),'blood':False,'pathway':0}, nd=tnd)
                tol = Decimal('0.3')
                fits = (md['tick'] + dur < bd[i]['tick'] - rd + tol) if al == -1 else (i == 0 or md['tick'] > bd[i-1]['tick'] - ld)
                if not fits:
                    state = ANIM_RIGHT[anim]; i += 1; continue
                L.append(md); bd.insert(i, md)
                continue  # i-- then loop i++ => same i
            else:
                state = ANIM_RIGHT[anim]
            i += 1

    def process_delay(self, info, L):
        sidx = int(info['genre'].split('_')[1]); delay = Decimal(0); cache = {}
        for m in L:
            nd = m['nd']
            if nd is not None and nd['ibms_id']:
                t = ntype(nd)
                if t == SCENECHANGE: sidx = self.scene_info[nd['ibms_id']]
                p = nd['prefab_name']
                if p:
                    if t not in (HP, MUSIC) and p[:2] not in ('00', 'em', 'bo'):
                        p = f'{sidx:02d}' + p[2:]
                    if p in self.start_delay: m['dt'] = D(self.start_delay[p])
                    else: self.warnings.append(f'no prefab {p}')
            st = m['tick'] - m['dt']
            if st < delay: delay = st
        return r3(delay)

    def process_geminis(self, L):
        cache = {}
        for i in range(1, len(L)):
            m = L[i]; m['doubleIdx'] = -1
            if ntype(m['nd']) not in (MONSTER, HIDE): continue
            air = m['cfg']['pathway'] == 1
            if m['tick'] in cache:
                ng = m['nd']['ibms_id'] == '0E'; tg = False; target = None
                for g in cache[m['tick']]:
                    if air == (g['cfg']['pathway'] == 1): continue
                    target = g; tg = g['nd']['ibms_id'] == '0E'
                    if ng and tg: break
                    if not ng: break
                if target is not None and target['objId'] > 0:
                    m['isDouble'] = ng and tg; m['doubleIdx'] = target['objId']
                    target['isDouble'] = ng and tg; target['doubleIdx'] = m['objId']
            else: cache[m['tick']] = []
            cache[m['tick']].append(m)

def to_records(L):
    out = []
    for m in L:
        out.append({'objId':m['objId'],'tick':m['tick'],'configData':m['cfg'],'isLongPressing':m['isLongPressing'],
                    'doubleIdx':m['doubleIdx'],'isDouble':m['isDouble'],'isLongPressEnd':m['isLongPressEnd'],
                    'longPressPTick':m['longPressPTick'],'endIndex':m['endIndex'],'dt':m['dt'],
                    'longPressNum':m['longPressNum'],'showTick':m['showTick']})
    return out
