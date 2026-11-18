#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_money_sound.py - plug-and-play installer for the REAL money sound
(Cardi B "Money") as the GG DEEP DELVE Tier 3 jackpot audio.

Verified facts (2026-11-17):
  - PD2's filter engine accepts only numeric %SOUNDID-n% indexes (BH source:
    ParseSoundID); a loose-file sfx override was tested and is ignored by PD2.
  - The game's sound effects live in the vanilla <PD2 root>\\d2sfx.mpq, which
    PD2's launcher never rewrites (the swap survives game updates).
  - Slot: cursor\\hostile.wav = SOUNDID 16 (vanilla index, PvM gameplay never
    plays it). Alternate: cursor\\questdone.wav = SOUNDID 14.
  - MPQEditor's default compression (`/auto`) wrote a codec D2 cannot read — the
    game crashed with "file data is corrupt". This script uses **StormLib with
    explicit ZLIB** (the codec vanilla 1.13 archives use — D2 reads it natively)
    and verifies the swap byte-for-byte after flushing.

Safety:
  - refuses to run while the game is open (the MPQ is write-locked)
  - backs up the whole d2sfx.mpq (d2sfx.mpq.money-bak) + the original sound
  - verifies the swap byte-for-byte after flushing, restores on failure

Usage:
  python install_money_sound.py                # convert + swap (game closed)
  python install_money_sound.py --seconds 11   # keep the full clip
  python install_money_sound.py --link-filter  # after .playsound 16 confirms money
  python install_money_sound.py --revert       # filter back to built-in jackpot 4733
  python install_money_sound.py --restore-slot # original hostile.wav back into the MPQ

Exit codes: 0 ok, 1 config/conversion failure, 2 filter edit aborted, 3 game running.
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.request
import wave
import zipfile
from ctypes import wintypes
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
STORMLIB_RELEASE = 'https://api.github.com/repos/StormLib/StormLib/releases/latest'

MPQ_FILE_REPLACEEXISTING = 0x80000000
MPQ_COMPRESSION_ZLIB = 2

# ------------------------------------------------------------------ env / paths
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

def locate_d2sfx(env):
    _, pd2, vanilla = game_dirs(env)
    for cand in (vanilla / 'd2sfx.mpq', vanilla.parent / 'd2sfx.mpq', pd2 / 'd2sfx.mpq'):
        if cand.exists():
            return cand
    for parent in [*vanilla.parents, vanilla]:
        c = parent / 'd2sfx.mpq'
        if c.exists():
            return c
    return None

# ------------------------------------------------------------------ audio
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

