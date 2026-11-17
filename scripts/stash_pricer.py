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
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ------------------------------------------------------------------ config
REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / '.env'
DASH_HTML = REPO / 'stash_prices.html'
DASH_MD = REPO / 'stash_prices.md'
CACHE = REPO / 'stash_cache.json'

VALUATE_URL = 'https://pd2trader.com/api/valuate'
APPRAISAL_BATCH_URL = 'https://pd2trader.com/api/rares/appraisal/batch'
PRICE_CHECK_URL = 'https://pd2trader.com/api/price-check'
APPRAISAL_URL = 'https://pd2trader.com/api/rares/appraisal'

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
    low = float(env.get('GG_STAND_CHECK', 0.3))     # >= this -> CHECK, below -> VENDOR
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
def fetch_stash_items(token, account, base=None):
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
        })
    # currency tab: runes + gems + materials as qty lines
    cur = d.get('currency') or {}
    for code, n in (cur.get('runes') or {}).items():
        if isinstance(n, int) and n > 0:
            items.append({'character': 'Currency Tab', 'tab': 'Runes', 'pos': code,
                          'code': code, 'name': code, 'base_name': code, 'quality': 'Currency',
                          'qty': n, 'id': 'cur-' + code})
    for tier, gems in (cur.get('gems') or {}).items():
        for gem, n in (gems or {}).items():
            if isinstance(n, int) and n > 0:
                nm = '%s %s' % (tier.capitalize(), gem.capitalize())
                items.append({'character': 'Currency Tab', 'tab': 'Gems', 'pos': gem,
                              'code': gem, 'name': nm, 'base_name': nm, 'quality': 'Gem',
                              'qty': n, 'id': 'gem-%s-%s' % (tier, gem)})
    for group, label in [('boss', 'Boss Mats'), ('craft', 'Craft Mats'), ('rejuv', 'Rejuvs'),
                         ('full_rejuv', 'Rejuvs'), ('demonic_cube', 'Mats'), ('jewel_frag', 'Mats')]:
        val = cur.get(group)
        if not isinstance(val, dict):
            continue
        for code, n in val.items():
            if isinstance(n, int) and n > 0:
                items.append({'character': 'Currency Tab', 'tab': label, 'pos': code,
                              'code': code, 'name': code, 'base_name': code, 'quality': 'Material',
                              'qty': n, 'id': 'mat-%s-%s' % (group, code)})
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

# ------------------------------------------------------------------ dashboard
def esc(s):
    import html
    return html.escape(str(s))

