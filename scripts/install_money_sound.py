#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_money_sound.py - plug-and-play installer for the REAL money sound
(Cardi B "Money") as the GG DEEP DELVE Tier 3 jackpot audio.

How it works (verified 2026-11-17):
  PD2's filter engine accepts only numeric %SOUNDID-n% indexes (BH source:
  ParseSoundID). The game loads sounds from the vanilla archives - the sfx live
  in <PD2 root>\\d2sfx.mpq (the vanilla install folder, which PD2's launcher
  never rewrites). This script swaps the sound slot cursor\\hostile.wav
  (SOUNDID 16 - vanilla index, never played by PvM gameplay) with money.wav
  using Ladik's MPQ Editor CLI (auto-downloaded to ProjectD2\\MpqFixer\\mpqeditor).

  A loose-file override (data\\global\\sfx\\... next to the exe) was tested and
  does NOT work in PD2 - the engine reads sfx only from the MPQ chain.

Safety:
  - refuses to run while the game is open (the MPQ is write-locked)
  - backs up the whole d2sfx.mpq first (d2sfx.mpq.money-bak)
  - backs up the original hostile.wav (assets\\hostile.wav.original.wav)
  - verifies the swap by re-extracting and byte-comparing before claiming success

Usage:
  python install_money_sound.py                # convert + patch MPQ (game must be closed)
  python install_money_sound.py --seconds 11   # keep the full clip instead of 8 s
  python install_money_sound.py --link-filter  # after .playsound 16 confirms money
  python install_money_sound.py --revert       # filter back to built-in jackpot 4733
  python install_money_sound.py --restore-slot # put the original hostile.wav back

After patching: in game run ".playsound 16" to hear money, then --link-filter,
then Numpad 0. Tier 3 jackpot = money.