# ------------------------------------------------------------------ StormLib
def locate_stormlib(pd2):
    exe = pd2 / 'MpqFixer' / 'stormlib' / 'StormLib.dll'
    if exe.exists():
        return exe
    print('downloading StormLib x64 (github.com/StormLib/StormLib)...')
    dst = exe.parent
    dst.mkdir(parents=True, exist_ok=True)
    rel = json.load(urllib.request.urlopen(urllib.request.Request(
        STORMLIB_RELEASE, headers={'User-Agent': 'x'}), timeout=60))
    asset = next((a for a in rel['assets'] if 'amd64_RUD' in a['name']
                  or ('amd64' in a['name'] and 'RUD' in a['name'])), None)
    if not asset:
        print('  no amd64 DLL asset in %s' % rel['tag_name'])
        return None
    data = urllib.request.urlopen(urllib.request.Request(
        asset['browser_download_url'], headers={'User-Agent': 'x'}), timeout=180).read()
    zpath = dst / 'stormlib.zip'
    zpath.write_bytes(data)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dst)
    if exe.exists():
        return exe
    return None

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def storm_swap(mpq, verify_only=False):
    """Swap the slot with money.wav via StormLib (zlib) and verify byte-for-byte."""
    dll = locate_stormlib(game_dirs(load_env())[1])
    if not dll:
        return 1
    storm = ctypes.WinDLL(str(dll))
    storm.SFileOpenArchive.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
    storm.SFileOpenFileEx.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.HANDLE)]
    storm.SFileReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    storm.SFileGetFileSize.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    storm.SFileAddFileEx.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    storm.SFileAddFileEx.restype = wintypes.BOOL
    for n in ('SFileOpenArchive', 'SFileOpenFileEx', 'SFileReadFile', 'SFileAddFileEx',
              'SFileFlushArchive', 'SFileCloseFile', 'SFileCloseArchive'):
        getattr(storm, n).restype = wintypes.BOOL
    k32 = ctypes.WinDLL('kernel32')

    def open_arc():
        h = wintypes.HANDLE()
        if not storm.SFileOpenArchive(str(mpq), 0, 0, ctypes.byref(h)):
            print('  open archive FAILED err=%d' % k32.GetLastError())
            return None
        return h

    def read_slot(h):
        fh = wintypes.HANDLE()
        if not storm.SFileOpenFileEx(h, SOUND_SLOT, 0, ctypes.byref(fh)):
            return None
        hi = wintypes.DWORD()
        size = storm.SFileGetFileSize(fh, ctypes.byref(hi))
        buf = ctypes.create_string_buffer(size + 1)
        rd = wintypes.DWORD()
        if not storm.SFileReadFile(fh, buf, size, ctypes.byref(rd), None):
            storm.SFileCloseFile(fh)
            return None
        storm.SFileCloseFile(fh)
        return buf.raw[:rd.value]

    if verify_only:
        h = open_arc()
        if not h:
            return 1
        data = read_slot(h)
        storm.SFileCloseArchive(h)
        if data is None:
            print('  slot unreadable')
            return 1
        same = hashlib.sha256(data).hexdigest() == sha(WAV_OUT)
        print('  slot == money.wav ->', same)
        return 0 if same else 1

    backup = mpq.with_name(mpq.name + '.money-bak')
    if not backup.exists():
        print('backing up the whole archive -> %s' % backup.name)
        shutil.copyfile(mpq, backup)
    work = REPO / 'assets'
    work.mkdir(exist_ok=True)
    orig_out = work / 'hostile.wav.original.wav'

    h = open_arc()
    if not h:
        return 1
    data = read_slot(h)
    if data is not None:
        if hashlib.sha256(data).hexdigest() == sha(WAV_OUT):
            print('slot already plays money - nothing to do (use --link-filter if needed).')
            storm.SFileCloseArchive(h)
            return 0
        if not orig_out.exists():
            orig_out.write_bytes(data)
            print('original slot sound backed up: %s (%d bytes)' % (orig_out.name, len(data)))
    else:
        print('! slot %s unreadable - aborting (nothing changed)' % SOUND_SLOT)
        storm.SFileCloseArchive(h)
        return 1
    if not storm.SFileAddFileEx(h, str(WAV_OUT), SOUND_SLOT,
                                MPQ_FILE_REPLACEEXISTING, MPQ_COMPRESSION_ZLIB, 0):
        print('  add FAILED err=%d - restoring backup' % k32.GetLastError())
        storm.SFileCloseArchive(h)
        shutil.copyfile(backup, mpq)
        return 1
    if not storm.SFileFlushArchive(h):
        print('  flush FAILED err=%d - restoring backup' % k32.GetLastError())
        storm.SFileCloseArchive(h)
        shutil.copyfile(backup, mpq)
        return 1
    storm.SFileCloseArchive(h)

    h = open_arc()
    if not h:
        return 1
    data = read_slot(h)
    storm.SFileCloseArchive(h)
    if data is not None and hashlib.sha256(data).hexdigest() == sha(WAV_OUT):
        print('SWAP VERIFIED: %s now plays money (%d bytes, byte-identical, zlib).' % (SOUND_SLOT, len(data)))
        print('Next: start the game, ".playsound 16" to hear it, then:')
        print('  python install_money_sound.py --link-filter   (then Numpad 0 in game)')
        return 0
    print('verification FAILED - restoring archive backup')
    shutil.copyfile(backup, mpq)
    return 1

