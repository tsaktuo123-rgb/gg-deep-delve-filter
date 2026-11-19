#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stash_pricer.py - Hands-free PD2 stash & inventory auto-appraiser for
GG DEEP DELVE (Project Diablo 2, Season 14).

VERIFIED live 2026-11-17:
  - GET https://api.projectdiablo2.com/game/stash/<account>
    with `Authorization: Bearer <PD2_AUTH_TOKEN>` returns the account's
    shared stash (275 items incl. exact rolled stats), the currency tab
    (runes/gems with counts), grail data and stats. Endpoint + auth scheme
    confirmed from the pd2trader app's own open-source client
    (github.com/errolgr/pd2-trade): personal character stashes arrive over
    its signed-in socket, not REST - so this tool prices the shared stash,
    the currency tab, and everything PD2's REST exposes.
  - Pricing via pd2trader's public endpoints: /api/valuate (bulk, qty-aware),
    /api/rares/appraisal/batch (exact-roll rare/craft comps from the same
    stat keys the stash API reports).
  - Token auto-import: the pd2trader desktop app stores `pd2Token` in
    %APPDATA%\\com.pd2-trader.app\\settings.json - this script imports it so
    nobody hand-copies secrets (the token never gets printed or committed).

Modes
  python stash_pricer.py --watch            background daemon (game-aware 90s polling)
  python stash_pricer.py --once             one-shot: price the whole stash
  python stash_pricer.py --once --tab 6     price only the tab matching "6"
  python stash_pricer.py --import-token     pull the token from the pd2trader app
  python stash_pricer.py --check            price-check the current clipboard item
  python stash_pricer.py --init-filter      initialize GG_DEEP_DELVE.filter from Hiim_Hyper

Security boundary: the tool talks only to the user's own account with the token
the user (or their pd2trader app) supplied locally. HTTP 401/403 pauses polling
and shows a banner instead of retrying.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ------------------------------------------------------------------ config
REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / '.env'
DASH_HTML = REPO / 'stash_prices.html'
DASH_MD = REPO / 'stash_prices.md'
CACHE = REPO / 'stash_cache.json'
WATCH_LOG = REPO / 'watcher.log'


class WatchLogTee:
    """Watch-mode only: mirrors stdout into watcher.log so the hidden instance
    can be monitored live (Monitor_Watcher.cmd tails this file). Every print
    in the watch loop - cycles, standings, posts, delists, hotkeys - lands in
    the log, line-buffered."""
    def __init__(self, path):
        self._out = sys.stdout
        self._log = open(path, 'a', encoding='utf-8', buffering=1)

    def write(self, s):
        try:
            self._out.write(s)
            self._out.flush()
        except Exception:                                   # noqa: BLE001
            pass                                            # console may be hidden/gone
        self._log.write(s)
        return len(s)

    def flush(self):
        try:
            self._out.flush()
        except Exception:                                   # noqa: BLE001
            pass
        self._log.flush()

VALUATE_URL = 'https://pd2trader.com/api/valuate'
APPRAISAL_BATCH_URL = 'https://pd2trader.com/api/rares/appraisal/batch'
PRICE_CHECK_URL = 'https://pd2trader.com/api/price-check'
APPRAISAL_URL = 'https://pd2trader.com/api/rares/appraisal'
NETWORTH_SYNC_URL = 'https://pd2trader.com/api/networth/sync'

PD2_API_BASE = 'https://api.projectdiablo2.com'
PD2TRAADER_SETTINGS = Path(os.environ.get('APPDATA', '')) / 'com.pd2-trader.app' / 'settings.json'
STASH_URL_TMPL = PD2_API_BASE + '/game/stash/%s'
POLL_SECONDS = 90
IDLE_CHECK_SECONDS = 60
GAME_PROCESSES = ('Game.exe', 'Diablo II.exe')

# ------------------------------------------------------------------ standings (product core)
# 3 deterministic standings for every priced item. Thresholds are HR values,
# configurable via .env (GG_STAND_HIGH=1.0, GG_STAND_CHECK=0.3).
def load_standings(env):
    high = float(env.get('GG_STAND_HIGH', 1.0))     # >= this -> GG (auto-sell candidate)
    low = float(env.get('GG_STAND_CHECK', 0.15))    # >= this -> CHECK, below -> VENDOR
    return high, low

def classify(it, high, low, appraisal=None):
    """Deterministic standing: GG / CHECK / VENDOR for one priced item.
    Rares/crafts use the pd2trader appraisal verdict + band when available;
    everything else uses the live HR value against the thresholds."""
    hr = it.get('hr') or 0
    verdict = (appraisal or {}).get('verdict')
    band = (appraisal or {}).get('band') or (appraisal or {}).get('priceBand') or {}
    band_hi = band.get('high') if isinstance(band.get('high'), (int, float)) else 0
    band_mid = band.get('mid') if isinstance(band.get('mid'), (int, float)) else 0
    stars = sum(1 for s in (appraisal or {}).get('stats', [])
                if isinstance(s, dict) and s.get('stars'))
    if it.get('quality') in ('Rare', 'Craft') and appraisal is not None:
        if verdict == 'keep' or band_hi >= high:
            standing, ask = 'GG', band_hi or hr
        elif verdict == 'check' or band_mid >= low:
            standing, ask = 'CHECK', band_mid or hr
        else:
            standing, ask = 'VENDOR', hr
    else:
        if hr >= high:
            standing, ask = 'GG', hr
        elif hr >= low:
            standing, ask = 'CHECK', hr
        else:
            standing, ask = 'VENDOR', hr
    return standing, ask, stars, verdict

# ------------------------------------------------------------------ .env
def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def write_env_key(key, value):
    lines = []
    if ENV_FILE.exists():
        lines = [l for l in ENV_FILE.read_text(encoding='utf-8').splitlines()]
    out, done = [], False
    for l in lines:
        if l.strip().startswith(key + '='):
            out.append('%s=%s' % (key, value)); done = True
        else:
            out.append(l)
    if not done:
        out.append('%s=%s' % (key, value))
    ENV_FILE.write_text('\n'.join(out) + '\n', encoding='utf-8')

def import_token():
    """Pull PD2_AUTH_TOKEN / account from the pd2trader desktop app settings."""
    if not PD2TRAADER_SETTINGS.exists():
        print('pd2trader app settings not found at %s' % PD2TRAADER_SETTINGS)
        print('Install/log into the pd2trader desktop app first (pd2trader.com/overlay).')
        return 1
    s = json.loads(PD2TRAADER_SETTINGS.read_text(encoding='utf-8'))
    token, account = s.get('pd2Token', ''), s.get('account', '')
    expiry = s.get('pd2TokenExpiry', 0)
    if not token:
        print('pd2trader settings found but no pd2Token in them - log into the app first.')
        return 1
    exp = datetime.fromtimestamp(expiry / 1000, tz=timezone.utc).strftime('%Y-%m-%d') if expiry else 'unknown'
    write_env_key('PD2_AUTH_TOKEN', token)
    if account:
        write_env_key('PD2_ACCOUNT_NAME', account)
    print('token imported from pd2trader (expires ~%s, account %s). .env updated, value hidden.' % (exp, account))
    return 0