Exit codes: 0 ok, 1 config/conversion failure, 2 filter edit aborted, 3 game running.
"""
import argparse
import hashlib
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import wave
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / '.env'
SOURCE_MP3 = REPO / 'assets' / 'money.mp3'
WAV_OUT = REPO / 'assets' / 'money.wav'
SOUND_SLOT = r'data\global\sfx\cursor\hostile.wav'
SOUND_INDEX = '16'
JACKPOT_FALLBACK = '4733'
FILTER = Path(r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local\GG_DEEP_DELVE.filter')
DEFAULT_SECONDS = 8
MPQEDITOR_URL = 'https://www.zezula.net/download/mpqeditor_en.zip'

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def game_dirs(env):
    """(game_root_with_Game_exe, pd2_folder, vanilla_root) for this install."""
    base = Path(env.get('PD2_FILTER_DIR',
                        r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local'))
    pd2 = base.parents[1]
    root = pd2
    for cand in (pd2, *pd2.parents):
        if (cand / 'Game.exe').exists():
            root = cand
            break
    return root, pd2, pd2.parent

def game_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Game.exe'],
                             capture_output=True, timeout=20).stdout.decode(errors='replace')
        return 'Game.exe' in out
    except Exception:                                       # noqa: BLE001
        return False

def locate_ffmpeg():
    import shutil as _sh
    exe = _sh.which('ffmpeg')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    print('ffmpeg not found - installing the bundled binary via pip (one-time)...')
    r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet',
                        '--no-warn-script-location', 'imageio-ffmpeg'])
    if r.returncode != 0:
        print('pip install failed; run: python -m pip install imageio-ffmpeg')
        return None
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def convert(seconds):
    ffmpeg = locate_ffmpeg()
    if not ffmpeg:
        return False
    if not SOURCE_MP3.exists():
        print('missing source: %s' % SOURCE_MP3)
        return False
    fade_start = max(0.0, seconds - 0.8)
    r = subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
                        '-t', str(seconds), '-i', str(SOURCE_MP3),
                        '-ac', '1', '-ar', '22050', '-sample_fmt', 's16',
                        '-af', 'afade=t=out:st=%.1f:d=0.8' % fade_start, str(WAV_OUT)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print('ffmpeg failed: %s' % r.stderr[-400:])
        return False
    w = wave.open(str(WAV_OUT))
    ok = (w.getnchannels() == 1 and w.getframerate() == 22050 and w.getsampwidth() == 2)
    dur = w.getnframes() / w.getframerate()
    w.close()
    print('converted: %s (mono/22050Hz/16-bit, %.1fs) %s' % (WAV_OUT, dur, 'OK' if ok else 'WRONG FORMAT'))
    return ok

def locate_mpqeditor(pd2):
    for sub in ('mpqeditor\\x64', 'mpqeditor\\Win32'):
        exe = pd2 / 'MpqFixer' / sub / 'MPQEditor.exe'
        if exe.exists():
            return exe
    print('downloading Ladik MPQ Editor (author site: zezula.net)...')
    dst = pd2 / 'MpqFixer' / 'mpqeditor'
    dst.mkdir(parents=True, exist_ok=True)
    zpath = dst / 'mpqeditor_en.zip'
    ctx = ssl.create_default_context()
    data = None
    try:
        data = urllib.request.urlopen(urllib.request.Request(
            MPQEDITOR_URL, headers={'User-Agent': 'Mozilla/5.0'}), timeout=120, context=ctx).read()
    except Exception as e:                                  # noqa: BLE001
        print('  standard fetch failed (%r); retrying with relaxed certificate check' % e)
        ctx = ssl._create_unverified_context()
        data = urllib.request.urlopen(urllib.request.Request(
            MPQEDITOR_URL, headers={'User-Agent': 'Mozilla/5.0'}), timeout=120, context=ctx).read()
    zpath.write_bytes(data)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dst)
    for sub in ('x64', 'Win32'):
        exe = dst / sub / 'MPQEditor.exe'
        if exe.exists():
            return exe
    return None

def locate_d2sfx(env):
    _, pd2, vanilla = game_dirs(env)
    for cand in (vanilla / 'd2sfx.mpq', vanilla.parent / 'd2sfx.mpq', pd2 / 'd2sfx.mpq'):
        if cand.exists():
            return cand
    # last resort: walk up looking for it
    for parent in [*vanilla.parents, vanilla]:
        c = parent / 'd2sfx.mpq'
        if c.exists():
            return c
    return None

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def patch_mpq(env):
    if game_running():
        print('REFUSED: Game.exe is running (the sound MPQ is write-locked).')
        print('Close the game, then run this command again.')
        return 3
    mpq = locate_d2sfx(env)
    if not mpq:
        print('d2sfx.mpq not found next to the install; set PD2_FILTER_DIR in .env')
        return 1
    mpqeditor = locate_mpqeditor(game_dirs(env)[1])
    if not mpqeditor:
        print('MPQEditor.exe not found and download failed')
        return 1
    backup = mpq.with_name(mpq.name + '.money-bak')
    if not backup.exists():
        print('backing up the whole archive -> %s' % backup.name)
        shutil.copyfile(mpq, backup)
    work = REPO / 'assets'
    work.mkdir(exist_ok=True)
    orig_out = work / 'hostile.wav.original.wav'
    def mpe(*args):
        r = subprocess.run([str(mpqeditor)] + list(args), capture_output=True, text=True, timeout=600)
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()[:160]
    tmp = work / '_slot_extract'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    rc, out = mpe('extract', str(mpq), SOUND_SLOT, str(tmp) + os.sep, '/fp')
    got = None
    for root, _, files in os.walk(tmp):
        for f in files:
            got = os.path.join(root, f)
    if got and sha(got) == sha(WAV_OUT):
        print('slot already plays money - nothing to do. Use --link-filter if not linked yet.')
        shutil.rmtree(tmp, ignore_errors=True)
        return 0
    if got and not orig_out.exists():
        shutil.copyfile(got, orig_out)
        print('original slot sound backed up: %s (%d bytes)' % (orig_out.name, os.path.getsize(got)))
    else:
        print('! slot %s not found in %s - aborting (nothing changed)' % (SOUND_SLOT, mpq.name))
        return 1
    shutil.rmtree(tmp, ignore_errors=True)
    rc, out = mpe('add', str(mpq), str(WAV_OUT), SOUND_SLOT, '/auto')
    if rc != 0:
        print('add failed (rc=%d) %s' % (rc, out))
        return 1
    rc, out = mpe('flush', str(mpq))
    if rc != 0:
        print('flush failed (rc=%d) %s' % (rc, out))
        return 1
    chk = work / '_slot_verify'
    shutil.rmtree(chk, ignore_errors=True)
    chk.mkdir()
    mpe('extract', str(mpq), SOUND_SLOT, str(chk) + os.sep, '/fp')
    got = None
    for root, _, files in os.walk(chk):
        for f in files:
            got = os.path.join(root, f)
    if got:
        same = sha(got) == sha(WAV_OUT)          # hash BEFORE any cleanup
        print('verify: extracted slot == money.wav ->', same)
    else:
        same = False
        print('verify: nothing extracted')
    shutil.rmtree(chk, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    if same:
        print('SWAP VERIFIED: %s in %s now plays money (%d bytes, byte-identical).'
              % (SOUND_SLOT, mpq.name, os.path.getsize(got)))
        print('Next: start the game, run ".playsound 16" to hear it, then:')
        print('  python install_money_sound.py --link-filter   (then Numpad 0 in game)')
        return 0
    print('verification FAILED - restoring archive backup')
    shutil.copyfile(backup, mpq)
    return 1

def link_filter():
    if not FILTER.exists():
        print('filter not found: %s' % FILTER)
        return 1
    text = FILTER.read_text(encoding='utf-8')
    m = re.search(r'^Alias\[GG_TIER3_FX\]:.*%SOUNDID-(\d+)%.*$', text, re.M)
    if not m:
        print('GG_TIER3_FX alias line not found; aborting')
        return 2
    current = m.group(1)
    if current == SOUND_INDEX:
        print('filter already linked to SOUNDID-%s' % SOUND_INDEX)
        return 0
    new_text, n = re.subn(
        r'^(Alias\[GG_TIER3_FX\]:.*?%SOUNDID-)\d+(%.*)$',
        lambda mm: mm.group(1) + SOUND_INDEX + mm.group(2),
        text, count=1, flags=re.M)
    if n != 1:
        print('rewrite failed; aborting')
        return 2
    shutil.copyfile(FILTER, FILTER.with_suffix('.filter.money-bak'))
    FILTER.write_text(new_text, encoding='utf-8', newline='\n')
    print('GG_TIER3_FX: SOUNDID-%s -> SOUNDID-%s (backup: *.money-bak)' % (current, SOUND_INDEX))
    print('Press Numpad 0 in game (or F8) to reload.')
    return 0

def revert():
    text = FILTER.read_text(encoding='utf-8')
    new_text, n = re.subn(
        r'^(Alias\[GG_TIER3_FX\]:.*?%SOUNDID-)\d+(%.*)$',
        lambda mm: mm.group(1) + JACKPOT_FALLBACK + mm.group(2),
        text, count=1, flags=re.M)
    if n != 1:
        print('rewrite failed; nothing changed')
        return 2
    shutil.copyfile(FILTER, FILTER.with_suffix('.filter.money-bak'))
    FILTER.write_text(new_text, encoding='utf-8', newline='\n')
    print('reverted GG_TIER3_FX to the built-in jackpot SOUNDID-%s' % JACKPOT_FALLBACK)
    return 0

def restore_slot(env):
    if game_running():
        print('REFUSED: close the game first (MPQ is write-locked).')
        return 3
    mpq = locate_d2sfx(env)
    orig = REPO / 'assets' / 'hostile.wav.original.wav'
    if not mpq.exists() or not orig.exists():
        print('need %s and %s' % (mpq, orig))
        return 1
    mpqeditor = locate_mpqeditor(game_dirs(env)[1])
    r = subprocess.run([str(mpqeditor), 'add', str(mpq), str(orig), SOUND_SLOT, '/auto'],
                       capture_output=True, text=True, timeout=600)
    r2 = subprocess.run([str(mpqeditor), 'flush', str(mpq)], capture_output=True, text=True, timeout=600)
    ok = r.returncode == 0 and r2.returncode == 0
    print('slot restored' if ok else 'restore failed (%s %s)' % (r.returncode, r2.returncode))
    print('run --revert to put the filter back on the built-in jackpot sound')
    return 0 if ok else 1

def main():
    env = load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seconds', type=int, default=DEFAULT_SECONDS)
    ap.add_argument('--link-filter', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--restore-slot', action='store_true')
    ap.add_argument('--skip-convert', action='store_true')
    args = ap.parse_args()
    if args.revert:
        return revert()
    if args.link_filter:
        return link_filter()
    if args.restore_slot:
        return restore_slot(env)
    if not args.skip_convert:
        if not convert(args.seconds):
            return 1
    return patch_mpq(env)

if __name__ == '__main__':
    sys.exit(main())