def restore_slot(env):
    if game_running():
        print('REFUSED: close the game first (MPQ is write-locked).')
        return 3
    mpq = locate_d2sfx(env)
    orig = REPO / 'assets' / 'hostile.wav.original.wav'
    if not mpq.exists() or not orig.exists():
        print('need %s and %s' % (mpq, orig))
        return 1
    dll = locate_stormlib(game_dirs(env)[1])
    storm = ctypes.WinDLL(str(dll))
    storm.SFileOpenArchive.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
    storm.SFileAddFileEx.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    storm.SFileAddFileEx.restype = wintypes.BOOL
    storm.SFileFlushArchive.argtypes = [wintypes.HANDLE]
    storm.SFileFlushArchive.restype = wintypes.BOOL
    storm.SFileCloseArchive.argtypes = [wintypes.HANDLE]
    storm.SFileCloseArchive.restype = wintypes.BOOL
    h = wintypes.HANDLE()
    if not storm.SFileOpenArchive(str(mpq), 0, 0, ctypes.byref(h)):
        print('open failed err=%d' % ctypes.GetLastError())
        return 1
    ok = storm.SFileAddFileEx(h, str(orig), SOUND_SLOT, MPQ_FILE_REPLACEEXISTING,
                              MPQ_COMPRESSION_ZLIB, 0)
    storm.SFileFlushArchive(h)
    storm.SFileCloseArchive(h)
    print('original slot restored (zlib)' if ok else 'restore FAILED')
    print('run --revert to put the filter back on the built-in jackpot sound')
    return 0 if ok else 1

# ------------------------------------------------------------------ filter link
def link_filter():
    if not FILTER.exists():
        print('filter not found: %s' % FILTER)
        return 1
    text = FILTER.read_text(encoding='cp1252')
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
    FILTER.write_text(new_text, encoding='cp1252', newline='\n')
    loot = FILTER.parent.parent / 'loot.filter'
    if loot.exists() and 'GG DEEP DELVE price engine' in loot.read_text(encoding='utf-8', errors='replace'):
        shutil.copyfile(FILTER, loot)
        print('loot.filter synced too (Numpad 0 will load it).')
    print('GG_TIER3_FX: SOUNDID-%s -> SOUNDID-%s (backups: *.money-bak)' % (current, SOUND_INDEX))
    print('Press Numpad 0 in game (or F8) to reload.')
    return 0

def revert():
    text = FILTER.read_text(encoding='cp1252')
    new_text, n = re.subn(
        r'^(Alias\[GG_TIER3_FX\]:.*?%SOUNDID-)\d+(%.*)$',
        lambda mm: mm.group(1) + JACKPOT_FALLBACK + mm.group(2),
        text, count=1, flags=re.M)
    if n != 1:
        print('rewrite failed; nothing changed')
        return 2
    shutil.copyfile(FILTER, FILTER.with_suffix('.filter.money-bak'))
    FILTER.write_text(new_text, encoding='cp1252', newline='\n')
    loot = FILTER.parent.parent / 'loot.filter'
    if loot.exists() and 'GG DEEP DELVE price engine' in loot.read_text(encoding='utf-8', errors='replace'):
        shutil.copyfile(FILTER, loot)
    print('reverted GG_TIER3_FX to the built-in jackpot SOUNDID-%s' % JACKPOT_FALLBACK)
    return 0

# ------------------------------------------------------------------ main
def main():
    env = load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seconds', type=int, default=DEFAULT_SECONDS)
    ap.add_argument('--link-filter', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--restore-slot', action='store_true')
    ap.add_argument('--skip-convert', action='store_true')
    ap.add_argument('--verify', action='store_true', help='only re-verify the slot')
    args = ap.parse_args()
    if args.revert:
        return revert()
    if args.link_filter:
        return link_filter()
    if args.restore_slot:
        return restore_slot(env)
    if args.verify:
        if game_running():
            print('verification works any time (read-only), game open is fine.')
        return storm_swap(locate_d2sfx(env), verify_only=True)
    if not args.skip_convert:
        if not convert(args.seconds):
            return 1
    if game_running():
        print('REFUSED: Game.exe is running (the sound MPQ is write-locked).')
        print('Close the game, then run this command again.')
        return 3
    return storm_swap(locate_d2sfx(env))

if __name__ == '__main__':
    sys.exit(main())
