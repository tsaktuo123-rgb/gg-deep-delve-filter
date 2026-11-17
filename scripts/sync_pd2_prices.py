#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sync_PD2_Filter - GG DEEP DELVE price updater for Project Diablo 2, Season 14.

One-click updater: pulls live Season 14 ladder (softcore) 7-day average prices
from the public pd2trader.com API and rewrites the value aliases inside
GG DEEP DELVE / Hiim_Hyper loot filters, so tier audio and tooltip prices
shift with the live economy.

What it updates (per filter file):
  - <RUNE>_RUNE_VALUE, <RUNE>_WSS_VALUE, <RUNE>_STACK_HR, <RUNE>_STACK_WSS
  - WSS/Tainted/Puzzlebox/Puzzlepiece/Demonic Cube/Catalyst aliases (HR, WSS, stacks)
  - Essence, DClone/Rathma/Lucion material, and Uber-key (PK1-3) value aliases
  - *_MARKET_RANGE text aliases (min-max of avg vs 7-day average)
  - CURRENCY_TIMESTAMP and the cube-header "Last updated" date

What it does NOT touch:
  - Every other rule in the filter (leveling, map immunity, vendor tags, bases)
  - The original backup Hiim_Hyper.filter.bak (never overwritten)

Tier semantics used by the filter's auto-tier engine (unchanged here):
  Tier 3 (money, SOUNDID-4733):  value*QTY > 1.0 HR   (strictly)
  Tier 2 (SOUNDID-4719):         0.5 <= value*QTY < 1.05  (0.5 and 1.0 inclusive)
  Tier 1 (SOUNDID-4715):         UM_RUNE_VALUE < value*QTY < 0.5
  silent:                        at or below the live Um-rune floor

Usage:  python sync_pd2_prices.py [--dry-run] [--hours 168]
Exit codes: 0 = updated, 1 = fetch/parse failure, 2 = filter mismatch (nothing written).

Verified against the live API on 2026-11-17:
  GET https://pd2trader.com/item-prices/average/all?isLadder=true&isHardcore=false&hours=168
  (public, anonymous, no API key required; responses carry movingAverage7Days etc.)