def filter_paths(env):
    base = Path(env.get('PD2_FILTER_DIR',
                        r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local'))
    return (base / env.get('PD2_FILTER_NAME', 'GG_DEEP_DELVE.filter'),
            base / env.get('PD2_FILTER_SOURCE_NAME', 'Hiim_Hyper.filter'))

# ------------------------------------------------------------------ http
def http_json(url, token=None, body=None, timeout=60):
    headers = {'User-Agent': 'GG-DEEP-DELVE-stash-pricer/0.2', 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.load(r)

class TokenExpired(Exception):
    pass

# ------------------------------------------------------------------ stash fetch (VERIFIED)
def is_personal_stash_location(loc):
    """PD2 reports a character's personal stash as 'Shared Stash' page 0 (the
    shared stash's own pages are 1-9) both in game/character and in listings
    made from it - the hook's isPersonalStashLocation, verified 2026-11-18."""
    storage = ((loc or {}).get('storage') or '')
    return storage.lower().replace('personal ', '') == 'stash' or \
        (storage.lower() == 'shared stash' and (loc or {}).get('stash_page') == 0)


def socket_personal_stash(token, characters):
    """Reads each character's PERSONAL STASH over the hook's socket flow.
    Verified live 2026-11-18: websocket transport is REQUIRED - polling 400s
    with 'Session ID unknown' because the socket.io server runs behind a load
    balancer without sticky sessions (each poll can hit another node). Flow:
      connect wss://api.projectdiablo2.com (websocket) ->
      emit('create', ('security/session', {'strategy': 'oauth', 'accessToken': token})) ->
      emit('get', ('game/character', <name>, {})) per character.
    python-socketio packs a TUPLE as flat multi-args (a list would nest them
    and the server rejects the service path). Returns {char: [personal items]}.
    """
    import socketio, threading
    out = {}
    sio = socketio.Client(logger=False, engineio_logger=False)
    state = {'connected': threading.Event(), 'authed': threading.Event(), 'err': None}

    @sio.event
    def connect():
        state['connected'].set()

        def on_create(err=None, *a):
            state['err'] = err
            state['authed'].set()
        sio.emit('create', ('security/session',
                            {'strategy': 'oauth', 'accessToken': token}),
                 callback=on_create)

    try:
        sio.connect('wss://api.projectdiablo2.com', transports=['websocket'],
                    wait_timeout=25)
        if not state['connected'].wait(15):
            raise RuntimeError('socket: no connect event in 15s')
        if not state['authed'].wait(20):
            raise RuntimeError('socket: no auth ack in 20s')
        if state['err']:
            raise RuntimeError('socket auth refused: %r' % state['err'])
        for ch in characters:
            done = threading.Event()
            res = {}

            def on_get(err=None, *a, _d=done, _r=res):
                _r['err'] = err
                _r['d'] = a[0] if a else None
                _d.set()
            sio.emit('get', ('game/character', ch, {}), callback=on_get)
            if not done.wait(25):
                raise RuntimeError('socket: no answer for %s in 25s' % ch)
            if res.get('err'):
                raise RuntimeError('socket get %s: %r' % (ch, res['err']))
            items = (res.get('d') or {}).get('items') or []
            out[ch] = [it for it in items if is_personal_stash_location(it.get('location'))]
    finally:
        try:
            sio.disconnect()
        except Exception:                                   # noqa: BLE001
            pass
    return out


def char_item(it, character, tab):
    """Normalize a character-held item to the same shape as shared-stash items."""
    base_obj = it.get('base') or {}
    loc = it.get('location') or {}
    mods = [(m.get('name'), (m.get('values') or [0])[0]) for m in it.get('modifiers', [])
            if m.get('name')]
    return {
        'character': character,
        'tab': tab,
        'pos': loc.get('zone', ''),
        'code': base_obj.get('id'),
        'name': it.get('name') or base_obj.get('name') or base_obj.get('id'),
        'base_name': base_obj.get('name'),
        'quality': (it.get('quality') or {}).get('name') or '',
        'ilvl': it.get('item_level'),
        'sockets': it.get('socketed_count') or 0,
        'ethereal': bool(it.get('is_ethereal')),
        'identified': bool(it.get('is_identified')),
        'runeword': bool(it.get('is_runeword')),
        'modifiers': mods,
        'id': str(it.get('id')),
        'raw': it,
        'qty': it.get('quantity') or 1,
    }


def scan_equipped_gear(env):
    """Worn-gear valuation — STANDALONE by design: priced for information,
    NEVER listed (worn items cannot be traded and must not consume rotation
    slots). Source: pd2trader's builds API, which prices each equipped item
    INCLUDING its socketed jewels/runes (measured 2026-11-18: Kira's Guardian
    4.19 HR with 3x Rainbow Facet (Lightning 3/4) inside,
    priceBasis=corruption). Only characters the builds service tracks
    (ladder, above its crawl floor) return data. Toggle: GG_SCAN_EQUIPPED / F5.
    """
    try:
        _, acct = http_json(PD2_API_BASE + '/game/account/' + env.get('PD2_ACCOUNT_NAME', ''))
        chars = [c.get('name') for c in (acct.get('details') or [])
                 if c.get('hardcore') is False and c.get('ladder') is True and c.get('name')]
    except Exception:                                       # noqa: BLE001
        return []
    out = []
    for ch in chars:
        try:
            _, d = http_json('https://pd2trader.com/api/builds/characters/' + ch)
        except Exception:                                   # noqa: BLE001
            continue
        for e in d.get('equipped') or []:
            v = e.get('priceHr')
            if not isinstance(v, (int, float)) or v <= 0:
                continue
            out.append({'character': ch,
                        'name': e.get('name') or '',
                        'quality': e.get('quality') or '',
                        'hr': v,
                        'basis': e.get('priceBasis') or '',
                        'socketed': ', '.join(s.get('name') for s in (e.get('socketed') or []))})
    out.sort(key=lambda r: -r['hr'])
    return out


def fetch_stash_items(token, account, base=None, env=None):
    """GET /game/stash/<account> - verified working (275 items, currency tab)."""
    url = (base or PD2_API_BASE).rstrip('/') + '/game/stash/' + account
    try:
        _, d = http_json(url, token=token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TokenExpired('stash %d' % e.code)
        raise RuntimeError('stash fetch failed: %d %s' % (e.code, e.reason))
    items = []
    for it in d.get('items', []):
        loc = it.get('location') or {}
        page = loc.get('stash_page')
        equip = loc.get('equipment') or ''
        tab = 'Page %s%s' % (page, (' - ' + equip) if equip else '')
        base_obj = it.get('base') or {}
        quality = (it.get('quality') or {}).get('name') or ''
        mods = [(m.get('name'), (m.get('values') or [0])[0]) for m in it.get('modifiers', [])
                if m.get('name')]
        items.append({
            'character': 'Shared Stash',
            'tab': tab,
            'pos': equip or loc.get('zone', ''),
            'code': base_obj.get('id'),
            'name': it.get('name') or base_obj.get('name') or base_obj.get('id'),
            'base_name': base_obj.get('name'),
            'quality': quality,
            'ilvl': it.get('item_level'),
            'sockets': it.get('socketed_count') or 0,
            'ethereal': bool(it.get('is_ethereal')),
            'identified': bool(it.get('is_identified')),
            'runeword': bool(it.get('is_runeword')),
            'modifiers': mods,
            'id': str(it.get('id')),
            'raw': it,
            'qty': it.get('quantity') or 1,
        })
    # currency/misc/maps/ubers/gems tabs: recursive walk of the ENTIRE currency
    # dict -> every (path, count) becomes a priced, listable line
    cur = d.get('currency') or {}
    # NEVER_LIST currency types: main currency (wss), rejuv potions, stash gold,
    # crafting materials (map_craft), jewel fragments. These stay in the stash -
    # they're consumed, not sold. Runes and gems also excluded (never sell).
    SKIP_CUR = {'rejuv', 'full_rejuv', 'stash_gold', 'version', 'identifier',
                'wss', 'twss', 'token_of_absolution', 'map_craft',
                'gems', 'maps', 'runes', 'demonic_cube', 'jewel_frag'}
    RUNE_NAMES = {'r01': 'El', 'r02': 'Eld', 'r03': 'Tir', 'r04': 'Nef', 'r05': 'Eth',
                  'r06': 'Ith', 'r07': 'Tal', 'r08': 'Ral', 'r09': 'Ort', 'r10': 'Thul',
                  'r11': 'Amn', 'r12': 'Sol', 'r13': 'Shael', 'r14': 'Dol', 'r15': 'Hel',
                  'r16': 'Io', 'r17': 'Lum', 'r18': 'Ko', 'r19': 'Fal', 'r20': 'Lem',
                  'r21': 'Pul', 'r22': 'Um', 'r23': 'Mal', 'r24': 'Ist', 'r25': 'Gul',
                  'r26': 'Vex', 'r27': 'Ohm', 'r28': 'Lo', 'r29': 'Sur', 'r30': 'Ber',
                  'r31': 'Jah', 'r32': 'Cham', 'r33': 'Zod'}
    DISP = {'wss': 'Worldstone Shard', 'twss': 'Tainted WSS',
            'puzzlebox': "Larzuk's Puzzlebox", 'puzzlepiece': "Larzuk's Puzzlepiece",
            'jewel_frag': 'Jewel Fragment', 'demonic_cube': 'Demonic Cube',
            'token_of_absolution': 'Token of Absolution', 'lilith_mirror': "Lilith's Mirror",
            'vial_of_lightsong': 'Vial of Lightsong'}

    def walk_currency(node, path, out):
        if isinstance(node, dict):
            for k, v in node.items():
                walk_currency(v, path + [k], out)
        elif isinstance(node, int) and node > 0:
            out.append((path, node))

    flat = []
    walk_currency(cur, [], flat)
    # path-aware names for the boss subtree: bare leaf codes truncate and
    # collide ('destruction' is both the map-craft orb AND the Festering
    # Essence of Destruction; 'jawbone' is Trang-Oul's Jawbone)
    BOSS_NAMES = {
        'mini_uber': {'terror': 'Burning Essence of Terror',
                      'hatred': 'Charged Essence of Hatred',
                      'destruction': 'Festering Essence of Destruction'},
        'dclone': {'pure_demonic_essence': 'Pure Demonic Essence',
                   'prime_evil_soul': 'Prime Evil Soul'},
        'rathma': {'jawbone': "Trang-Oul's Jawbone",
                   'splinter': 'Splinter of the Void',
                   'ashes': 'Hellfire Ashes'},
        'lucion': {'insignia': 'Demonic Insignia',
                   'talisman': 'Talisman of Transgression',
                   'flesh': 'Flesh of Malic'},
    }
    # these physical stacks already exist in items[] with hashes and exact
    # names; walking them here too DOUBLE-COUNTS them (measured 2026-11-18:
    # 'Madawc' 0.9 HR walk row AND 'Sigil of Madawc' 0.66 items[] row for one
    # stack, both eligible for the rotation -> duplicate market listings)
    WALK_SKIP_BOSS = {'uber_ancients'}                      # all three sigils in items[]
    WALK_SKIP_LEAF = {('dclone', 'black_soulstone')}        # in items[] with hash
    for path, n in flat:
        if not path or path[0] in SKIP_CUR:
            continue
        code = path[-1]
        if path[0] == 'boss' and len(path) >= 3:
            branch = path[1]
            if branch in WALK_SKIP_BOSS or (branch, code) in WALK_SKIP_LEAF:
                continue
            disp = BOSS_NAMES.get(branch, {}).get(code,
                                                  code.replace('_', ' ').title())
        elif path[0] == 'craft':
            disp = code.capitalize() + ' Craft Infusion'
        else:
            disp = DISP.get(code, RUNE_NAMES.get(code, code.replace('_', ' ').title()))
        tab = path[0].capitalize()
        items.append({'character': 'Currency Tab', 'tab': tab, 'pos': code,
                      'code': code, 'name': disp, 'base_name': disp, 'quality': 'Currency',
                      'qty': n, 'id': 'cur-' + '-'.join(path) + '-%d' % n})
    # ---- character-held value (user rule: value from ALL tabs, 2026-11-18)
    # Reachability measured: personal stashes = socket-only (hook-proven
    # listing path, so listable); inventory/cube = public REST (scanned +
    # priced; listed only with GG_ALLOW_INVENTORY=1 - the market transfer
    # path for inventory items is UNVERIFIED); equipped ('Unknown' storage)
    # and mercenary = NEVER (worn, not tradeable). The currency dict's counts
    # are a stale game-save snapshot (it claimed 15x Sigil of Madawc after
    # the stack had sold) and must not be trusted for ownership.
    try:
        _, acct = http_json(PD2_API_BASE + '/game/account/' + account)
        chars = [c.get('name') for c in (acct.get('details') or [])
                 if c.get('hardcore') is False and c.get('ladder') is True
                 and c.get('name')]
    except Exception:                                       # noqa: BLE001
        chars = []
    if chars:
        try:
            sock = socket_personal_stash(token, chars)
            for ch in chars:
                for it in sock.get(ch, []):
                    items.append(char_item(it, ch, '%s (Personal)' % ch))
        except Exception as e:                              # noqa: BLE001
            print('  personal-stash socket read failed: %r' % e)
        # inventory/cube: REMOVED per user decision 2026-11-18 - nothing from
        # the character inventory is priced or listed. The reachable stash
        # sources are the shared stash (all pages) and character personal
        # stashes (above). Equipped gear and mercenaries are never scanned.
    return items

# ------------------------------------------------------------------ pricing (VERIFIED public endpoints)
def valuate_lines(lines, hours=168):
    """Bulk price via public /api/valuate. lines: [{item, qty}]. Returns {input: (hr, status)}."""
    out = {}
    for i in range(0, len(lines), 500):
        chunk = lines[i:i + 500]
        body = {'items': [{'item': l['item'], 'qty': l.get('qty', 1)} for l in chunk],
                'hours': hours, 'isLadder': True, 'isHardcore': False}
        _, d = http_json(VALUATE_URL, body=body)
        for line in d.get('lines', []):
            out[line.get('input') or line.get('item')] = (
                line.get('valueHr', line.get('priceHr')), line.get('confidence'),
                line.get('unresolvedReason'))
    return out, d

COMP_CACHE = {}                                        # name -> (ts, sorted prices)
COMP_TTL = 3600                                        # re-query market comps hourly

# ---- sold market (v0.4.0): the pricing truth - what actually SOLD
SOLD_FILE = REPO / 'sold_market.json'
SOLD_TTL = 1800                                        # refresh the sold corpus every 30 min
SOLD_MAX_PAGES = 15                                    # 15x100 = up to 1500 recent solds
SOLD_CACHE = {'rows': None, 'ts': 0}
# Owner's user_id (loaded from stash_cache.json at runtime) - own sales are
# excluded from pricing evidence to prevent self-pollution (D1)
OWNER_USER_ID = None


def fetch_sold_market(max_pages=SOLD_MAX_PAGES, owner_user_id=None):
    """The market's SOLD corpus (accepted offers), newest first. Rows carry
    name, base, quality, sockets, eth, corrupted, hr_price (only when the
    listing was PRICED - o.b.o. solds prove sellability but not the amount),
    seller_id (to exclude own sales from evidence), and updated_at.
    Cached to sold_market.json + memory (30 min TTL)."""
    global OWNER_USER_ID
    if owner_user_id is not None:
        OWNER_USER_ID = owner_user_id
    elif OWNER_USER_ID is None:
        # Try to load from stash_cache.json
        if CACHE.exists():
            try:
                cache_data = json.loads(CACHE.read_text(encoding='utf-8'))
                OWNER_USER_ID = cache_data.get('user_id')
            except Exception:
                pass
    now = time.time()
    # If we have an owner_user_id, we need rows with seller_id to exclude own sales.
    # Check if cached rows have seller_id; if not, force refetch.
    need_refetch = owner_user_id is not None or OWNER_USER_ID is not None
    if SOLD_CACHE['rows'] is not None and now - SOLD_CACHE['ts'] < SOLD_TTL and not need_refetch:
        return SOLD_CACHE['rows']
    disk = None
    if SOLD_FILE.exists():
        try:
            disk = json.loads(SOLD_FILE.read_text(encoding='utf-8'))
            disk_rows = disk.get('rows') or []
            # Check if disk rows have seller_id (new format)
            has_seller_id = disk_rows and 'seller_id' in disk_rows[0]
            if now - disk.get('ts', 0) < SOLD_TTL and (not need_refetch or has_seller_id):
                SOLD_CACHE['rows'] = disk_rows
                SOLD_CACHE['ts'] = disk.get('ts', 0)
                return SOLD_CACHE['rows']
        except Exception:                               # noqa: BLE001
            disk = None
    q = urllib.parse.urlencode({'type': 'item', '$limit': '100',
                                'accepted_offer_id[$ne]': 'null',
                                'is_ladder': 'true', 'is_hardcore': 'false',
                                '$sort[updated_at]': '-1'})
    rows = []
    owner_sold_count = 0
    for page in range(max_pages):
        qq = q + '&$skip=%d' % (page * 100)
        try:
            _, d = http_json(PD2_API_BASE + '/market/listing?' + qq)
        except Exception:                               # noqa: BLE001
            break
        data = d.get('data') or []
        for l in data:
            it = l.get('item') or {}
            seller_id = l.get('user_id')
            # D1: exclude owner's own sales from evidence corpus
            if OWNER_USER_ID and seller_id == OWNER_USER_ID:
                owner_sold_count += 1
                continue
            rows.append({
                'name': (it.get('name') or '').strip(),
                'base': ((it.get('base') or {}).get('name') or '').strip(),
                'base_id': ((it.get('base') or {}).get('id') or '').strip(),
                'quality': (it.get('quality') or {}).get('name') or '',
                'cat': (it.get('category') or '').strip().lower(),
                'qty': it.get('quantity') or 1,
                'ilvl': it.get('item_level'),
                'sockets': it.get('socketed_count') or 0,
                'eth': bool(it.get('is_ethereal')),
                'corrupted': bool(it.get('corrupted')),
                'hr': l.get('hr_price') if isinstance(l.get('hr_price'), (int, float)) else None,
                'at': (l.get('updated_at') or '')[:10],
                'seller_id': seller_id,
            })
        if len(data) < 100:
            break
        time.sleep(0.2)
    if owner_sold_count:
        print('  sold corpus: excluded %d own sales (user_id=%s)' % (owner_sold_count, OWNER_USER_ID))
    if rows:
        SOLD_CACHE['rows'] = rows
        SOLD_CACHE['ts'] = now
        try:
            SOLD_FILE.write_text(json.dumps({'ts': now, 'rows': rows}), encoding='utf-8')
        except Exception:                               # noqa: BLE001
            pass
    return rows


def sold_comps(item, window_days=7):
    """Sale evidence with TIERED volume (v0.4.0 gate 1, user rule: generalize
    by sold volume - the most important gate):
      name tier: exact-name solds (a named unique's own demand)
      type tier: base+quality solds (the item type's demand)
      cat tier:  the item's CATEGORY sold volume (broad-demand fallback)
    Gate passes on any tier with real volume. Ranking uses the most specific
    volume available. Price evidence comes from the most specific tier with
    enough PRICED solds (o.b.o. solds prove demand but not the amount).
    Returns provenance for dashboard audit (D6): name/base + count + median + last sold date."""
    rows = fetch_sold_market()
    empty = {'count': 0, 'priced': 0, 'median': None, 'vol_name': 0, 'vol_type': 0,
             'vol_cat': 0, 'tier': 'none', 'last': '', 'provenance': {}}
    if not rows:
        return empty
    q = item.get('quality') or ''
    nm = (item.get('name') or '').strip().lower()
    bn = (item.get('base_name') or '').strip().lower()
    cat = ((item.get('raw') or {}).get('category') or '').strip().lower()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime('%Y-%m-%d')

    def in_window(r):
        return (r.get('at') or '') >= cutoff

    named = [r for r in rows if (r.get('name') or '').strip().lower() == nm and in_window(r)]
    typed = []
    if q not in ('Unique', 'Set', 'Currency'):
        item_corr = bool((item.get('raw') or {}).get('corrupted'))
        typed = [r for r in rows if (r.get('base') or '').strip().lower() == bn
                 and (r.get('quality') or '') == q
                 and r.get('corrupted') == item_corr
                 and in_window(r)]
    cat_rows = [r for r in rows if (r.get('cat') or '') == cat and in_window(r)] if cat else []
    priced_named = [r['hr'] for r in named if isinstance(r.get('hr'), (int, float)) and r['hr'] > 0]
    median = round(statistics.median(priced_named), 4) if len(priced_named) >= 2 else None
    unit_median = None
    if median is not None and (item.get('qty') or 1) > 1:
        # stackable: price evidence is per-unit (solds come in different stack
        # sizes; the unit price is the comparable) - the caller scales it by
        # OUR stack size for the total
        units = sorted(r['hr'] / max(1, r.get('qty') or 1) for r in named
                       if isinstance(r.get('hr'), (int, float)) and r['hr'] > 0)
        unit_median = round(statistics.median(units), 4) if len(units) >= 2 else None
    priced_typed = []
    if median is None:
        priced_typed = [r['hr'] for r in typed if isinstance(r.get('hr'), (int, float)) and r['hr'] > 0]
        median = round(statistics.median(priced_typed), 4) if len(priced_typed) >= 2 else None
    tier = 'name' if named else ('type' if typed else ('cat' if cat_rows else 'none'))
    hits = named or typed or cat_rows
    
    # D6: Price provenance for dashboard audit
    provenance = {
        'tier': tier,
        'name': nm if tier == 'name' else (bn if tier == 'type' else cat),
        'total_sold': len(hits),
        'priced_sold': len(priced_named) + len(priced_typed),
        'median_hr': median,
        'last_sold': max((r.get('at') or '') for r in hits) if hits else '',
        'unit_median': unit_median,
    }
    
    return {'count': len(hits), 'priced': len(priced_named) + len(priced_typed),
            'median': median, 'unit_median': unit_median,
            'vol_name': len(named), 'vol_type': len(typed), 'vol_cat': len(cat_rows),
            'tier': tier,
            'last': max((r.get('at') or '') for r in hits) if hits else '',
            'provenance': provenance}


# corruption-feed pricing for corrupted uniques (v0.4.3): the API's own
# per-corruption medians, keyed by the catalog's baseCode
CATALOG_CACHE = {'ts': 0, 'map': None}
CORRUPT_FEED_CACHE = {}


def catalog_map():
    """itemName(lower) -> pd2trader baseCode, from the average/all catalog
    (cached 30 min)."""
    now = time.time()
    if CATALOG_CACHE['map'] is not None and now - CATALOG_CACHE['ts'] < 1800:
        return CATALOG_CACHE['map']
    _, d = http_json('https://pd2trader.com/item-prices/average/all?isLadder=true&isHardcore=false&hours=168')
    rows = d.get('data') if isinstance(d, dict) else d
    m = {r.get('itemName', '').strip().lower(): r.get('baseCode')
         for r in rows if r.get('baseCode')}
    CATALOG_CACHE['map'] = m
    CATALOG_CACHE['ts'] = now
    return m


def corruption_feed_price(item_name, corruptions):
    """The corruption feed's median price for THIS corrupted unique's own
    corruption (token-overlap text match, sample >= 3). Verified against the
    live feed: Steel Carapace clean median 0.27 with per-corruption rows
    (to All Skills 6.0 n=82, Numsockets 1.0 n=789...)."""
    try:
        bc = catalog_map().get((item_name or '').strip().lower())
        if not bc or not corruptions:
            return None
        now = time.time()
        cached = CORRUPT_FEED_CACHE.get(bc)
        if cached and now - cached[0] < 1800:
            rows = cached[1]
        else:
            _, d = http_json('https://pd2trader.com/item-prices/corruption-prices'
                             '?baseCode=%s&isLadder=true&isHardcore=false&hours=720' % bc)
            rows = [(c.get('corruptionName') or '', c.get('medianPrice'), c.get('sampleCount', 0))
                    for c in d.get('corruptionPrices') or []]
            CORRUPT_FEED_CACHE[bc] = (now, rows)
            time.sleep(0.1)
        text = str(corruptions[0]).lower()
        toks = set(w for w in re.findall(r'[a-z]+', text) if len(w) > 3)
        best, best_ov = None, 0.0
        for nm, med, n in rows:
            if not isinstance(med, (int, float)) or med <= 0 or n < 3:
                continue
            ftoks = set(w for w in re.findall(r'[a-z]+', nm.lower()) if len(w) > 3)
            if not ftoks:
                continue
            ov = len(toks & ftoks) / max(1, len(ftoks))
            if ov > best_ov or (ov == best_ov and best is not None and med > best):
                best, best_ov = med, ov
        return best if best_ov >= 0.6 else None
    except Exception:                                       # noqa: BLE001
        return None


def market_comp_ask(name, pct=25, exclude_uid=None):
    """Live market comps by exact item name: what OTHER sellers ask right now
    (live listings only, priced > 0, newest first, the owner's OWN listings
    excluded so the baseline never anchors to ourselves). Returns
    (percentile_price, min_ask, p25, median, sample) or None below 3 comps.
    The percentile price is the COMPETITIVE ask: pct=25 undercuts ~75% of the
    market (median-ask pricing is structurally non-competitive - measured
    2026-11-18: a Basilisk's Quill stack SOLD at 0.15 while the ask median
    sat at 0.20 and linear math claimed 18.34)."""
    if not name:
        return None
    cached = COMP_CACHE.get(name)
    if cached and time.time() - cached[0] < COMP_TTL:
        return cached[1]
    else:
        q = urllib.parse.urlencode({
            'type': 'item', '$limit': '40',
            'item.name[$regex]': '^%s$' % re.escape(name),
            'accepted_offer_id': 'null',
            'is_ladder': 'true', 'is_hardcore': 'false',
            'hr_price[$gt]': '0', '$sort[bumped_at]': '-1'})
        try:
            _, d = http_json(PD2_API_BASE + '/market/listing?' + q)
        except Exception:                               # noqa: BLE001
            return None
        pairs = [(l['hr_price'], (l.get('item') or {}).get('quantity') or 1)
                 for l in (d.get('data') or [])
                 if isinstance(l.get('hr_price'), (int, float)) and l['hr_price'] > 0
                 and (not exclude_uid
                      or str(l.get('user_id') or '') != str(exclude_uid))]
        if len(pairs) < 3:
            return None
        prices = sorted(p[0] for p in pairs)
        units = sorted(p[0] / max(1, p[1]) for p in pairs)
        result = {'min': round(prices[0], 4),
                  'p25': round(prices[max(0, len(prices) // 4)], 4),
                  'med': round(prices[len(prices) // 2], 4),
                  'n': len(prices),
                  'unit_med': round(statistics.median(units), 4)}
        COMP_CACHE[name] = (time.time(), result)
        time.sleep(0.05)                                # polite per fresh query
        return result


def networth_sync(token, account, is_ladder=True, is_hardcore=False):
    """The hook's own deep-delve endpoint (verified live 2026-11-17, HTTP 200):
    POST /api/networth/sync {account, isLadder, isHardcore, trigger, persist}
    + Bearer token. The server fetches the stash from PD2 itself - clients never
    upload contents - and returns (a) valuation.lines: every priced item keyed
    by PD2 stash item hash (unitPrice/value/confidence/sampleCount) and
    (b) rares: every rare in the stash appraised server-side with verdict
    keep/check/vendor, predicted rank percentile within its slot, and a market
    ask band (lowHr-highHr) with sample size. persist=False keeps it read-only.
    """
    body = {'account': account, 'isLadder': is_ladder, 'isHardcore': is_hardcore,
            'trigger': 'manual', 'persist': False}
    _, d = http_json(NETWORTH_SYNC_URL, token=token, body=body)
    return d


def networth_join(sync):
    """Join keys verified 2026-11-17 against the live stash (297/297 hashes,
    tab 'Shared N' <-> location.stash_page, position {column,row} exact):
      -> ({hash: valuation line}, {(page, col, row): rare appraisal}, all lines)
    The third element keeps the NO-HASH lines (currency-panel mats) - they are
    the server-vouched source for panel-backed listings."""
    lines, rares_pos, all_lines = {}, {}, []
    raw_lines = (sync.get('valuation') or {}).get('lines') or []
    all_lines = raw_lines
    for ln in raw_lines:
        for h in ln.get('itemHashes') or []:
            lines[str(h)] = ln
    for r in sync.get('rares') or []:
        m = re.search(r'(\d+)', r.get('tab') or '')
        pos = r.get('position') or {}
        if m and pos.get('column') is not None and pos.get('row') is not None:
            rares_pos[(int(m.group(1)), pos['column'], pos['row'])] = r
    return lines, rares_pos, all_lines


BANNED_PANEL = ('worldstone shard', 'tainted worldstone', 'wss')


def networth_panel_rows(val_lines, stash_items):
    """Currency-PANEL rows from the server's own valuation (no-hash lines).
    The server vouches the account owns these right now (the hook's net worth
    shows the same lines), which makes them the listable source for misc/uber
    mats that live in the currency panel instead of stash grids - AND the
    phantom guard: mats the server does NOT vouch for (e.g. the Festering
    Essence whose dict count stayed stale after the stack was gone) get no
    line and therefore never list. Grid stacks of the same name are
    subtracted so nothing is double-counted. Runes/gems/wss never list
    (user's standing never-list rules)."""
    grid = {}
    for it in stash_items:
        if it.get('raw'):
            nm = (it.get('name') or '').strip().lower()
            grid[nm] = grid.get(nm, 0) + (it.get('qty') or 1)
    out = []
    for ln in val_lines:
        if not isinstance(ln, dict) or ln.get('itemHashes'):
            continue                                    # grid items: hash-joined elsewhere
        cat = ln.get('category') or ''
        nm = (ln.get('name') or '').strip()
        if cat in ('runes', 'gems'):
            continue
        if any(b in nm.lower() for b in BANNED_PANEL):
            continue
        unit = ln.get('unitPrice')
        if not isinstance(unit, (int, float)) or unit <= 0:
            continue
        qty = (ln.get('qty') or 0) - grid.get(nm.lower(), 0)
        if qty <= 0:
            continue                                    # fully covered by grid stacks
        out.append({'character': 'Currency Panel', 'tab': cat.capitalize(),
                    'pos': nm, 'code': ln.get('key'), 'name': nm,
                    'base_name': nm, 'quality': 'Currency',
                    'qty': qty, 'id': 'panel-' + str(ln.get('key')),
                    'hr': round(unit * qty, 4),
                    'confidence': ln.get('confidence'),
                    'panel': True})
    return out


def appraise_rares(rares, hours=None):
    """Exact-roll rare/craft comps via public /api/rares/appraisal/batch.
    rares: list of our normalized items (quality Rare/Craft). Returns {id: verdict_dict}."""
    out = {}
    for i in range(0, len(rares), 100):
        chunk = rares[i:i + 100]
        entries = []
        for it in chunk:
            stats = [{'key': k, 'value': v} for k, v in it.get('modifiers', [])]
            entries.append({'item': {'baseCode': it.get('code'), 'stats': stats,
                                     'sockets': it.get('sockets', 0),
                                     'ethereal': it.get('ethereal', False),
                                     'quality': it['quality']}})
        body = {'items': entries, 'isLadder': True, 'isHardcore': False}
        try:
            _, d = http_json(APPRAISAL_BATCH_URL, body=body)
        except urllib.error.HTTPError as e:
            print('  rare batch failed (%d), chunk skipped' % e.code)
            continue
        for res in d.get('results', []):
            idx = res.get('index')
            if isinstance(idx, int) and idx < len(chunk):
                ap = res.get('appraisal') or {}
                out[chunk[idx]['id']] = ap
    return out

# ------------------------------------------------------------------ market (auto-list / auto-delist)
# Verified 2026-11-17: POST /market/listing returns 201 (listing id + server-derived
# user_id from the token); GET /market/listing accepts Feathers queries (user_id,
# type, is_ladder, is_hardcore, $limit); DELETE /market/listing/:id removes a listing.
# Body shape taken from pd2trader's own client (useMarketActions.ts).

MARKET_LISTING_URL = PD2_API_BASE + '/market/listing'

def get_own_listings(token, user_id):
    # verified: user_id + type is the working query; is_ladder/is_hardcore
    # filters zero out the result (the stored listing docs don't match them)
    q = {'user_id': user_id, 'type': 'item', '$limit': 100}
    from urllib.parse import urlencode
    url = MARKET_LISTING_URL + '?' + urlencode(q)
    _, d = http_json(url, token=token)
    return d.get('data') or []

def post_listing(token, raw_item, account, hr_price, note, is_ladder=True, is_hardcore=False):
    body = {
        'type': 'item',
        'is_hardcore': is_hardcore,
        'is_ladder': is_ladder,
        'item': dict(raw_item, account_id=account.lower()),
        'hr_price': hr_price,
        'price': note,
        'bumped_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
    }
    _, d = http_json(MARKET_LISTING_URL, token=token, body=body)
    listing = d.get('data') if isinstance(d, dict) and 'data' in d else d
    return listing

def delete_listing(token, listing_id):
    req = urllib.request.Request(MARKET_LISTING_URL + '/' + listing_id, method='DELETE',
                                 headers={'User-Agent': 'GG-DEEP-DELVE/0.1',
                                          'Authorization': 'Bearer ' + token,
                                          'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        print('  delete %s -> %d' % (listing_id, e.code))
        return e.code

def shorten_label(lab):
    lab = (lab or '').strip()
    return lab if len(lab) <= 22 else ' '.join(lab.split()[:3])

PRETTY_MODS = {'poisonresist': 'Psn Res', 'item_magicbonus': 'MF',
               'item_goldbonus': 'Gold', 'item_fastercastrate': 'FCR',
               'item_fastermovevelocity': 'FRW', 'fres': 'F Res', 'cres': 'C Res',
               'lres': 'L Res', 'pres': 'P Res', 'maxhp': 'Life', 'maxmana': 'Mana',
               'item_tohit_percent': 'AR%', 'item_allskills': '+Skills',
               'allskills': '+Skills', 'item_damagetomana': 'DMG->Mana',
               'item_lifesteal': 'LL', 'item_manasteal': 'ML', 'item_maxdamage': 'MaxDmg',
               'item_maxdamage_percent': 'ED', 'strength': 'Str', 'dexterity': 'Dex',
               'vitality': 'Vit', 'energy': 'Ene', 'all_resist': 'All Res',
               'item_fastergethitrate': 'FHR', 'item_splashonhit': 'Splash',
               'item_addclassskills': 'Class Skill', 'item_nonclassskill': 'Skill',
               'item_replenishlife': 'Rep Life', 'item_indestructible': 'Indestr.',
               'item_fasterattackrate': 'IAS', 'item_fasterblockrate': 'FBR',
               'item_damageresist': 'PDR', 'item_normalmissile': 'IM',
               'item_halffreeze': 'Half Freeze', 'item_stupidity': 'Hit Blinds'}

def shorten_mod(m):
    """m: dict (stash API shape) or (key, value) tuple (normalized shape)."""
    if isinstance(m, dict):
        lab = m.get('label')
        if lab:
            return shorten_label(lab)
        key, val = m.get('name'), (m.get('values') or [0])[0]
    else:
        key, val = m[0], m[1]
    if str(key).lower() in ('corrupted', 'is_corrupted') and not val:
        return None                                    # corruption pseudo-mod: no info
    pretty = PRETTY_MODS.get(key, str(key).replace('item_', '').replace('_', ' ').title())
    return '%s %s' % (pretty, val)

def strict_note(it, obo=True):
    """Strict template: bulk -> just the amount ('160x o.b.o.'); singles -> their
    top stat lines ('ED 23% · Requirements -15% o.b.o.'). No name in the note -
    the listing card already shows the item."""
    qty = it.get('qty', 1) or 1
    tail = ' o.b.o.' if obo else ''
    if qty > 1:
        return '%dx%s' % (qty, tail)
    mods = it.get('modifiers') or []
    short = [shorten_mod(m) for m in mods[:2]]
    body = ' \u00b7 '.join(s for s in short if s)
    if body:
        return body + tail
    return 'o.b.o.' if obo else ''

def resolve_user_id(token, listing_id=None):
    """The market derives user_id from the token; read it back from a listing we own."""
    if listing_id:
        try:
            _, d = http_json(MARKET_LISTING_URL + '/' + listing_id, token=token)
            uid = d.get('user_id') if isinstance(d, dict) else None
            if uid:
                return uid
        except Exception:                                   # noqa: BLE001
            pass
    return None


# ------------------------------------------------------------------ overseer tooltip (filter integration)
def update_filter_status(env, state, rows=None):
    """Feed findings into the filter: the Horadric Cube tooltip becomes the
    engine's live readout (standing thresholds, queued counts, total HR,
    timestamp). The watcher refreshes it each cycle so the overseer can hover
    the cube in game and review why the engine is listing what it lists."""
    gg, _ = filter_paths(env)
    if not gg.exists():
        return False
    high, low = load_standings(env)
    total = sum(it.get('hr') or 0 for it in state.get('items', []))
    gg_n = sum(1 for r in (rows or []) if r.get('standing') == 'GG')
    check_n = sum(1 for r in (rows or []) if r.get('standing') == 'CHECK')
    stamp = datetime.now(timezone.utc).strftime('%H:%M UTC')
    body_text = 'standings GG>=%.2f CHECK>=%.2f | %d GG / %d check | %.2f HR | engine %s' % (
        high, low, gg_n, check_n, total, stamp)
    text = '%GRAY%' + body_text
    new_alias = 'Alias[GG_ENGINE_STATUS]: ' + text
    targets = {gg}
    for path in targets:
        t = path.read_text(encoding='utf-8')
        if 'Alias[GG_ENGINE_STATUS]' in t:
            t = re.sub(r'^Alias\[GG_ENGINE_STATUS\]:.*$', new_alias, t, count=1, flags=re.M)
        elif 'Alias[GG_MONEY_TAG]' in t:
            t = t.replace('Alias[GG_MONEY_TAG]', new_alias + '\nAlias[GG_MONEY_TAG]', 1)
        else:
            continue
        path.write_text(t, encoding='utf-8', newline='\n')
    return True


def esc(s):
    import html
    return html.escape(str(s))

def write_dashboard(state):
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    # NET VALUE = the verified server figure (pd2trader's own engine) when
    # available — the owner's rule: never fabricate a net value. The per-item
    # server prices below remain for the listing gates.
    srv = state.get('server_total')
    total = srv if isinstance(srv, (int, float)) else \
        sum(it.get('hr') or 0 for it in state.get('items', [])
            if it.get('hr') is not None
            and not (it.get('confidence') == 'server(low)'))
    banner = ''
    if state.get('token_expired'):
        banner = ('<div style="background:#7a1010;color:#fff;padding:12px 16px;font-weight:bold">'
                  'Token Expired - refresh via: python scripts/stash_pricer.py --import-token '
                  '(pd2trader app) or paste PD2_AUTH_TOKEN into .env</div>')
    rows_html, md_rows = [], []
    groups = {}
    for it in state.get('items', []):
        key = (it.get('character') or 'Stash', it.get('tab') or '-')
        groups.setdefault(key, []).append(it)
    for (char, tab), items in sorted(groups.items()):
        sub = sum(i.get('hr') or 0 for i in items)
        rows_html.append('<h2>%s &rsaquo; %s <small>(%s HR)</small></h2><table border=1 cellpadding=4 '
                         'style="border-collapse:collapse;font-family:Consolas,monospace;font-size:13px">'
                         '<tr><th>Pos</th><th>Item</th><th>Quality</th><th>iLvl</th><th>Value (HR)</th>'
                         '<th>WSS &asymp;</th><th>Price Source (audit)</th><th>Listing note (copy)</th></tr>'
                         % (esc(char), esc(tab), round(sub, 2)))
        md_rows.append('\n## %s › %s — %s HR\n' % (char, tab, round(sub, 2)))
        md_rows.append('| Pos | Item | Quality | iLvl | Value (HR) | Price Source | Listing note |')
        md_rows.append('|---|---|---|---|---|---|---|')
        for it in sorted(items, key=lambda x: -(x.get('hr') or 0)):
            note = 'wts %s' % it.get('name', '')
            if it.get('hr'):
                note += ' // %s HR' % round(it['hr'], 2)
            # D6: Price provenance for audit
            prov = ''
            sc = it.get('sold') or {}
            prov_data = sc.get('provenance') or {}
            if prov_data:
                tier = prov_data.get('tier', 'none')
                if tier != 'none':
                    src_name = prov_data.get('name', '')
                    total_sold = prov_data.get('total_sold', 0)
                    priced_sold = prov_data.get('priced_sold', 0)
                    median_hr = prov_data.get('median_hr')
                    last_sold = prov_data.get('last_sold', '')
                    if median_hr is not None:
                        prov = '%s: %d sold (%d priced), median %.2f HR, last %s' % (
                            tier, total_sold, priced_sold, median_hr, last_sold)
                    else:
                        prov = '%s: %d sold (%d priced), last %s' % (
                            tier, total_sold, priced_sold, last_sold)
            rows_html.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
                             '<td><b>%s</b></td><td>%s</td><td title="%s">%s</td>'
                             '<td><input style="width:340px" readonly value="%s" onclick="this.select()"></td></tr>'
                             % (esc(it.get('pos', '')), esc(it.get('name', '')),
                                esc(it.get('quality', '')), esc(it.get('ilvl', '')),
                                esc(round(it.get('hr') or 0, 2)),
                                esc(int(round((it.get('hr') or 0) * 100))),
                                esc(prov), esc(prov[:60] + ('…' if len(prov) > 60 else '')),
                                esc(note)))
            md_rows.append('| %s | %s | %s | %s | %s | `%s` | `%s` |' % (
                it.get('pos', ''), it.get('name', ''), it.get('quality', ''),
                it.get('ilvl', ''), round(it.get('hr') or 0, 2), prov, note))
        rows_html.append('</table>')
        md_rows.append('')
    # equipped-gear standalone section: priced (jewels rolled in), never listed
    eq = state.get('equipped') or []
    equipped_html, equipped_md = '', ''
    if eq:
        eq_total = sum(r['hr'] for r in eq)
        equipped_html = ('<h2>Equipped gear — worn, NOT for sale <small>(%.2f HR, socketed jewels '
                         'priced in &middot; F5 toggle)</small></h2>'
                         '<table border=1 cellpadding=4 style="border-collapse:collapse;'
                         'font-family:Consolas,monospace;font-size:13px">'
                         '<tr><th>Character</th><th>Item</th><th>Quality</th><th>HR</th>'
                         '<th>Socketed</th></tr>' % round(eq_total, 2))
        equipped_md = ('\n## Equipped gear — worn, NOT for sale — %.2f HR\n\n'
                       '| Character | Item | Quality | HR | Socketed |\n|---|---|---|---|---|\n'
                       % round(eq_total, 2))
        for r in eq:
            equipped_html += ('<tr><td>%s</td><td>%s</td><td>%s</td><td><b>%s</b></td>'
                              '<td>%s</td></tr>'
                              % (esc(r['character']), esc(r['name']), esc(r['quality']),
                                 esc(round(r['hr'], 2)), esc(r['socketed'])))
            equipped_md += ('| %s | %s | %s | %s | %s |\n'
                            % (r['character'], r['name'], r['quality'],
                               round(r['hr'], 2), r['socketed']))
        equipped_html += '</table>'
        equipped_md += '\n'
    srv = state.get('server_total')
    srv_line = ''
    if isinstance(srv, (int, float)):
        srv_line = ('<div style="background:#0f2a12;color:#9fe8a8;padding:8px 16px;margin-bottom:10px">'
                    '<b>Net worth: %.2f HR</b> — verified from the pd2trader engine%s</div>'
                    % (srv, (' · %s items unpriced by the server (excluded from its total)'
                             % state.get('server_unpriced')) if state.get('server_unpriced') else ''))
    html_doc = ('<!doctype html><meta charset="utf-8"><title>GG DEEP DELVE - Stash Prices</title>'
                '<meta http-equiv="refresh" content="60">'
                '<style>body{background:#0b0b10;color:#eee;font-family:Segoe UI,Consolas;margin:24px}'
                'h1{color:#FFD700} h2{color:#9B9BFF} small{color:#999} input{font-family:Consolas}</style>'
                + banner + srv_line +
                '<h1>GG DEEP DELVE - Live Stash Pricing <small>updated %s &middot; net worth %s HR &middot; '
                'auto-refresh 60s</small></h1>' % (now, round(total, 2))
                + ''.join(rows_html) + equipped_html +
                '<p><small>values: pd2trader.com ladder SC (public API); stash data: your PD2 account '
                'via the pd2trader-app token; rares appraised with exact rolls.</small></p>')
    DASH_HTML.write_text(html_doc, encoding='utf-8')
    md = ('# GG DEEP DELVE - Stash Prices\n\nupdated %s — net worth %s HR (verified: pd2trader engine)\n\n'
          'values: pd2trader.com ladder SC (public API); rares appraised with exact rolls.\n'
          % (now, round(total, 2)))
    if isinstance(srv, (int, float)):
        md += ('\n**Verified server net worth: %.2f HR** (pd2trader engine)%s\n'
               % (srv, (' · %s items unpriced by the server' % state.get('server_unpriced'))
                  if state.get('server_unpriced') else ''))
    if state.get('token_expired'):
        md += '\n**Token Expired — Refresh via --import-token**\n'
    DASH_MD.write_text(md + '\n'.join(md_rows) + equipped_md, encoding='utf-8')

# ------------------------------------------------------------------ scan + cache
def fingerprint(it):
    blob = json.dumps({'c': it.get('code'), 'n': it.get('name'), 'i': it.get('ilvl'),
                       's': it.get('sockets'), 'e': it.get('ethereal'),
                       'q': it.get('quality'), 'm': sorted(str(m) for m in it.get('modifiers', [])),
                       'q2': it.get('qty')}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()

def scan_once(token, env, tab_filter=None, full=False):
    account = env.get('PD2_ACCOUNT_NAME', '')
    if not account:
        raise RuntimeError('PD2_ACCOUNT_NAME missing in .env (needed for /game/stash/<account>)')
    items = fetch_stash_items(token, account, env=env)
    if tab_filter:
        items = [it for it in items if tab_filter.lower() in it.get('tab', '').lower()
                 or tab_filter.lower() in (it.get('name') or '').lower()]
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            cache = {}
    # D1: Load owner's user_id from cache and pre-fetch sold market corpus
    # with own-sales excluded. This ensures the corpus is fresh before pricing.
    owner_user_id = cache.get('user_id')
    if owner_user_id:
        fetch_sold_market(owner_user_id=owner_user_id)
    else:
        # Fallback: try to resolve from a live listing
        try:
            if token:
                owner_user_id = resolve_user_id(token)
                if owner_user_id:
                    cache['user_id'] = owner_user_id
                    fetch_sold_market(owner_user_id=owner_user_id)
        except Exception:
            pass
    seen = cache.get('items', {})
    fresh = []
    for it in items:
        fp = fingerprint(it)
        prev = seen.get(fp)
        if prev and isinstance(prev, dict) and prev.get('hr') is not None \
                and not prev.get('needs_probe') and not full:
            fresh.append(dict(it, hr=prev.get('hr'), status='cached'))
            continue
        fresh.append(dict(it))
    # ---- deep delve: the hook's server-side networth/sync (one call prices the
    # whole stash and appraises every rare; join keys verified above). Legacy
    # valuate/probe paths stay as fallback for anything the server leaves unpriced.
    nw_lines, nw_rares, all_val_lines = {}, {}, []
    server_total = None
    server_unpriced = None
    try:
        _sync = networth_sync(token, env.get('PD2_ACCOUNT_NAME', ''))
        nw_lines, nw_rares, all_val_lines = networth_join(_sync)
        # the server's own net worth (verified anchor): pd2trader's engine
        # values the stash itself - this is the headline number, ours is not
        _val = _sync.get('valuation') or {}
        server_total = _val.get('totalHr')
        server_unpriced = _val.get('unpricedCount')
        if isinstance(server_total, (int, float)):
            print('  server net worth: %.2f HR (unpriced by server: %s)'
                  % (server_total, server_unpriced))
    except Exception as e:                                  # noqa: BLE001
        print('  networth sync failed (%r) - falling back to legacy pricing' % e)
    # currency-panel rows: server-vouched mats (misc/uber/keys) that live in
    # the currency panel; dict-walk rows the panel already covers are dropped
    panel = networth_panel_rows(all_val_lines, fresh) if all_val_lines else []
    if panel:
        panel_names = {r['name'].strip().lower() for r in panel}
        fresh = [it for it in fresh
                 if not (str(it.get('id', '')).startswith('cur-')
                         and (it.get('name') or '').strip().lower() in panel_names)]
        fresh.extend(panel)
        print('  panel rows: %d (server-vouched currency-panel mats)' % len(panel))
    # ---- pricing (v0.4.2: SERVER-ONLY VALUES — the pd2trader engine is the
    # source of truth; the items it leaves unpriced stay unpriced here too,
    # never fabricated). Only rares missing from the server appraisal get the
    # synthesized-batch fallback.
    rare_items = []
    for it in fresh:
        q = it.get('quality')
        _raw = it.get('raw') or {}
        _loc = _raw.get('location') or {}
        _pos = _raw.get('position') or {}
        if q in ('Rare', 'Craft') and (
                (_loc.get('stash_page'), _pos.get('column'), _pos.get('row')) not in nw_rares):
            rare_items.append(it)
    priced_items = []
    aps = appraise_rares(rare_items) if rare_items else {}
    for it in fresh:
        it2 = dict(it)
        q = it['quality']
        raw = it.get('raw') or {}
        loc = raw.get('location') or {}
        pos = raw.get('position') or {}
        rare_key = (loc.get('stash_page'), pos.get('column'), pos.get('row'))
        nw_line = nw_lines.get(str(raw.get('hash') or ''))
        server_priced = False
        # user rule 2026-11-19: ALL stackable items (qty > 1 — weapon stacks,
        # panel stacks, everything) are removed from the watcher's pricing and
        # listings entirely. The server's own net worth still counts them.
        if (it.get('qty') or 1) > 1:
            it2['hr'] = None
            it2['unpriced'] = True
            it2['stackable'] = True
            priced_items.append(it2)
            continue
        if q in ('Rare', 'Craft'):
            # the server's own rare appraisal (rank + market band); the
            # synthesized batch is fallback coverage for rares it skipped
            sr = nw_rares.get(rare_key)
            ap = aps.get(it['id']) or {}
            if sr:
                band = sr.get('market') or {}
                lo, hi = band.get('lowHr'), band.get('highHr')
                ap = dict(ap,
                          verdict=sr.get('verdict'),
                          rank=sr.get('rank'),
                          checkPriority=sr.get('checkPriority'),
                          band={'low': lo, 'mid': (lo + hi) / 2 if lo is not None and hi is not None else None,
                                'high': hi} if (lo is not None or hi is not None) else ap.get('band'),
                          stats=sr.get('stats') or ap.get('stats') or [])
            it2['verdict'] = ap.get('verdict')
            it2['appraisal'] = ap
            band = ap.get('band') or {}
            hr = band.get('mid') or band.get('high') or band.get('low')
            if isinstance(hr, (int, float)) and hr > 0:
                it2['hr'] = round(hr, 4)
                it2['confidence'] = ('server-rare(rank %.2f)' % sr.get('rank')) if sr \
                    else 'appraisal-fallback'
                it2['samples'] = (sr.get('market') or {}).get('sampleSize') if sr else None
                server_priced = True
            else:
                it2['hr'] = None
                it2['unpriced'] = True
        elif nw_line is not None and isinstance(nw_line.get('unitPrice'), (int, float)) \
                and nw_line['unitPrice'] > 0:
            # SERVER-PRICED (verified anchor): the pd2trader engine's own
            # valuation, hash-joined. Nothing overrides it. Lines the server
            # marks low-confidence are displayed but NOT sellable and NOT
            # counted in the total (mirrors the server's own net-worth rule).
            it2['hr'] = round(nw_line['unitPrice'] * it.get('qty', 1), 4)
            it2['confidence'] = 'server(%s)' % nw_line.get('confidence')
            it2['samples'] = nw_line.get('sampleCount')
            it2['nw'] = {'key': nw_line.get('key'), 'sampleCount': nw_line.get('sampleCount'),
                         'change7d': nw_line.get('priceChange7dPercent')}
            server_priced = nw_line.get('confidence') in ('ok', 'fixed')
        elif it.get('panel'):
            hr = it.get('hr')
            if isinstance(hr, (int, float)) and hr > 0:
                it2['hr'] = hr
                it2['confidence'] = 'server-panel'
                server_priced = True
            else:
                it2['hr'] = None
                it2['unpriced'] = True
        else:
            # the server leaves this unpriced (its own low-confidence gate) -
            # mirrored honestly: unpriced here too, never fabricated
            it2['hr'] = None
            it2['unpriced'] = True
        it2['sale_ok'] = server_priced
        priced_items.append(it2)
    # steal baseline (v0.4.3): every posting price is HARD 2-decimal (x.xx —
    # 4-decimal prices read botish, measured on the live market). Corrupted
    # uniques price from the API's OWN corruption feed: the specific
    # corruption's median (measured: Steel Carapace High+21% and Crown of
    # Ages High+39% came from the ask median, not the corruption's real
    # clearing price). Everything else: the median ask of OTHER sellers
    # (ours excluded) x the discount.
    owner_uid = cache.get('user_id')
    discount = float(env.get('GG_FAIR_DISCOUNT', '0.87'))
    n_base = 0
    for it in priced_items:
        nm = it.get('name')
        qty = it.get('qty') or 1
        raw = it.get('raw') or {}
        cat = (raw.get('category') or '').strip().lower()
        is_gear = cat in ('weapon', 'armor') or qty <= 1
        target = None                                   # total-stack posting price
        if it.get('quality') in ('Rare', 'Craft'):
            sc = sold_comps(it, window_days=int(env.get('GG_SALE_WINDOW_DAYS', '7')))
            it['sold'] = sc
            it['eff_vol'] = sc['count']                 # the type's sold volume = the API-native gate-2 volume
            if sc['priced'] >= 2 and sc['median']:
                band_val = it.get('hr')
                if isinstance(band_val, (int, float)) and band_val > 0:
                    # per-item stat-aware price: the server's own appraisal
                    it['post_price'] = round(band_val * discount, 2)
                    it['confidence'] = 'server-per-item(x%.2f; type sold n=%d, priced=%d)' % (
                        discount, sc['count'], sc['priced'])
                else:
                    # no band: the type's sold median is the only anchor
                    it['post_price'] = round(sc['median'] * discount, 2)
                    it['confidence'] = 'sold-median(%d sold, %d priced)' % (sc['count'], sc['priced'])
            else:
                it['hr'] = None
                it['unpriced'] = True
                it['sale_ok'] = False
                continue
            else:
                it['hr'] = None
                it['unpriced'] = True
                it['sale_ok'] = False
                continue
        if it.get('quality') == 'Unique' and raw.get('corrupted'):
            # corrupted unique: the API's corruption feed prices THIS corruption
            feed = corruption_feed_price(nm, raw.get('corruptions') or [])
            if feed is not None:
                target = round(feed * discount, 2)
        if target is None:
            comp = market_comp_ask(nm, exclude_uid=owner_uid)
            if not comp:
                continue
            if is_gear:
                fair = comp['med']                      # median ask of others = Fair
                target = round(fair * discount, 2)      # total, posting format
            else:
                fair_unit = comp['unit_med']
                if fair_unit is None:
                    continue
                target = round(fair_unit * discount, 2)  # unit, posting format
        it['post_price'] = target
        judged = target * (qty if (not is_gear and qty > 1) else 1)
        if it['hr'] is None:
            # a rare whose band was missing but the sold corpus has evidence:
            # the sold median IS its value now
            it['hr'] = target
            n_base += 1
        elif it['hr'] > judged:
            n_base += 1
    print('steal baseline: %d item(s) posted under Fair (x%.2f discount, 2-decimal hard)' % (n_base, discount))
    # gate 2 (v0.4.2): price floor 0.25 HR, with the API-NATIVE volume
    # exception - the server's own sampleCount per valuation line. An item
    # under the floor still lists when its line's sample count ranks in the
    # top 75% of the server-priced pool (user rule: every item gets a chance)
    floor = float(env.get('GG_LIST_FLOOR', '0.25'))
    top_pct = min(99, max(1, int(env.get('GG_VOL_TOP_PCT', '75'))))
    # D5 NOTE: lower GG_VOL_TOP_PCT = STRICTER (cutoff moves up toward median).
    # top_pct=75 -> 25th percentile (top 75% qualify)
    # top_pct=50 -> 50th percentile/median (top 50% qualify) - STRICTER

    def eff_vol(it):
        # the volume evidence: sold-corpus count for rares/crafts (set in the
        # steal pass), the server line's sampleCount for everything else
        return it.get('eff_vol') or it.get('samples') or 0

    vols = sorted(eff_vol(it) for it in priced_items if it.get('sale_ok') and eff_vol(it) > 0)
    if vols:
        vol_cutoff = vols[min(len(vols) - 1, int(len(vols) * (100 - top_pct) / 100))]
    else:
        vol_cutoff = 0
    for it in priced_items:
        it['list_ok'] = bool(it.get('sale_ok')) and (
            (it.get('hr') or 0) >= floor or (eff_vol(it) > 0 and eff_vol(it) >= vol_cutoff))
    n_floor = sum(1 for it in priced_items if it.get('list_ok') and (it.get('hr') or 0) >= floor)
    n_vol = sum(1 for it in priced_items if it.get('list_ok') and (it.get('hr') or 0) < floor)
    print('gate 2: %d listable (%d by price >= %.2f HR, %d by volume top-%d%%)'
          % (n_floor + n_vol, n_floor, floor, n_vol, top_pct))
    # cache
    for it in priced_items:
        seen[fingerprint(it)] = {'name': it.get('name'), 'hr': it.get('hr'),
                                 'needs_probe': bool(it.get('needs_probe')),
                                 'ts': datetime.now(timezone.utc).isoformat()}
    cache['items'] = seen
    cache['last_scan'] = datetime.now(timezone.utc).isoformat()
    # keep raw stash objects for the instant-sell hotkey (F7) and auto-listing
    cache['raw_items'] = {it['id']: it.get('raw') for it in items if it.get('raw')}
    CACHE.write_text(json.dumps(cache, indent=1), encoding='utf-8')
    state = {'items': priced_items, 'token_expired': False, 'last_scan': cache['last_scan'],
             'server_total': server_total, 'server_unpriced': server_unpriced}
    # equipped-gear valuation: STANDALONE (toggle GG_SCAN_EQUIPPED / F5) - worn
    # items are priced for information only and never listed or ranked, so the
    # 45 rotation slots are never spent on them (user rule 2026-11-18)
    state['equipped'] = scan_equipped_gear(env) if \
        str(env.get('GG_SCAN_EQUIPPED', '0')).strip().lower() in ('1', 'true', 'yes') else []
    write_dashboard(state)
    return state

# ------------------------------------------------------------------ filter init
def init_filter(env):
    gg, src = filter_paths(env)
    if gg.exists():
        print('GG filter already present: %s' % gg)
        return
    bak = src.with_suffix('.filter.bak')
    if not bak.exists():
        shutil.copyfile(src, bak)
        print('backup created: %s' % bak)
    shutil.copyfile(src, gg)
    print('initialized %s from %s' % (gg.name, src.name))

# ------------------------------------------------------------------ game detection + hotkeys
def detect_game_dir():
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "Get-CimInstance Win32_Process -Filter \"Name='Game.exe' OR Name='Diablo II.exe'\""
             " | Select-Object -First 1 -ExpandProperty ExecutablePath"],
            capture_output=True, timeout=30)
        path = out.stdout.decode('utf-8', 'replace').strip()
        return Path(path) if path and path.lower().endswith('.exe') else None
    except Exception:                                       # noqa: BLE001
        return None

def find_filters_dir(game_dir):
    for cand in (game_dir / 'filters' / 'local', game_dir / 'ProjectD2' / 'filters' / 'local'):
        if cand.is_dir():
            return cand
    return None

def ensure_filter_linked(filters_dir):
    target = filters_dir / 'GG_DEEP_DELVE.filter'
    if not target.exists():
        source = REPO / 'filter' / 'GG_DEEP_DELVE.filter'
        if not source.exists():
            print('  ! repo filter copy missing: %s' % source)
            return False
        shutil.copyfile(source, target)
        print('  installed GG_DEEP_DELVE.filter -> %s' % target)
    else:
        print('  GG_DEEP_DELVE.filter already present in %s' % filters_dir)
    loot = filters_dir.parent.parent / 'loot.filter'
    if loot.exists():
        try:
            head = loot.read_text(encoding='utf-8', errors='replace')
        except Exception:                                   # noqa: BLE001
            head = ''
        if 'GG DEEP DELVE price engine' not in head:
            shutil.copyfile(loot, loot.with_name('loot.filter.pre-gg'))
            shutil.copyfile(target, loot)
            print('  activated: loot.filter now loads GG DEEP DELVE (backup loot.filter.pre-gg)')
        else:
            print('  loot.filter already loads GG DEEP DELVE')
    return True

RELOAD_VK_NAMES = {
    'VK_NUMPAD0': 'num 0', 'VK_NUMPAD1': 'num 1', 'VK_NUMPAD2': 'num 2',
    'VK_NUMPAD3': 'num 3', 'VK_NUMPAD4': 'num 4', 'VK_NUMPAD5': 'num 5',
    'VK_NUMPAD6': 'num 6', 'VK_NUMPAD7': 'num 7', 'VK_NUMPAD8': 'num 8',
    'VK_NUMPAD9': 'num 9',
}

def reload_key_name(game_dir):
    cfg = game_dir / 'ProjectDiablo.cfg'
    try:
        for line in cfg.read_text(encoding='utf-8', errors='replace').splitlines():
            low = line.lower()
            if 'reload' in low:
                for vk, name in RELOAD_VK_NAMES.items():
                    if vk.lower() in low:
                        return name
    except Exception:                                       # noqa: BLE001
        pass
    return 'num 0'

def send_reload(game_dir):
    try:
        import keyboard                                     # noqa: PLC0415
        key = reload_key_name(game_dir)
        keyboard.send(key)
        print('  reload keystroke sent (%s) - filter reloaded live in game' % key)
        return True
    except Exception as e:                                  # noqa: BLE001
        print('  could not send reload (%r) - press Numpad 0 manually' % e)
        return False

def link_running_game():
    game = detect_game_dir()
    if not game:
        print('  no running Game.exe / Diablo II.exe detected - start the game first')
        return False
    print('  running client: %s' % game.parent)
    fdir = find_filters_dir(game.parent)
    if not fdir or not ensure_filter_linked(fdir):
        return False
    send_reload(game.parent)
    return True

def restart_running_client():
    game = detect_game_dir()
    if not game:
        print('  no running client detected')
        return False
    print('  WARNING: killing Game.exe now - hardcore characters die. Ctrl+C within 5s to abort.')
    time.sleep(5)
    subprocess.run(['taskkill', '/IM', 'Game.exe', '/F'], capture_output=True)
    time.sleep(3)
    subprocess.Popen([str(game)], cwd=str(game.parent))
    print('  relaunched from %s (sign in again if the launcher session is required)' % game.parent)
    return True

# ------------------------------------------------------------------ clipboard (F10)
def clipboard_text():
    out = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-Clipboard -Raw'],
                         capture_output=True, timeout=15)
    return out.stdout.decode('utf-8', 'replace').strip()

def parse_pd2_clipboard(text):
    m = re.search(r'\{[\s\S]*"name"[\s\S]*\}', text)
    if not m:
        return None
    try:
        item = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(item, dict) or 'name' not in item:
        return None
    return item

def appraise_clipboard():
    raw = clipboard_text()
    item = parse_pd2_clipboard(raw)
    if not item:
        print('Clipboard is not a PD2 item copy. Hover an item in game, press Ctrl+C, then try again.')
        return None
    name = item.get('name', '')
    quality = item.get('quality', '')
    result = {'name': name, 'quality': quality}
    if quality in ('Rare', 'Crafted'):
        try:
            _, d = http_json(APPRAISAL_URL,
                             body={'text': raw, 'isLadder': True, 'isHardcore': False})
            ap = d.get('appraisal') or {}
            result['verdict'] = ap.get('verdict')
            band = ap.get('band') or ap.get('priceBand') or {}
            result['band'] = band
        except Exception as e:                              # noqa: BLE001
            result['error'] = 'appraisal failed: %r' % e
    try:
        _, d = http_json(PRICE_CHECK_URL, {'item': name, 'quality': quality or None,
                                           'isLadder': True, 'isHardcore': False, 'hours': 168})
        e = d.get('estimateHr') or {}
        result['pc'] = (e.get('low'), e.get('mid'), e.get('high'), d.get('confidence'))
    except Exception as e:                                  # noqa: BLE001
        result['error'] = result.get('error', '') + ' price-check failed: %r' % e
    return result

# ------------------------------------------------------------------ toast + chime
def chime():
    try:
        import winsound
        winsound.Beep(880, 120)
        winsound.Beep(1320, 160)
    except Exception:                                       # noqa: BLE001
        pass

def toast(title, lines, seconds=7):
    try:
        import tkinter as tk
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.geometry('340x%d+40+80' % (56 + 22 * len(lines)))
        frame = tk.Frame(root, bg='#101018', highlightbackground='#D3A000', highlightthickness=2)
        frame.pack(fill='both', expand=True)
        tk.Label(frame, text=title, bg='#101018', fg='#FFD700',
                 font=('Consolas', 11, 'bold')).pack(anchor='w', padx=10, pady=(8, 2))
        for ln in lines:
            tk.Label(frame, text=ln, bg='#101018', fg='#EDEDED',
                     font=('Consolas', 9)).pack(anchor='w', padx=10)
        root.after(seconds * 1000, root.destroy)
        root.update_idletasks()
        root.mainloop()
        return True
    except Exception:                                       # noqa: BLE001
        print('[toast] %s | %s' % (title, ' | '.join(lines)))
        return False

# ------------------------------------------------------------------ instant sell (F7)
def instant_sell(env):
    """Hover item -> Ctrl+C -> F7: parse the exact rolls, price via pd2trader,
    match the stash copy, and POST the listing with that price. If the item is
    not in the stash yet, price it and say so (move it to stash, F7 again)."""
    token = env.get('PD2_AUTH_TOKEN', '')
    if not token:
        print('[F7] no token - run --import-token')
        return
    raw_clip = clipboard_text()
    item = parse_pd2_clipboard(raw_clip)
    if not item:
        print('[F7] clipboard is not a PD2 item copy (hover item, Ctrl+C first)')
        return
    name = item.get('name', '')
    quality = item.get('quality', '')
    ilvl = item.get('iLevel')
    # 1. price it (exact rolls for rares/crafts)
    ask, basis = None, ''
    if quality in ('Rare', 'Crafted'):
        try:
            _, d = http_json(APPRAISAL_URL, body={'text': raw_clip,
                                                  'isLadder': True, 'isHardcore': False})
            ap = d.get('appraisal') or {}
            band = ap.get('band') or ap.get('priceBand') or {}
            hi, mid = band.get('high'), band.get('mid')
            ask = hi if isinstance(hi, (int, float)) and hi else (
                round(mid * 1.1, 2) if isinstance(mid, (int, float)) else None)
            basis = 'appraisal %s (rank %.2f)' % (ap.get('verdict'), ap.get('rank') or 0)
        except Exception as e:                              # noqa: BLE001
            print('[F7] appraisal failed: %r' % e)
    if ask is None:
        try:
            _, d = http_json(PRICE_CHECK_URL, {'item': name, 'quality': quality or None,
                                               'isLadder': True, 'isHardcore': False, 'hours': 168})
            e = d.get('estimateHr') or {}
            ask, basis = e.get('high') or e.get('mid'), 'comps %s' % d.get('confidence')
        except Exception as e:                              # noqa: BLE001
            print('[F7] price check failed: %r' % e)
    if not isinstance(ask, (int, float)):
        print('[F7] no price for %s - not listing' % name)
        return
    # 2. match the stash copy (the market lists stash items, like pd2trader does)
    cache = json.loads(CACHE.read_text(encoding='utf-8')) if CACHE.exists() else {}
    raw_items = cache.get('raw_items') or {}
    match = None
    for iid, raw in raw_items.items():
        b = (raw.get('base') or {})
        if ((raw.get('name') or b.get('name')) == name
                and raw.get('item_level') == ilvl):
            match = dict(raw, account_id=env.get('PD2_ACCOUNT_NAME', '').lower())
            stash_id = iid
            break
    if not match:
        print('[F7] %s priced %.2f HR (%s) - not in the stash yet; move it in and press F7 again'
              % (name, ask, basis))
        toast('GG DEEP DELVE - %s' % name,
              ['%.2f HR (%s)' % (ask, basis), 'not in stash - move it in, F7 again'])
        return
    # 3. list it
    note = 'wts %s o.n.o' % name
    try:
        listing = post_listing(token, match, env.get('PD2_ACCOUNT_NAME', ''), round(ask, 2), note)
        lid = (listing or {}).get('_id') or (listing or {}).get('id')
        print('[F7] LISTED %s @ %.2f HR (%s) id=%s' % (name, ask, basis, lid))
        chime()
        toast('GG DEEP DELVE - LISTED', ['%s @ %.2f HR' % (name, ask), basis])
    except urllib.error.HTTPError as e:
        print('[F7] listing failed -> %d %s' % (e.code,
                                                e.read()[:150].decode('utf-8', 'replace')))
    except Exception as e:                                  # noqa: BLE001
        print('[F7] listing failed: %r' % e)


# ------------------------------------------------------------------ hotkeys
def start_hotkeys(on_f9, on_f10, on_f8, on_f7=None, on_f6=None, on_f5=None):
    """Global hotkeys via `keyboard` (pip install keyboard). Runs as a daemon
    thread; degrades to polling-only when unavailable (or without admin rights
    while PD2 runs elevated)."""
    try:
        import keyboard                                     # noqa: PLC0415
    except Exception as e:                                  # noqa: BLE001
        print('! hotkeys unavailable (pip install keyboard; admin if PD2 is elevated): %r' % e)
        return None
    for hot, fn in [('f5', on_f5), ('f6', on_f6), ('f7', on_f7), ('f8', on_f8),
                    ('f9', on_f9), ('f10', on_f10)]:
        if fn is None:
            continue
        try:
            keyboard.add_hotkey(hot, fn)
            print('hotkey registered: %s' % hot.upper())
        except Exception as e:                              # noqa: BLE001
            print('! hotkey %s failed: %r' % (hot, e))
    return True

# ------------------------------------------------------------------ discovery (product core)
def price_str(ask):
    """pd2trader dialog format: >= 1 HR -> 'N HR', below -> 'N WSS' (the app's
    price input takes either, per the user's screenshot)."""
    if ask >= 1.0:
        return '%g HR' % round(ask, 2)
    return '%d WSS' % int(round(ask * 100))

def discover(state, env, out_path=None):
    """3-standings discovery: every priced item -> GG / CHECK / VENDOR, with a
    prepared listing note for the GG tier. Deterministic: thresholds from .env
    (GG_STAND_HIGH / GG_STAND_CHECK), rare verdicts from pd2trader's appraiser."""
    high, low = load_standings(env)
    rows = []
    for it in state.get('items', []):
        standing, ask, stars, verdict = classify(it, high, low, it.get('appraisal'))
        key_stats = []
        ap = it.get('appraisal') or {}
        for s in ap.get('stats', []) or []:
            if isinstance(s, dict) and s.get('stars'):
                key_stats.append(s.get('label') or s.get('key'))
        if not key_stats and it.get('modifiers'):
            mods = sorted(it['modifiers'], key=lambda m: -(m[1] or 0))[:2]
            key_stats = ['%s %s' % (v, k) for k, v in mods]
        note = strict_note(it, obo=True)
        if ask:
            note += ' // %s' % price_str(ask)
        rows.append({'standing': standing, 'ask': ask or 0, 'stars': stars,
                     'verdict': verdict, 'note': note, 'item': it,
                     'price_str': price_str(ask) if ask else ''})
    order = {'GG': 0, 'CHECK': 1, 'VENDOR': 2}
    rows.sort(key=lambda r: (order[r['standing']], -r['ask']))
    gg = [r for r in rows if r['standing'] == 'GG']
    check = [r for r in rows if r['standing'] == 'CHECK']
    vendor = [r for r in rows if r['standing'] == 'VENDOR']

    print('\n===== DISCOVERY (standings: GG>=%.2f HR | CHECK>=%.2f HR) =====' % (high, low))
    print('GG (%d) - queue these in pd2trader (paste note + price):' % len(gg))
    for r in gg[:15]:
        print('  [GG]    %-30s %-9s %s' % ((r['item'].get('name') or '')[:30], r['price_str'], r['note'][:80]))
    print('CHECK (%d) - worth a look:' % len(check))
    for r in check[:10]:
        print('  [CHECK] %-30s %-9s' % ((r['item'].get('name') or '')[:30], r['price_str']))
    print('VENDOR (%d) - everything else' % len(vendor))

    md = ['# GG DEEP DELVE - Discovery %s' % datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
          '',
          'standings: GG >= %.2f HR | CHECK >= %.2f HR | else VENDOR' % (high, low),
          'GG items are listing-ready: copy the note into the PD2 market (or pd2trader Quick List).',
          '']
    md += ['## GG - queue these (%d)' % len(gg), '',
           'paste the note + price into pd2trader\'s Queue Item dialog (Ctrl+D on the item, Ctrl+L):',
           '', '| Item | Tab | Price | Listing note |', '|---|---|---|---|']
    for r in gg:
        it = r['item']
        md.append('| %s | %s | `%s` | `%s` |' % (esc(it.get('name', '')), esc(it.get('tab', '')),
                                                 r['price_str'], r['note']))
    md += ['', '## CHECK - verify before selling (%d)' % len(check), '',
           '| Item | Tab | Price | Verdict |', '|---|---|---|---|']
    for r in check:
        it = r['item']
        md.append('| %s | %s | `%s` | %s |' % (esc(it.get('name', '')), esc(it.get('tab', '')),
                                               r['price_str'], r['verdict'] or '-'))
    md += ['', '## VENDOR (%d items - full list in stash_prices.md)' % len(vendor)]
    out_path = out_path or (REPO / 'discoveries.md')
    out_path.write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('discovery file: %s' % out_path.name)
    return rows


def run_autolist(env, rows, max_posts=10, include_check=False, obo=True, do_delist=True,
                 target_n=None, max_actions=15):
    """Maintain the market at the TOP N listings by live value. N comes from
    .env GG_ROTATION_TARGET (default 45) so the server's true active-listing
    ceiling gets measured the moment the stash offers more listable items
    (the old hard-coded 45 predated the cap question)."""
    target_n = target_n or max(1, int(env.get('GG_ROTATION_TARGET', '45')))
    token = env.get('PD2_AUTH_TOKEN', '')
    account = env.get('PD2_ACCOUNT_NAME', '').lower()
    cache = json.loads(CACHE.read_text(encoding='utf-8')) if CACHE.exists() else {}
    listings = cache.setdefault('listings', {})
    stash_ids = {r['item']['id'] for r in rows if r['item'].get('id')}

    user_id = cache.get('user_id')
    own = {}
    if user_id:
        # HTTP success with an EMPTY result is a valid answer (market genuinely
        # empty - e.g. every listing sold): posting is safe, there is nothing
        # to duplicate. Only a FAILED lookup twice must block posting.
        lookup_ok = False
        for attempt in range(2):
            try:
                for l in get_own_listings(token, user_id):
                    iid = str(((l.get('item') or {}).get('id')))
                    if iid and iid != 'None':
                        own[iid] = (l.get('_id'), l.get('hr_price'))
                lookup_ok = True
                break
            except Exception as e:                      # noqa: BLE001
                print('  own-listings fetch failed: %r' % e)
                time.sleep(3)
        if not lookup_ok:
            print('  ! own-listings lookup failed twice - refusing to post (duplicate risk)')
            return [], []

    # the rotation: top N by live ask, listable (raw stash object or synthetic)
    # only GG + CHECK standing items get listed (the user's "don't waste sell
    # slots" rule: VENDOR items are junk by definition)
    ranked = [r for r in rows if r['ask'] > 0 and r['item'].get('id')
              and r['item'].get('list_ok')             # v0.4.0: gates 1+2 decide listability
              # (the GG/CHECK label stays dashboard-only; a cheap-but-high-volume
              # stack passes gate 2 by volume and must not be blocked here)
              and (r['item'].get('raw') or r['item'].get('panel'))
              # ONLY real item objects (shared stash + character personal
              # stashes) OR server-vouched currency-panel rows may be listed.
              # Synthetic currency-walk rows (raw-less, unvouched) posted
              # phantom listings - measured 2026-11-18, 4 phantoms delisted.
              and not str(r['item'].get('code', '')).startswith('r')
              and r['item'].get('list_ok')]               # v0.4.0: gate 1 (volume) + gate 2 (price floor / volume exception)
    ranked.sort(key=lambda r: (
        -((r['item'].get('sold') or {}).get('vol_name', 0)
          or (r['item'].get('sold') or {}).get('vol_type', 0)),
        -r['ask']))
    # v0.4.0: VOLUME FIRST (user rule) - the rotation is ranked by the sold
    # volume of the item's most specific evidence tier (its own name, else its
    # type), not by price. High-volume types sell fastest and take the slots.
    target = ranked[:target_n]
    target_ids = [r['item']['id'] for r in target]

    posted, delisted, kept = [], [], 0
    actions = 0

    def note_for(r):
        it = r['item']
        qty = it.get('qty') or 1
        # user rule 2026-11-18: the listing description carries ONLY the
        # stack amount — no stat text (the game shows the stats itself)
        return ('%dx o.b.o.' % qty) if qty > 1 else 'o.b.o.'

    # 1. delist: listings whose item left the stash OR fell out of the top N
    if do_delist and user_id:
        for iid, (lid, _p) in list(own.items()):
            if iid not in stash_ids or iid not in target_ids:
                if actions >= max_actions:
                    print('  action cap (%d) reached mid-delist' % max_actions)
                    break
                st = delete_listing(token, lid)
                actions += 1
                if st in (200, 204):
                    nm = listings.get(iid, {}).get('name', iid)
                    delisted.append(nm)
                    listings[iid] = listings.get(iid) or {}
                    listings[iid]['status'] = 'delisted'
                    print('  [DELISTED] %s (out of top %d or gone from stash)' % (nm, target_n))
                else:
                    print('  [delist failed] %s -> %s' % (iid, st))
                time.sleep(2.5)

    # 2. post: target items not yet listed
    own = {str(iid): (lid, p) for iid, (lid, p) in own.items() if iid in target_ids}
    for r in target:
        iid = str(r['item']['id'])
        if actions >= max_actions:
            print('  action cap (%d) reached mid-post' % max_actions)
            break
        it = r['item']
        raw = it.get('raw')
        if not raw:
            b = {'id': it.get('code'), 'category': 'misc',
                 'codes': {'normal': it.get('code')}, 'name': it.get('name'),
                 'type': 'Rune' if str(it.get('code', '')).startswith('r') else 'Material',
                 'type_code': str(it.get('code', '')),
                 'size': {'height': 1, 'width': 1}, 'requirements': {}}
            raw = {'base': b, 'quality': {'id': 1, 'name': 'Normal'},
                   'is_identified': True, 'is_ethereal': False, 'is_runeword': False,
                   'socketed_count': 0, 'item_level': 1, 'id': iid,
                   'location': {'storage': 'Shared Stash', 'stash_page': 0}}
        note = strict_note(it, obo=obo)
        # the steal baseline (post_price) is stored ALREADY in posting format
        # (total for gear incl. weapon stacks; unit for misc-panel stacks) by
        # the scan pass - use it directly, never re-divide.
        if it.get('post_price') is not None:
            post_price = round(it['post_price'], 4)
        else:
            qty = it.get('qty') or 1
            want_total = r['ask']
            _cat = ((it.get('raw') or {}).get('category') or '').strip().lower()
            post_price = round(want_total / qty, 4) if (qty > 1 and _cat not in ('weapon', 'armor')) \
                else round(want_total, 2)
        # self-repricing: a live listing whose price drifted >10% from the
        # current model (dirt-cheap sub-1HR / percentile / comp refresh) is
        # delisted now and reposted at the new price within the same tick.
        # A listing not on the 2-decimal grid (0.6525-style) ALWAYS reprices
        # (user rule: strict x.xx pricing, never botish 4-decimals).
        if iid in own and do_delist:
            lid, cur = own[iid]
            needs_format_fix = isinstance(cur, (int, float)) and round(cur, 2) != round(cur, 2) + 1e-9 \
                and abs(cur - round(cur, 2)) > 1e-9
            if isinstance(cur, (int, float)) and cur > 0 and \
                    (needs_format_fix or
                     abs(cur - post_price) > max(0.02, 0.10 * post_price)) and \
                    actions < max_actions:
                st = delete_listing(token, lid)
                actions += 1
                own.pop(iid)
                if st in (200, 204):
                    delisted.append({'name': it.get('name'), 'reason': 'repriced'})
                    print('  [REPRICED] %-28r was %.2f -> %.2f' % (
                        it.get('name'), cur, post_price))
                else:
                    continue                    # could not delist - keep the old listing
            else:
                kept += 1                       # price still current - nothing to do
                continue
        try:
            listing = post_listing(token, raw, account, post_price, note)
            lid = (listing or {}).get('_id') or (listing or {}).get('id')
            if lid:
                listings[iid] = {'listing_id': lid, 'status': 'posted',
                                 'name': it.get('name'), 'ask': r['ask'],
                                 'ts': datetime.now(timezone.utc).isoformat()}
                if (listing or {}).get('user_id') and not user_id:
                    user_id = listing['user_id']
                    cache['user_id'] = user_id
                posted.append((it.get('name'), r['ask'], note))
                print('  [POSTED] %-28s %-9s %s' % ((it.get('name') or '')[:28], r['price_str'], note))
            else:
                print('  [FAILED] %s: unexpected response' % (it.get('name'),))
        except urllib.error.HTTPError as e:
            detail = e.read()[:120].decode('utf-8', 'replace')
            print('  [FAILED] %s -> %d %s' % (it.get('name'), e.code, detail))
            if e.code in (401, 403):
                break
        except Exception as e:                              # noqa: BLE001
            print('  [FAILED] %s: %r' % (it.get('name'), e))
        actions += 1
        time.sleep(2.5)

    cache['listings'] = listings
    if user_id:
        cache['user_id'] = user_id
    CACHE.write_text(json.dumps(cache, indent=1), encoding='utf-8')
    print('rotation: %d posted, %d delisted, %d kept | target top %d' % (
        len(posted), len(delisted), kept, target_n))
    return posted, delisted

def run_once(env, tab_filter=None, full=False, discover_mode=False, do_post=False,
             max_posts=10, include_check=False, max_actions=15):
    token = env.get('PD2_AUTH_TOKEN', '')
    if not token:
        print('no token: run  python scripts/stash_pricer.py --import-token')
        return 1
    try:
        state = scan_once(token, env, tab_filter=tab_filter, full=full)
    except TokenExpired:
        print('TOKEN EXPIRED — refresh: python scripts/stash_pricer.py --import-token')
        write_dashboard({'items': [], 'token_expired': True})
        return 2
    except Exception as e:                                  # noqa: BLE001
        print('scan failed: %r' % e)
        return 3
    total = sum(i.get('hr') or 0 for i in state['items'])
    print('items priced: %d (total %.2f HR) -> %s / %s'
          % (len(state['items']), total, DASH_HTML.name, DASH_MD.name))
    if discover_mode or do_post:
        rows = discover(state, env)
    else:
        rows = discover(state, env)
    update_filter_status(env, state, rows)
    if do_post:
        run_autolist(env, rows, max_posts=max_posts, include_check=include_check,
                     max_actions=max_actions)
    return 0

def run_watch(env):
    token = env.get('PD2_AUTH_TOKEN', '')
    stash_enabled = bool(token)
    if not stash_enabled:
        print('! no PD2_AUTH_TOKEN - run --import-token; hotkeys still work meanwhile.')
    state = {'items': [], 'token_expired': False}
    paused = False

    def game_running():
        try:
            out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Game.exe'],
                                 capture_output=True, timeout=20).stdout.decode(errors='replace')
            if 'Game.exe' in out:
                return True
            out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Diablo II.exe'],
                                 capture_output=True, timeout=20).stdout.decode(errors='replace')
            return 'Diablo II.exe' in out
        except Exception:                                   # noqa: BLE001
            return False

    def force_sync():
        print('[F9] forcing economy + stash sync...')
        try:
            subprocess.run([sys.executable, str(Path(__file__).with_name('sync_pd2_prices.py'))],
                           timeout=300)
        except Exception as e:                              # noqa: BLE001
            print('  economy sync failed: %r' % e)
        if stash_enabled:
            try:
                state = scan_once(token, env)
                total = sum(i.get('hr') or 0 for i in state['items'])
                rows = discover(state, env)
                update_filter_status(env, state, rows)
                run_autolist(env, rows, max_posts=40, max_actions=45)
                print('  synced: %d items, %.2f HR' % (len(state['items']), total))
                chime()
                toast('GG DEEP DELVE - Synced', ['%d items, %.1f HR' % (len(state['items']), total),
                                                 'Numpad 0 in game to reload filter'])
            except TokenExpired as e:
                print('  TOKEN EXPIRED — run --import-token (%s)' % e)
            except Exception as e:                          # noqa: BLE001
                print('  stash sync failed: %r' % e)
        else:
            chime()
            print('  (stash pricing skipped - no token)')

    def hot_price_check():
        print('[F10] clipboard price check...')
        try:
            res = appraise_clipboard()
            if not res:
                return
            lines = []
            if res.get('pc'):
                low, mid, high, conf = res['pc']
                lines.append('%s - %s - %s HR (%s)' % (low, mid, high, conf))
            if res.get('verdict'):
                lines.append('verdict: %s' % res['verdict'])
            if not lines:
                lines.append('no live comp - see dashboard')
            toast('GG DEEP DELVE - %s' % res.get('name', 'Price Check'), lines)
        except Exception as e:                              # noqa: BLE001
            print('  price check failed: %r' % e)

    def hot_link():
        print('[F8] link + reload...')
        link_running_game()

    def hot_deep_scan():
        """[F6] scan ALL items + price them NOW (no market posts): shared stash
        (every page) + character personal stashes, deep-delve pricing, standings,
        dashboard refresh + toast summary. Equipped-gear value shown only when
        its toggle is on (F5) - standalone, never listed."""
        print('[F6] full scan + price...')
        if not stash_enabled:
            toast('GG DEEP DELVE', ['no token - run --import-token'])
            return
        try:
            state = scan_once(token, env)
            rows = discover(state, env)
            total = sum(i.get('hr') or 0 for i in state['items'])
            n_gg = sum(1 for r in rows if r['standing'] == 'GG')
            n_chk = sum(1 for r in rows if r['standing'] == 'CHECK')
            lines = ['%d items | %.2f HR' % (len(state['items']), total),
                     '%d GG / %d CHECK' % (n_gg, n_chk)]
            eq = state.get('equipped') or []
            if eq:
                lines.append('equipped (not for sale): %.2f HR | top: %s %.2f HR'
                             % (sum(r['hr'] for r in eq), eq[0]['name'], eq[0]['hr']))
            print('  scanned: %d items, %.2f HR, %d GG / %d CHECK' % (
                len(state['items']), total, n_gg, n_chk))
            toast('GG DEEP DELVE - Scanned', lines)
            chime()
        except TokenExpired as e:
            print('  TOKEN EXPIRED — run --import-token (%s)' % e)
        except Exception as e:                              # noqa: BLE001
            print('  deep scan failed: %r' % e)

    def toggle_equipped():
        """[F5] equipped-gear scan on/off (persists in .env as GG_SCAN_EQUIPPED).
        Standalone: worn gear is priced into the dashboard, never listed, and
        never spends a rotation slot."""
        cur = str(env.get('GG_SCAN_EQUIPPED', '0')).strip().lower() in ('1', 'true', 'yes')
        new = '0' if cur else '1'
        env['GG_SCAN_EQUIPPED'] = new
        write_env_key('GG_SCAN_EQUIPPED', new)
        print('[F5] equipped-gear scan: %s' % ('ON' if new == '1' else 'OFF'))
        toast('GG DEEP DELVE', ['equipped-gear scan: %s' % ('ON — priced in dashboard (standalone, never listed)'
                                                           if new == '1' else 'OFF')])
        chime()

    start_hotkeys(force_sync, hot_price_check, hot_link, on_f7=instant_sell,
                  on_f6=hot_deep_scan, on_f5=toggle_equipped)
    print('watch mode: polling every %ds while %s is running; F5 equipped toggle, '
          'F6 scan+price, F7 instant-sell, F8 link+reload, F9 sync+reload, F10 clipboard check.'
          % (POLL_SECONDS, ' / '.join(GAME_PROCESSES)))
    first_cycle = True
    first_cycle = True
    slam_toasted = set()        # item ids already popup-announced this watcher run
    while True:
        if paused or not stash_enabled or not game_running():
            time.sleep(IDLE_CHECK_SECONDS)
            continue
        try:
            state = scan_once(token, env)
            # every cycle: full pipeline — price, discover, standings, autolist
            rows = discover(state, env)
            update_filter_status(env, state, rows)
            total = sum(i.get('hr') or 0 for i in state['items'])
            n_gg = sum(1 for r in rows if r['standing'] == 'GG')
            n_check = sum(1 for r in rows if r['standing'] == 'CHECK')
            # the net worth is the server's verified figure — never our sum
            # (our arithmetic counts categories the server excludes by design)
            shown_total = state.get('server_total') if isinstance(
                state.get('server_total'), (int, float)) else total
            print('cycle: %d items | net worth %.2f HR (server) | %d GG / %d CHECK' % (
                len(state['items']), shown_total, n_gg, n_check))
            # slam popups (user request 2026-11-18): a NEWLY-seen uncorrupted
            # GG unique/craft pops the in-game toast + chime - "GO SLAM IT"
            slam_new = []
            for r in rows:
                it = r['item']
                raw = it.get('raw') or {}
                if r['standing'] != 'GG' or raw.get('corrupted') is not False:
                    continue
                if it.get('quality') not in ('Unique', 'Craft'):
                    continue
                iid = str(it.get('id'))
                if iid in slam_toasted:
                    continue
                slam_toasted.add(iid)
                slam_new.append('SLAM IT: %s - %.2f HR' % (it.get('name'), r['ask']))
            if slam_new and not first_cycle:
                toast('GG DEEP DELVE - SLAM CHECK', slam_new[:4] +
                      ['corrupt with a WSS - hover the item in game for outcomes'])
                chime()
                print('slam popup: %d new uncorrupted GG item(s)' % len(slam_new))
            if first_cycle:
                # the first cycle after watcher start: fill the market
                run_autolist(env, rows, max_posts=40, max_actions=45)
                first_cycle = False
            else:
                # per-tick rotation: re-rank + diff every cycle so new items
                # displace the bottom of the top-45 (user rule: rotate each tick)
                run_autolist(env, rows, max_posts=10, max_actions=12)
        except TokenExpired as e:
            paused = True
            state['token_expired'] = True
            write_dashboard(state)
            print('TOKEN EXPIRED — run: python scripts/stash_pricer.py --import-token (%s)' % e)
            continue
        except Exception as e:                              # noqa: BLE001
            print('poll failed: %r (retrying next cycle)' % e)
        time.sleep(POLL_SECONDS)

def main():
    env = load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--watch', action='store_true', help='background daemon (game-aware)')
    ap.add_argument('--once', action='store_true', help='one-shot stash scan + pricing')
    ap.add_argument('--discover', action='store_true', help='with --once: 3-standings discovery (GG/CHECK/VENDOR) + listing notes')
    ap.add_argument('--post', action='store_true',
                    help='with --once --discover: AUTO-LIST GG items on the PD2 market and '
                         'auto-delist anything gone from the stash (uses your token; rate-limited)')
    ap.add_argument('--max-posts', type=int, default=10, help='session cap for --post (default 10)')
    ap.add_argument('--max-actions', type=int, default=15, help='session cap for --post actions (default 15)')
    ap.add_argument('--list-check', action='store_true', help='with --post: also list CHECK-standing items')
    ap.add_argument('--tab', metavar='TEXT', help='with --once: price only tabs/items matching TEXT')
    ap.add_argument('--full', action='store_true', help='with --once: re-price everything (ignore cache)')
    ap.add_argument('--check', action='store_true', help='price-check clipboard item')
    ap.add_argument('--import-token', action='store_true', help='pull token from the pd2trader app')
    ap.add_argument('--init-filter', action='store_true', help='init GG filter from Hiim_Hyper')
    ap.add_argument('--detect', action='store_true',
                    help='detect the running client, link the filter into its install, reload live')
    ap.add_argument('--restart-client', action='store_true',
                    help='DANGEROUS: kill + relaunch the running client (hardcore death risk); '
                         'never needed for filter reloads')
    args = ap.parse_args()
    if args.import_token:
        return import_token()
    if args.init_filter:
        init_filter(env)
        return 0
    if args.restart_client:
        return 0 if restart_running_client() else 1
    if args.detect:
        return 0 if link_running_game() else 1
    if args.watch:
        WATCH_LOG.write_text('', encoding='utf-8')          # fresh log per watcher run
        sys.stdout = WatchLogTee(WATCH_LOG)
        print('[%s] watcher starting (log: watcher.log)' % datetime.now(timezone.utc).strftime('%H:%M:%S'))
        return run_watch(env)
    if args.once:
        return run_once(env, tab_filter=args.tab, full=args.full, discover_mode=args.discover,
                        do_post=args.post, max_posts=args.max_posts, include_check=args.list_check,
                        max_actions=args.max_actions)
    if args.check:
        res = appraise_clipboard()
        return 0 if res else 1
    ap.print_help()
    return 1

if __name__ == '__main__':
    sys.exit(main())
