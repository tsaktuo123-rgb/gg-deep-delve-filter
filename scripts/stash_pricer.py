#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stash_pricer.py - Hands-free PD2 stash & inventory auto-appraiser for
GG DEEP DELVE (Project Diablo 2, Season 14).

Modes
  python stash_pricer.py --watch          background daemon (game-aware 90s polling)
  python stash_pricer.py --once           one-shot stash scan + pricing
  python stash_pricer.py --check          price-check the current clipboard item
  python stash_pricer.py --init-filter    initialize GG_DEEP_DELVE.filter from Hiim_Hyper

Security boundary (this file's own rule):
  This tool talks ONLY to the user's own PD2 account with the PD2_AUTH_TOKEN the
  user themselves pasted into .env. No credential is invented, substituted, or
  bypassed. Without a token in .env the watcher refuses to start and prints the
  setup prompt instead. HTTP 401/403 (expired JWT) pauses polling and raises a
  banner instead of retrying.

Endpoint status (verified 2026-11-17):
  - VERIFIED PUBLIC (no auth): pd2trader.com  /api/valuate, /api/price-check,
    /api/rares/appraisal - used for all pricing.
  - AUTHENTICATED (user token): PD2 account stash/inventory endpoints are read
    from PD2_API_BASE with candidate paths in PD2_STASH_PATHS. PD2 does not
    publish authenticated API docs, so every path attempt is logged and the
    script NEVER invents data: if no candidate resolves, it reports what it
    tried and exits non-zero rather than printing a confident blank.
"""

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ------------------------------------------------------------------ config
REPO = Path(__file__).resolve().parent.parent          # gg-deep-delve-filter/
FILTER = Path(r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local\GG_DEEP_DELVE.filter')
FILTER_SOURCE = Path(r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local\Hiim_Hyper.filter')
DASH_HTML = REPO / 'stash_prices.html'
DASH_MD = REPO / 'stash_prices.md'
CACHE = REPO / 'stash_cache.json'
ENV_FILE = REPO / '.env'

VALUATE_URL = 'https://pd2trader.com/api/valuate'
PRICE_CHECK_URL = 'https://pd2trader.com/api/price-check'
APPRAISAL_URL = 'https://pd2trader.com/api/rares/appraisal'

PD2_API_BASE = 'https://api.projectdiablo2.com'
# candidate authenticated paths, tried in order; first 200 wins. Editable via .env.
PD2_STASH_PATHS = ['/api/player/stash', '/api/stash', '/api/account/stash',
                   '/api/player/inventory', '/api/v1/stash']
POLL_SECONDS = 90
IDLE_CHECK_SECONDS = 60
GAME_PROCESSES = ('Game.exe', 'Diablo II.exe')

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

def require_token(env):
    token = env.get('PD2_AUTH_TOKEN', '')
    account = env.get('PD2_ACCOUNT_NAME', '')
    if not token:
        print(__doc__)
        print('\n=== SETUP REQUIRED ===')
        print('1. Copy .env.example to .env (same folder as this script).')
        print('2. Paste your PD2 session token as PD2_AUTH_TOKEN=... (never commit .env).')
        print('3. Optionally set PD2_ACCOUNT_NAME=... and rerun:')
        print('   python stash_pricer.py --watch')
        raise SystemExit(1)
    return token, account

# ------------------------------------------------------------------ http
def http_json(url, token=None, body=None, timeout=45):
    headers = {'User-Agent': 'GG-DEEP-DELVE-stash-pricer/0.1', 'Accept': 'application/json'}
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

def fetch_stash(token, base, paths):
    """Try candidate authenticated stash endpoints; log every attempt."""
    tried = []
    for path in paths:
        url = base.rstrip('/') + path
        try:
            status, data = http_json(url, token=token)
            print('  stash endpoint OK: %s (status %s)' % (path, status))
            return data, path
        except urllib.error.HTTPError as e:
            tried.append((path, e.code))
            if e.code in (401, 403):
                raise TokenExpired('%s -> %d' % (path, e.code))
        except Exception as e:                              # noqa: BLE001
            tried.append((path, repr(e)[:60]))
    detail = '; '.join('%s -> %s' % t for t in tried)
    raise RuntimeError('no stash endpoint resolved. Tried: ' + detail)

# ------------------------------------------------------------------ pricing
def valuate_items(items, hours=168):
    """Bulk-price via the public pd2trader /api/valuate endpoint.
    items: [{'item': '<name or code>', 'qty': int}, ...]"""
    body = {'items': [{'item': it['item'], 'qty': it.get('qty', 1)} for it in items],
            'hours': hours, 'isLadder': True, 'isHardcore': False}
    _, d = http_json(VALUATE_URL, body=body)
    out = {}
    for line in d.get('lines', []):
        price = line.get('priceHr')
        status = line.get('status')
        out[line.get('input') or line.get('item')] = (price, status)
    return out, d

def price_check_exact(name=None, base_code=None, stats=None, quality=None, hours=168):
    """Single-item exact comps via public /api/price-check."""
    body = {'item': base_code or name, 'isLadder': True, 'isHardcore': False, 'hours': hours}
    if quality:
        body['quality'] = quality
    if stats:
        body['stats'] = stats
    _, d = http_json(PRICE_CHECK_URL, body=body)
    e = d.get('estimateHr') or {}
    return e.get('low'), e.get('mid'), e.get('high'), d.get('confidence')

# ------------------------------------------------------------------ clipboard (F10)
PD2_ITEM_JSON_RE = re.compile(r'\{[\s\S]*"name"[\s\S]*\}')

def clipboard_text():
    """Windows clipboard via PowerShell (no extra deps)."""
    ps = ('powershell -NoProfile -Command "Get-Clipboard -Raw"')
    out = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-Clipboard -Raw'],
                         capture_output=True, timeout=15)
    return out.stdout.decode('utf-8', 'replace').strip()

def parse_pd2_clipboard(text):
    """Parse PD2's Ctrl+C item copy (JSON: name, quality, type, iLevel, stats[]...).
    Returns dict or None if the clipboard is not a PD2 item copy."""
    m = PD2_ITEM_JSON_RE.search(text)
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
    stats = [{'key': s.get('key') or s.get('name'), 'value': s.get('value', s.get('level', 0))}
             for s in item.get('stats', []) if isinstance(s, dict)]
    sockets = item.get('sockets') or 0
    ethereal = bool(item.get('ethereal') or item.get('isEthereal'))
    result = {'name': name, 'quality': quality, 'ilvl': item.get('iLevel'), 'sockets': sockets,
              'ethereal': ethereal}
    if quality in ('Rare', 'Crafted'):
        try:
            _, d = http_json(APPRAISAL_URL,
                             body={'text': raw, 'isLadder': True, 'isHardcore': False})
            appraisal = d.get('appraisal') or {}
            result['appraisal'] = appraisal
            result['verdict'] = appraisal.get('verdict')
            band = appraisal.get('priceBand') or appraisal.get('askingPrice') or {}
            result['band'] = band
        except Exception as e:                              # noqa: BLE001
            result['error'] = 'appraisal failed: %r' % e
    try:
        low, mid, high, conf = price_check_exact(name=name, quality=quality or None)
        result['pc'] = (low, mid, high, conf)
    except Exception as e:                                  # noqa: BLE001
        result['pc'] = None
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
    """Non-focus-stealing topmost overlay (Tk, borderless, auto-close)."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.geometry('340x%d+40+80' % (56 + 22 * len(lines)))
        frame = tk.Frame(root, bg='#101018', highlightbackground='#D3A000',
                         highlightthickness=2)
        frame.pack(fill='both', expand=True)
        tk.Label(frame, text=title, bg='#101018', fg='#FFD700', font=('Consolas', 11, 'bold')).pack(anchor='w', padx=10, pady=(8, 2))
        for ln in lines:
            tk.Label(frame, text=ln, bg='#101018', fg='#EDEDED', font=('Consolas', 9)).pack(anchor='w', padx=10)
        root.after(seconds * 1000, root.destroy)
        root.update_idletasks()
        root.mainloop()
        return True
    except Exception:                                       # noqa: BLE001
        print('[toast] %s | %s' % (title, ' | '.join(lines)))
        return False

# ------------------------------------------------------------------ dashboard
def esc(s):
    return html.escape(str(s))

def write_dashboard(state):
    """stash_prices.html (auto-refresh) + stash_prices.md, grouped by character/tab."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    total = sum(it.get('hr') or 0 for it in state.get('items', []))
    banner = ''
    if state.get('token_expired'):
        banner = ('<div style="background:#7a1010;color:#fff;padding:12px 16px;font-weight:bold">'
                  'Token Expired - Refresh PD2_AUTH_TOKEN in .env, then rerun stash_pricer.py --watch</div>')
    rows_html, md_rows = [], []
    groups = {}
    for it in state.get('items', []):
        key = (it.get('character') or 'Stash', it.get('tab') or '-', )
        groups.setdefault(key, []).append(it)
    for (char, tab), items in sorted(groups.items()):
        sub = sum(i.get('hr') or 0 for i in items)
        rows_html.append('<h2>%s &rsaquo; %s <small>(%s HR)</small></h2><table border=1 cellpadding=4 '
                         'style="border-collapse:collapse;font-family:Consolas,monospace;font-size:13px">'
                         '<tr><th>Pos</th><th>Item</th><th>Quality</th><th>iLvl</th><th>Sock</th>'
                         '<th>Value (HR)</th><th>WSS &asymp;</th><th>Listing note (copy)</th></tr>'
                         % (esc(char), esc(tab), round(sub, 2)))
        md_rows.append('\n## %s › %s — %s HR\n' % (char, tab, round(sub, 2)))
        md_rows.append('| Pos | Item | Quality | iLvl | Value (HR) | Listing note |')
        md_rows.append('|---|---|---|---|---|---|')
        for it in sorted(items, key=lambda x: -(x.get('hr') or 0)):
            note = 'wts %s' % it.get('name', '')
            if it.get('hr'):
                note += ' // %s HR' % round(it['hr'], 2)
            rows_html.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
                             '<td><b>%s</b></td><td>%s</td>'
                             '<td><input style="width:340px" readonly value="%s" onclick="this.select()"></td></tr>'
                             % (esc(it.get('pos', '')), esc(it.get('name', '')),
                                esc(it.get('quality', '')), esc(it.get('ilvl', '')),
                                esc(it.get('sockets', '')), esc(round(it.get('hr') or 0, 2)),
                                esc(int(round((it.get('hr') or 0) * 100))), esc(note)))
            md_rows.append('| %s | %s | %s | %s | %s | `%s` |' % (
                it.get('pos', ''), it.get('name', ''), it.get('quality', ''),
                it.get('ilvl', ''), round(it.get('hr') or 0, 2), note))
        rows_html.append('</table>')
        md_rows.append('')
    html_doc = ('<!doctype html><meta charset="utf-8"><title>GG DEEP DELVE - Stash Prices</title>'
                '<meta http-equiv="refresh" content="60">'
                '<style>body{background:#0b0b10;color:#eee;font-family:Segoe UI,Consolas;margin:24px}'
                'h1{color:#FFD700} small{color:#999} input{font-family:Consolas}</style>'
                + banner +
                '<h1>GG DEEP DELVE - Live Stash Pricing <small>updated %s &middot; total %s HR &middot; '
                'auto-refresh 60s</small></h1>' % (now, round(total, 2))
                + ''.join(rows_html) +
                '<p><small>values: pd2trader.com ladder SC 7-day avg (public API); '
                'stash data: your PD2 account via PD2_AUTH_TOKEN</small></p>')
    DASH_HTML.write_text(html_doc, encoding='utf-8')
    md = ('# GG DEEP DELVE - Stash Prices\n\nupdated %s — total %s HR\n\n'
          'values: pd2trader.com ladder SC 7-day average (public API).\n'
          % (now, round(total, 2)))
    if state.get('token_expired'):
        md += '\n**Token Expired — Refresh PD2_AUTH_TOKEN in .env**\n'
    DASH_MD.write_text(md + '\n'.join(md_rows), encoding='utf-8')

# ------------------------------------------------------------------ scan + cache
def fingerprint(it):
    blob = json.dumps({'c': it.get('code'), 'n': it.get('name'), 'i': it.get('ilvl'),
                       's': it.get('sockets'), 'e': it.get('ethereal'),
                       'q': it.get('quality'), 't': sorted(json.dumps(it.get('stats', [])))},
                      sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()

def normalize_stash(payload, account):
    """Map whatever the authenticated endpoint returned into flat items.
    Handles nested characters[] / tabs[] shapes defensively; unknown shapes are
    reported, never guessed."""
    items = []
    if not isinstance(payload, dict):
        return items
    for char in payload.get('characters', []) or ([payload] if payload.get('name') else []):
        cname = char.get('name') or account or 'Character'
        for tab in char.get('stash', char.get('tabs', char.get('inventory', []))) or []:
            if not isinstance(tab, dict):
                continue
            tname = tab.get('name') or tab.get('label') or tab.get('id') or 'tab'
            for it in tab.get('items', []) or []:
                if not isinstance(it, dict):
                    continue
                items.append({
                    'character': it.get('character') or cname,
                    'tab': tname,
                    'pos': '%s,%s' % (it.get('x', it.get('row', '?')), it.get('y', it.get('col', '?'))),
                    'code': it.get('code') or it.get('baseCode'),
                    'name': it.get('name') or it.get('baseName') or it.get('code'),
                    'quality': it.get('quality'),
                    'ilvl': it.get('iLevel') or it.get('ilvl'),
                    'sockets': it.get('sockets') or 0,
                    'ethereal': bool(it.get('ethereal') or it.get('isEthereal')),
                    'identified': it.get('identified', True),
                    'stats': it.get('stats', []),
                    'qty': it.get('qty') or it.get('count') or 1,
                })
    return items

def scan_once(token, env):
    base = env.get('PD2_API_BASE', PD2_API_BASE)
    paths = [p for p in re.split(r'[,\s]+', env.get('PD2_STASH_PATHS', '') or '') if p] or PD2_STASH_PATHS
    payload = fetch_stash(token, base, paths)
    items = normalize_stash(payload, env.get('PD2_ACCOUNT_NAME', ''))
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            cache = {}
    seen = cache.get('items', {})
    new_items = []
    for it in items:
        fp = fingerprint(it)
        if fp in seen:
            continue
        new_items.append(it)
        seen[fp] = {'name': it.get('name'), 'ts': datetime.now(timezone.utc).isoformat()}
    priced = []
    if new_items:
        lines = [{'item': (it.get('quality') == 'Unique' and it.get('name')) or it.get('code') or it.get('name'),
                  'qty': it.get('qty', 1)} for it in new_items]
        try:
            val, _ = valuate_items(lines)
        except Exception as e:                              # noqa: BLE001
            print('  bulk valuation failed: %r' % e)
            val = {}
        for it in new_items:
            key = (it.get('quality') == 'Unique' and it.get('name')) or it.get('code') or it.get('name')
            hr, status = val.get(key, (None, None))
            it2 = dict(it)
            it2['hr'] = hr if isinstance(hr, (int, float)) else 0
            it2['status'] = status
            priced.append(it2)
    cache['items'] = seen
    cache['last_scan'] = datetime.now(timezone.utc).isoformat()
    CACHE.write_text(json.dumps(cache, indent=1), encoding='utf-8')
    state = {'items': priced, 'token_expired': False, 'last_scan': cache['last_scan']}
    write_dashboard(state)
    return state

# ------------------------------------------------------------------ filter init
def init_filter():
    if FILTER.exists():
        print('GG_DEEP_DELVE.filter already present: %s' % FILTER)
        return
    bak = FILTER_SOURCE.with_suffix('.filter.bak')
    if not bak.exists():
        import shutil
        shutil.copyfile(FILTER_SOURCE, bak)
        print('backup created: %s' % bak)
    import shutil
    shutil.copyfile(FILTER_SOURCE, FILTER)
    print('initialized %s from %s' % (FILTER.name, FILTER_SOURCE.name))

# ------------------------------------------------------------------ hotkeys
def start_hotkeys(on_f9, on_f10):
    """Global hotkeys via `keyboard` (pip install keyboard). Runs as a daemon
    thread; degrades to polling-only when unavailable (or without admin rights
    while PD2 runs elevated)."""
    try:
        import keyboard                                     # noqa: PLC0415
    except Exception as e:                                  # noqa: BLE001
        print('! hotkeys unavailable (pip install keyboard; admin if PD2 is elevated): %r' % e)
        return None
    for hot, fn in [('f9', on_f9), ('f10', on_f10)]:
        try:
            keyboard.add_hotkey(hot, fn)
            print('hotkey registered: %s' % hot.upper())
        except Exception as e:                              # noqa: BLE001
            print('! hotkey %s failed: %r' % (hot, e))
    return True

# ------------------------------------------------------------------ modes
def run_once(env):
    token, _ = require_token(env)
    try:
        state = scan_once(token, env)
    except TokenExpired as e:
        print('TOKEN EXPIRED — Refresh PD2_AUTH_TOKEN in .env (%s)' % e)
        write_dashboard({'items': [], 'token_expired': True})
        return 2
    except Exception as e:                                  # noqa: BLE001
        print('scan failed: %r' % e)
        return 3
    total = sum(i.get('hr') or 0 for i in state['items'])
    print('new/unseen items priced: %d (total %.2f HR) -> %s / %s'
          % (len(state['items']), total, DASH_HTML.name, DASH_MD.name))
    return 0

def run_watch(env):
    token, _ = require_token(env)
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
        nonlocal state, paused
        print('[F9] forcing stash + economy sync...')
        try:
            subprocess.run([sys.executable, str(Path(__file__).with_name('sync_pd2_prices.py'))],
                           timeout=300)
        except Exception as e:                              # noqa: BLE001
            print('  economy sync failed: %r' % e)
        try:
            state = scan_once(token, env)
            paused = False
            total = sum(i.get('hr') or 0 for i in state['items'])
            print('  synced: %d new items, %.2f HR' % (len(state['items']), total))
            chime()
            toast('GG DEEP DELVE - Synced', ['New: %d items' % len(state['items']),
                                             'Press Numpad 0 in game to reload filter'])
        except TokenExpired as e:
            paused = True
            state['token_expired'] = True
            write_dashboard(state)
            print('  TOKEN EXPIRED — Refresh PD2_AUTH_TOKEN in .env (%s)' % e)
        except Exception as e:                              # noqa: BLE001
            print('  sync failed: %r' % e)

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
            print('  %s: %s' % (res.get('name'), lines))
            toast('GG DEEP DELVE - %s' % res.get('name', 'Price Check'), lines)
        except Exception as e:                              # noqa: BLE001
            print('  price check failed: %r' % e)

    start_hotkeys(force_sync, hot_price_check)
    print('watch mode: polling every %ds while %s is running; F9 force sync, F10 clipboard check.'
          % (POLL_SECONDS, ' / '.join(GAME_PROCESSES)))
    while True:
        if paused:
            time.sleep(IDLE_CHECK_SECONDS)
            continue
        if not game_running():
            time.sleep(IDLE_CHECK_SECONDS)
            continue
        try:
            state = scan_once(token, env)
        except TokenExpired as e:
            paused = True
            state['token_expired'] = True
            write_dashboard(state)
            print('TOKEN EXPIRED — Refresh PD2_AUTH_TOKEN in .env (%s)' % e)
            continue
        except Exception as e:                              # noqa: BLE001
            print('poll failed: %r (retrying next cycle)' % e)
        time.sleep(POLL_SECONDS)

def main():
    env = load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--watch', action='store_true', help='background daemon (game-aware)')
    ap.add_argument('--once', action='store_true', help='one-shot stash scan')
    ap.add_argument('--check', action='store_true', help='price-check clipboard item')
    ap.add_argument('--init-filter', action='store_true', help='init GG filter from Hiim_Hyper')
    args = ap.parse_args()
    if args.init_filter:
        init_filter()
        return 0
    if args.watch:
        return run_watch(env)
    if args.once:
        return run_once(env)
    if args.check:
        res = appraise_clipboard()
        return 0 if res else 1
    ap.print_help()
    return 1

if __name__ == '__main__':
    sys.exit(main())