"""

import json
import re
import sys
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = 'https://pd2trader.com/item-prices/average/all'
FILTERS = [
    Path(r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local\GG_DEEP_DELVE.filter'),
    Path(r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local\Hiim_Hyper.filter'),
]
# alias name (as used inside the filter) -> pd2trader baseCode
ALIAS_TO_CODE = {
    'LEM_RUNE_VALUE': 'r20', 'PUL_RUNE_VALUE': 'r21', 'UM_RUNE_VALUE': 'r22',
    'MAL_RUNE_VALUE': 'r23', 'IST_RUNE_VALUE': 'r24', 'GUL_RUNE_VALUE': 'r25',
    'VEX_RUNE_VALUE': 'r26', 'OHM_RUNE_VALUE': 'r27', 'LO_RUNE_VALUE': 'r28',
    'SUR_RUNE_VALUE': 'r29', 'BER_RUNE_VALUE': 'r30', 'JAH_RUNE_VALUE': 'r31',
    'CHAM_RUNE_VALUE': 'r32', 'ZOD_RUNE_VALUE': 'r33',
    'TAINTED_WORLDSTONE_SHARD_VALUE': 'cwss',
    'LARZUK_PUZZLEBOX_VALUE': 'lbox',
    'LARZUK_PUZZLEPIECE_VALUE': 'lpp',
    'DEMONIC_CUBE_VALUE': 'imrn',
    'CATALYST_SHARD_VALUE': 'iwss',
    'FESTERING_ESS_VALUE': 'fed', 'BURNING_ESS_VALUE': 'bet',
    'CHARGED_ESS_VALUE': 'ceh', 'TWISTED_ESS_VALUE': 'tes',
    'BLACK_SOULSTONE_VALUE': 'dcho', 'PRIME_EVIL_SOUL_VALUE': 'dcso',
    'PURE_DEMONIC_ESSENCE_VALUE': 'dcbl', 'HELLFIRE_ASHES_VALUE': 'cm2f',
    'SPLINTER_OF_THE_VOID_VALUE': 'rtmv', 'TRANG_OUL_JAWBONE_VALUE': 'rtmo',
    'FLESH_OF_MALIC_VALUE': 'lucd', 'TALISMAN_OF_TRANSGRESSION_VALUE': 'lucc',
    'DEMONIC_INSIGNIA_VALUE': 'lucb',
    'PK1_VALUE': 'pk1', 'PK2_VALUE': 'pk2', 'PK3_VALUE': 'pk3',
}
# appraisal aliases refreshed from pd2trader's rare guide + price-check percentiles
GUIDE_URL = 'https://pd2trader.com/api/rares/guide'
PRICE_CHECK_URL = 'https://pd2trader.com/api/price-check'
GUIDE_LADDERS = {   # filter family -> rare-guide slot
    'HR_RIN': 'ring', 'HR_AMU': 'amul', 'HR_JEW': 'jewl', 'HR_HELM': 'helm',
    'HR_CIRC': 'circ', 'HR_GLOVES': 'glov', 'HR_BOOTS': 'boot', 'HR_BELT': 'belt',
    'HR_CHEST': 'tors', 'HR_SHIELD': 'shield',
}
UNIQUE_EST = {      # filter stem -> pd2trader item code
    'UNI_UAP': 'harlequin_crest', 'UNI_CI3': 'griffon_s_eye',
    'UNI_MARA': 'mara_s_kaleidoscope', 'UNI_UMC': 'verdungo_s_hearty_cord',
    'UNI_XTB': 'war_traveler', 'UNI_XHB': 'gore_rider', 'UNI_UIT': 'stormshield',
    'UNI_CM3': 'gheed_s_fortune',
}
UNIQUE_SLAM = ['UNI_UAP', 'UNI_CI3', 'UNI_MARA']   # stems whose GG-slam alias also refreshes
# wss is untracked by the market (falls back to the API's fixed 0.01 HR table)
FIXED_VALUES = {'WSS_ITEM_HR_VALUE': 0.01}
RUNES = ['LEM', 'PUL', 'UM', 'MAL', 'IST', 'GUL', 'VEX',
         'OHM', 'LO', 'SUR', 'BER', 'JAH', 'CHAM', 'ZOD']
WSS_FAMILY = ['TAINTED_WORLDSTONE_SHARD', 'LARZUK_PUZZLEBOX', 'LARZUK_PUZZLEPIECE',
              'DEMONIC_CUBE', 'CATALYST_SHARD']
MIN_SAMPLES = 5   # below this the API's data is too thin; previous value is kept


def fetch_prices(hours):
    url = '%s?isLadder=true&isHardcore=false&hours=%d' % (API_URL, hours)
    req = urllib.request.Request(url, headers={'User-Agent': 'GG-DEEP-DELVE-sync/1.0'})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.load(resp)
    items = data.get('data') if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError('unexpected API payload shape')
    by_code = {}
    for it in items:
        by_code[it['baseCode']] = it
    return by_code, len(items)


def grid5(v):
    """Round to the 0.05 HR grid the tier conditions are built around."""
    return max(0.05, round(v * 20) / 20.0)


def fmt(v):
    s = ('%.2f' % v).rstrip('0').rstrip('.')
    return s if s else '0'


def pick_price(it):
    """7-day moving average first, then median, then plain average."""
    for key in ('movingAverage7Days', 'medianPrice', 'averagePrice'):
        v = it.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v), key
    return None, None


def tier_of(v, um):
    if v > 1.0:
        return 3
    if 0.45 < v < 1.05:
        return 2
    if um * 0.8 < v < 0.5:      # um*0.8 == ">= um" for 0.05-grid values (matches filter condition)
        return 1
    return 0


def refresh_appraisal_aliases(lines, hours):
    """Refresh post-ID appraisal aliases from pd2trader's rare guide and
    price-check percentiles. Returns True if at least one alias updated.
    Failures here are non-fatal: previous tooltip values are kept."""
    import urllib.error
    updated = False

    def get(url, body=None):
        req = urllib.request.Request(
            url, headers={'User-Agent': 'GG-DEEP-DELVE-sync/1.0',
                          'Content-Type': 'application/json'},
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    try:
        guide = get(GUIDE_URL)
        for fam, slot in GUIDE_LADDERS.items():
            lad = guide['slots'][slot]['ladder']['softcore']
            q = {e['percentile']: e['hr'] for e in lad}
            for suffix, pctl in (('P75', 0.75), ('P90', 0.9), ('P99', 0.99)):
                v = q.get(pctl)
                if isinstance(v, (int, float)) and v > 0:
                    lines['Alias[%s_%s]' % (fam, suffix)] = fmt(float(v))
                    updated = True
        print('rare-guide ladders refreshed (%s)' % guide.get('generatedAt', 'n/a'))
    except Exception as e:                    # noqa: BLE001
        print('  rare guide fetch failed: %r' % e)

    for stem, code in UNIQUE_EST.items():
        try:
            d = get(PRICE_CHECK_URL, {'item': code, 'quality': 'Unique',
                                      'isLadder': True, 'isHardcore': False,
                                      'hours': hours})
            e = d.get('estimateHr') or {}
            lo, mid, hi = e.get('low'), e.get('mid'), e.get('high')
            if all(isinstance(x, (int, float)) for x in (lo, mid, hi)):
                lines['Alias[%s_LOW]' % stem] = fmt(float(lo))
                lines['Alias[%s_MID]' % stem] = fmt(float(mid))
                lines['Alias[%s_HIGH]' % stem] = fmt(float(hi))
                updated = True
                print('  %s (%s): %.2f / %.2f / %.2f HR (%s, n=%s)' % (
                    stem, code, lo, mid, hi, d.get('confidence'), d.get('sampleSize')))
            else:
                print('  %s (%s): no estimate returned, kept previous' % (stem, code))
        except Exception as e:                # noqa: BLE001
            print('  %s (%s) price-check failed: %r' % (stem, code, e))
    for stem in UNIQUE_SLAM:
        code = UNIQUE_EST[stem]
        try:
            d = get(PRICE_CHECK_URL, {'item': code, 'quality': 'Unique', 'corrupted': True,
                                      'isLadder': True, 'isHardcore': False, 'hours': hours})
            hi = (d.get('estimateHr') or {}).get('high')
            if isinstance(hi, (int, float)) and hi > 0:
                lines['Alias[%s_CORR]' % stem] = fmt(float(hi))
                updated = True
        except Exception as e:                # noqa: BLE001
            print('  %s corrupted-pass failed: %r' % (stem, e))
    return updated


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    hours = 168
    if '--hours' in args:
        hours = int(args[args.index('--hours') + 1])

    print('fetching live S14 prices (ladder softcore, last %dh)...' % hours)
    try:
        by_code, tracked = fetch_prices(hours)
    except Exception as e:                    # noqa: BLE001 - report and stop, never half-write
        print('FETCH FAILED: %r' % e)
        print('filters left untouched; try again later (the API caches for up to 24h).')
        return 1
    print('tracked items with data: %d' % tracked)

    um_code_val, _ = pick_price(by_code.get('r22', {})) or (None, None)
    values = {}          # alias -> new HR value
    changed_report = []
    for alias, code in ALIAS_TO_CODE.items():
        it = by_code.get(code)
        price, source = (pick_price(it) if it else (None, None))
        samples = (it or {}).get('sampleCount', 0)
        if price is None:
            print('  ! no price for %-4s (%s) - keeping current filter value' % (code, alias))
            continue
        if samples < MIN_SAMPLES:
            print('  ! thin market for %-4s (%d samples) - keeping current value' % (code, samples))
            continue
        values[alias] = grid5(price)
        changed_report.append((alias, code, price, source, samples))

    um = values.get('UM_RUNE_VALUE')
    if um is None:
        print('! Um rune price unavailable - tier anchoring would go stale; nothing written.')
        return 1

    # ---- build the alias rewrite table (text lines) ----------------------
    lines = {}
    for alias, v in values.items():
        lines['Alias[%s]' % alias] = fmt(v)
        if alias.endswith('_RUNE_VALUE'):
            stem = alias[:-11]                       # strip _RUNE_VALUE
            w = int(round(v * 100))
            lines['Alias[%s_WSS_VALUE]' % stem] = str(w)
            lines['Alias[%s_STACK_HR]' % stem] = '(QTY*%s)' % fmt(v)
            lines['Alias[%s_STACK_WSS]' % stem] = '(QTY*%d)' % w
    for stem in WSS_FAMILY:
        alias = stem + '_VALUE'
        if alias in values:
            v = values[alias]
            w = int(round(v * 100))
            lines['Alias[%s_WSS_VALUE]' % stem] = str(w)
            lines['Alias[%s_STACK_HR]' % stem] = '(QTY*%s)' % fmt(v)
            lines['Alias[%s_STACK_WSS]' % stem] = '(QTY*%d)' % w
    lines['Alias[WSS_ITEM_HR_VALUE]'] = fmt(FIXED_VALUES['WSS_ITEM_HR_VALUE'])
    for alias, v in values.items():
        if alias.endswith('_RUNE_VALUE'):
            base = alias[:-11]                       # LEM_RUNE_VALUE -> LEM_MARKET_RANGE
        else:
            base = alias[:-6]                        # strip _VALUE
        if not (alias.endswith('_RUNE_VALUE') or base in WSS_FAMILY):
            continue                                 # range aliases exist only for runes + WSS family
        it = by_code[ALIAS_TO_CODE[alias]]
        lo = min(it.get('averagePrice') or 9e9, it.get('movingAverage7Days') or 9e9)
        hi = max(it.get('averagePrice') or 0, it.get('movingAverage7Days') or 0)
        lines['Alias[%s_MARKET_RANGE]' % base] = '%s - %s HR' % (fmt(grid5(lo)), fmt(grid5(hi)))

    stamp = datetime.now(timezone.utc).strftime('%b %d %Y')
    lines['Alias[CURRENCY_TIMESTAMP]'] = '%WHITE%HR Values: ' + stamp + ' - S14 ladder SC 7-day avg (pd2trader.com)'
    header_date = 'Last updated %s' % stamp

    # ---- appraisal aliases: rare-guide ladders + unique price-check percentiles
    refreshed = refresh_appraisal_aliases(lines, hours)
    if not refreshed:
        print('! appraisal alias refresh unavailable - previous tooltip values kept')

    # ---- apply to each filter -------------------------------------------
    ok = True
    for path in FILTERS:
        if not path.exists():
            print('MISSING: %s' % path)
            ok = False
            continue
        text = path.read_text(encoding='utf-8')
        # appraisal aliases (HR_*/UNI_* tooltips) are GG-DEEP-DELVE-only; other
        # filters just skip them. Core economy aliases must exist everywhere.
        appraisal = [k for k in lines if k.startswith('Alias[HR_') or k.startswith('Alias[UNI_')]
        core = [k for k in lines if k not in appraisal]
        missing_core = [k for k in core
                        if re.search(r'^Alias\[%s\][ \t]*:' % re.escape(k[6:-1]), text, re.M) is None]
        if missing_core:
            print('ABORT for %s: aliases not found: %s' % (path.name, ', '.join(missing_core)))
            ok = False
            continue
        file_lines = {k: v for k, v in lines.items()
                      if k in core or re.search(r'^Alias\[%s\][ \t]*:' % re.escape(k[6:-1]), text, re.M)}
        skipped = len(lines) - len(file_lines)
        new_text = text
        for key, val in file_lines.items():
            pattern = r'(^Alias\[%s\][ \t]*:)[^\r\n]*$' % re.escape(key[6:-1])
            new_text, n = re.subn(pattern, lambda m: m.group(1) + ' ' + val, new_text,
                                  count=1, flags=re.M)
            if n != 1:
                print('ABORT for %s: could not rewrite %s' % (path.name, key))
                ok = False
        # header date
        new_text = re.sub(r'Last updated [A-Z][a-z]{2} \d{1,2} \d{4}', header_date, new_text, count=1)
        if new_text == text:
            print('%s: already up to date' % path.name)
            continue
        if dry:
            print('%s: DRY RUN - %d alias lines would change' % (path.name, len(file_lines)))
            continue
        bak = path.with_suffix(path.suffix + '.sync-bak')
        shutil.copyfile(path, bak)
        path.write_text(new_text, encoding='utf-8', newline='\n')
        skipped_note = (' (%d appraisal aliases not present here, skipped)' % skipped) if skipped else ''
        print('%s: updated (%d alias lines)%s, previous copy -> %s' % (
            path.name, len(file_lines), skipped_note, bak.name))
    if not ok:
        return 2

    # ---- transparency report --------------------------------------------
    print('\n%-34s %-5s %8s  %-7s %s' % ('item', 'code', 'HR', 'src', 'tier (single / stacks)'))
    for alias, code, price, source, samples in sorted(changed_report, key=lambda x: -x[2]):
        v = values[alias]
        t = tier_of(v, um)
        t3 = t2 = '-'
        if t != 3 and v > 0:
            t3 = 'QTY>%d' % (int(1.0 / v) + (0 if 1.0 / v % 1 else 0)) if v <= 1 else '-'
            t2 = 'QTY>%d' % (int(0.45 / v)) if v <= 0.45 else '-'
        label = {3: 'T3 MONEY', 2: 'T2', 1: 'T1', 0: 'silent'}[t]
        print('%-34s %-5s %8s  %-7s %s  (T3 at %s, T2 at %s)' %
              (alias, code, fmt(v), (source or '')[:3] + '/%d' % samples, label, t3, t2))
    print('\nTier 1 lower cutoff is anchored to the live Um rune value (%s HR).' % fmt(um))
    print('Note: PD2 supports only %SOUNDID-n% ids from sounds.txt (no custom file paths);')
    print('Tier 3 plays the built-in jackpot sound 4733. money.mp3 needs an MPQ replacement')
    print('(16-bit PCM wav) and ffmpeg was not found on this machine - see session report.')
    if dry:
        print('DRY RUN - no files were modified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