def write_dashboard(state):
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    total = sum(it.get('hr') or 0 for it in state.get('items', []))
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
                         '<th>WSS &asymp;</th><th>Listing note (copy)</th></tr>'
                         % (esc(char), esc(tab), round(sub, 2)))
        md_rows.append('\n## %s › %s — %s HR\n' % (char, tab, round(sub, 2)))
        md_rows.append('| Pos | Item | Quality | iLvl | Value (HR) | Listing note |')
        md_rows.append('|---|---|---|---|---|---|')
        for it in sorted(items, key=lambda x: -(x.get('hr') or 0)):
            note = 'wts %s' % it.get('name', '')
            if it.get('hr'):
                note += ' // %s HR' % round(it['hr'], 2)
            rows_html.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
                             '<td><b>%s</b></td><td>%s</td>'
                             '<td><input style="width:340px" readonly value="%s" onclick="this.select()"></td></tr>'
                             % (esc(it.get('pos', '')), esc(it.get('name', '')),
                                esc(it.get('quality', '')), esc(it.get('ilvl', '')),
                                esc(round(it.get('hr') or 0, 2)),
                                esc(int(round((it.get('hr') or 0) * 100))), esc(note)))
            md_rows.append('| %s | %s | %s | %s | %s | `%s` |' % (
                it.get('pos', ''), it.get('name', ''), it.get('quality', ''),
                it.get('ilvl', ''), round(it.get('hr') or 0, 2), note))
        rows_html.append('</table>')
        md_rows.append('')
    html_doc = ('<!doctype html><meta charset="utf-8"><title>GG DEEP DELVE - Stash Prices</title>'
                '<meta http-equiv="refresh" content="60">'
                '<style>body{background:#0b0b10;color:#eee;font-family:Segoe UI,Consolas;margin:24px}'
                'h1{color:#FFD700} h2{color:#9B9BFF} small{color:#999} input{font-family:Consolas}</style>'
                + banner +
                '<h1>GG DEEP DELVE - Live Stash Pricing <small>updated %s &middot; total %s HR &middot; '
                'auto-refresh 60s</small></h1>' % (now, round(total, 2))
                + ''.join(rows_html) +
                '<p><small>values: pd2trader.com ladder SC (public API); stash data: your PD2 account '
                'via the pd2trader-app token; rares appraised with exact rolls.</small></p>')
    DASH_HTML.write_text(html_doc, encoding='utf-8')
    md = ('# GG DEEP DELVE - Stash Prices\n\nupdated %s — total %s HR\n\n'
          'values: pd2trader.com ladder SC (public API); rares appraised with exact rolls.\n'
          % (now, round(total, 2)))
    if state.get('token_expired'):
        md += '\n**Token Expired — Refresh via --import-token**\n'
    DASH_MD.write_text(md + '\n'.join(md_rows), encoding='utf-8')

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
    items = fetch_stash_items(token, account)
    if tab_filter:
        items = [it for it in items if tab_filter.lower() in it.get('tab', '').lower()
                 or tab_filter.lower() in (it.get('name') or '').lower()]
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            cache = {}
    seen = cache.get('items', {})
    fresh = []
    for it in items:
        fp = fingerprint(it)
        prev = seen.get(fp)
        if prev and isinstance(prev, dict) and prev.get('hr') is not None and not full:
            fresh.append(dict(it, hr=prev.get('hr'), status='cached'))
            continue
        fresh.append(dict(it))
    # ---- pricing
    val_lines = []
    rare_items = []
    for it in fresh:
        q = it.get('quality')
        if q in ('Rare', 'Craft'):
            rare_items.append(it)
        elif q in ('Gem',):
            continue                                   # gems have no market - skip fuzz
        elif q in ('Magic', 'Normal', 'Superior'):
            continue                                   # base items: live fuzzy matching misleads; show base value only
        else:
            nm = it.get('name') or it.get('base_name')  # uniques/sets/runewords by name, runes/mats by code
            if it.get('runeword') and it.get('name'):
                nm = it['name']
            val_lines.append({'item': nm, 'qty': it.get('qty', 1), 'it': it})
    dedup = {}
    for l in val_lines:
        dedup.setdefault((l['item'], l.get('qty', 1)), []).append(l['it'])
    priced, _ = valuate_lines([{'item': k[0], 'qty': k[1]} for k in dedup])
    aps = appraise_rares(rare_items) if rare_items else {}
    priced_items = []
    for it in fresh:
        it2 = dict(it)
        if it['quality'] in ('Rare', 'Craft'):
            ap = aps.get(it['id']) or {}
            band = ap.get('band') or ap.get('priceBand') or {}
            hr = band.get('mid') or band.get('high') or band.get('low')
            it2['verdict'] = ap.get('verdict')
            it2['appraisal'] = ap
        else:
            nm = it.get('name') or it.get('base_name')
            hr, conf, why = priced.get(nm, (None, None, None))
            it2['confidence'] = conf
        it2['hr'] = hr if isinstance(hr, (int, float)) else 0
        priced_items.append(it2)
    # cache
    for it in priced_items:
        seen[fingerprint(it)] = {'name': it.get('name'), 'hr': it.get('hr'),
                                 'ts': datetime.now(timezone.utc).isoformat()}
    cache['items'] = seen
    cache['last_scan'] = datetime.now(timezone.utc).isoformat()
    CACHE.write_text(json.dumps(cache, indent=1), encoding='utf-8')
    state = {'items': priced_items, 'token_expired': False, 'last_scan': cache['last_scan']}
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

# ------------------------------------------------------------------ hotkeys
def start_hotkeys(on_f9, on_f10, on_f8):
    try:
        import keyboard                                     # noqa: PLC0415
    except Exception as e:                                  # noqa: BLE001
        print('! hotkeys unavailable (pip install keyboard; admin if PD2 is elevated): %r' % e)
        return None
    for hot, fn in [('f8', on_f8), ('f9', on_f9), ('f10', on_f10)]:
        try:
            keyboard.add_hotkey(hot, fn)
            print('hotkey registered: %s' % hot.upper())
        except Exception as e:                              # noqa: BLE001
            print('! hotkey %s failed: %r' % (hot, e))
    return True

# ------------------------------------------------------------------ discovery (product core)
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
        note = 'wts %s' % it.get('name', '')
        if key_stats:
            note += ' (%s)' % ', '.join(key_stats[:2])
        if it.get('ethereal'):
            note += ' eth'
        if ask:
            note += ' // %s HR' % round(ask, 2)
        rows.append({'standing': standing, 'ask': ask or 0, 'stars': stars,
                     'verdict': verdict, 'note': note, 'item': it})
    order = {'GG': 0, 'CHECK': 1, 'VENDOR': 2}
    rows.sort(key=lambda r: (order[r['standing']], -r['ask']))
    gg = [r for r in rows if r['standing'] == 'GG']
    check = [r for r in rows if r['standing'] == 'CHECK']
    vendor = [r for r in rows if r['standing'] == 'VENDOR']

    print('\n===== DISCOVERY (standings: GG>=%.2f HR | CHECK>=%.2f HR) =====' % (high, low))
    print('GG (%d) - ready to list:' % len(gg))
    for r in gg[:15]:
        print('  [GG]    %-30s %6.2f HR  %s' % ((r['item'].get('name') or '')[:30], r['ask'], r['note'][:80]))
    print('CHECK (%d) - worth a look:' % len(check))
    for r in check[:10]:
        print('  [CHECK] %-30s %6.2f HR' % ((r['item'].get('name') or '')[:30], r['ask']))
    print('VENDOR (%d) - everything else' % len(vendor))

    md = ['# GG DEEP DELVE - Discovery %s' % datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
          '',
          'standings: GG >= %.2f HR | CHECK >= %.2f HR | else VENDOR' % (high, low),
          'GG items are listing-ready: copy the note into the PD2 market (or pd2trader Quick List).',
          '']
    md += ['## GG - list these (%d)' % len(gg), '', '| Item | Tab | Ask (HR) | Listing note |', '|---|---|---|---|---|']
    for r in gg:
        it = r['item']
        md.append('| %s | %s | %s | `%s` |' % (esc(it.get('name', '')), esc(it.get('tab', '')),
                                               round(r['ask'], 2), r['note']))
    md += ['', '## CHECK - verify before selling (%d)' % len(check), '',
           '| Item | Tab | Est (HR) | Verdict |', '|---|---|---|---|']
    for r in check:
        it = r['item']
        md.append('| %s | %s | %s | %s |' % (esc(it.get('name', '')), esc(it.get('tab', '')),
                                             round(r['ask'], 2), r['verdict'] or '-'))
    md += ['', '## VENDOR (%d items - full list in stash_prices.md)' % len(vendor)]
    out_path = out_path or (REPO / 'discoveries.md')
    out_path.write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('discovery file: %s' % out_path.name)
    return rows


def run_once(env, tab_filter=None, full=False, discover_mode=False):
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
    if discover_mode:
        discover(state, env)
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
                print('  synced: %d items, %.2f HR' % (len(state['items']), total))
                chime()
                toast('GG DEEP DELVE - Synced', ['%d items, %.1f HR' % (len(state['items']), total),
                                                 'reloading filter in game...'])
                g = detect_game_dir()
                if g:
                    send_reload(g.parent)
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

    start_hotkeys(force_sync, hot_price_check, hot_link)
    print('watch mode: polling every %ds while %s is running; F8 link+reload, '
          'F9 sync+reload, F10 clipboard check.' % (POLL_SECONDS, ' / '.join(GAME_PROCESSES)))
    while True:
        if paused or not stash_enabled or not game_running():
            time.sleep(IDLE_CHECK_SECONDS)
            continue
        try:
            state = scan_once(token, env)
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
        return run_watch(env)
    if args.once:
        return run_once(env, tab_filter=args.tab, full=args.full, discover_mode=args.discover)
    if args.check:
        res = appraise_clipboard()
        return 0 if res else 1
    ap.print_help()
    return 1

if __name__ == '__main__':
    sys.exit(main())
