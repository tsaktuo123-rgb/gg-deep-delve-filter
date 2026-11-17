#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_money_sound.py - plug-and-play installer for the REAL money sound
(Cardi B "Money") as the GG DEEP DELVE Tier 3 jackpot audio.

PD2's filter engine accepts only numeric %SOUNDID-n% indexes (verified in the
BH source: ParseSoundID reads SOUNDID keys only), so the money clip must live
inside a game sound slot. This script automates every step around the engine
restriction:

  1. converts money.mp3 -> money.wav in the exact format D2 requires
     (mono, 22050 Hz, 16-bit PCM; default first 8 s + fade-out)
  2. places it as a LOOSE file override:
     <game root>\\data\\global\\sfx\\cursor\\hostile.wav
     (classic D2 loose-file loading - zero MPQ risk; test in game with
     ".playsound 16"; if the game loads it, you are done)
  3. prints the exact WinMPQ fallback steps if the loose override is ignored
     (WinMPQ ships with PD2 in ProjectD2\\MpqFixer)
  4. --link-filter then switches GG_TIER3_FX from %SOUNDID-4733% to
     %SOUNDID-16% (the hostile.wav slot) in the live GG filter, with backup

Why hostile.wav (SOUNDID 16): vanilla index (stable; PD2 appends its custom
sounds at 4714+), and PvM gameplay never plays it - the only trigger is
toggling PvP-hostile. Alternate slot: cursor\\questdone.wav (SOUNDID 14).

Usage:
  python install_money_sound.py              # convert + place loose file + print test
  python install_money_sound.py --link-filter # after .playsound 16 confirms money
  python install_money_sound.py --revert      # alias back to %SOUNDID-4733%
  python install_money_sound.py --seconds 11  # keep the full clip
Exit codes: 0 ok, 1 config/conversion failure, 2 filter edit aborted.
"""
import argparse
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / '.env'
SOURCE_MP3 = REPO / 'assets' / 'money.mp3'
WAV_OUT = REPO / 'assets' / 'money.wav'
SOUND_SLOT = 'cursor\\hostile.wav'
SOUND_INDEX = '16'
JACKPOT_FALLBACK = '4733'
FILTER = Path(r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local\GG_DEEP_DELVE.filter')
DEFAULT_SECONDS = 8

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def game_root(env):
    base = env.get('PD2_FILTER_DIR',
                   r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local')
    p = Path(base)
    # <PD2 install>\Diablo II\ProjectD2\filters\local -> game root holds Game.exe
    for parent in [p.parent, *p.parents]:
        if (parent / 'Game.exe').exists():
            return parent
    return None

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
    cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
           '-t', str(seconds), '-i', str(SOURCE_MP3),
           '-ac', '1', '-ar', '22050', '-sample_fmt', 's16',
           '-af', 'afade=t=out:st=%.1f:d=0.8' % fade_start, str(WAV_OUT)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print('ffmpeg failed: %s' % r.stderr[-400:])
        return False
    w = wave.open(str(WAV_OUT))
    ok = (w.getnchannels() == 1 and w.getframerate() == 22050 and w.getsampwidth() == 2)
    dur = w.getnframes() / w.getframerate()
    w.close()
    print('converted: %s (mono/22050Hz/16-bit, %.1fs) %s' % (WAV_OUT, dur, 'OK' if ok else 'WRONG FORMAT'))
    return ok

def place_loose(env):
    root = game_root(env)
    if not root:
        print('could not locate the game root (Game.exe). Set PD2_FILTER_DIR in .env.')
        return False
    target_dir = root / 'data' / 'global' / 'sfx' / 'cursor'
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / 'hostile.wav'
    if not target.exists():
        print('original wav not present as loose file (expected - this is the override)')
    shutil.copyfile(WAV_OUT, target)
    print('loose override placed: %s' % target)
    return True

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
    bak = FILTER.with_suffix('.filter.money-bak')
    shutil.copyfile(FILTER, bak)
    FILTER.write_text(new_text, encoding='utf-8', newline='\n')
    print('GG_TIER3_FX: SOUNDID-%s -> SOUNDID-%s (backup: %s)' % (current, SOUND_INDEX, bak.name))
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
    print('reverted GG_TIER3_FX to SOUNDID-%s' % JACKPOT_FALLBACK)
    return 0

def print_winmpq_steps(env):
    root = game_root(env)
    if not root:
        root = Path(env.get('PD2_FILTER_DIR',
            r'E:\D2\Diablo II\Diablo II\ProjectD2\filters\local')).parents[1]
    print('\nIf ".playsound 16" did NOT play money, the game ignores loose files here.')
    print('Use the MPQ editor already in your install (ProjectD2\\MpqFixer\\WinMPQ.exe):')
    print('  1. Close the game.')
    print('  2. Run WinMPQ.exe (from MpqFixer) as Administrator.')
    print('  3. File > Open... %s' % (root / 'pd2data.mpq'))
    print('  4. Expand data\\global\\sfx\\cursor\\ - RIGHT-CLICK hostile.wav >')
    print('     Extract... (save the original as a backup somewhere safe).')
    print('  5. RIGHT-CLICK the extracted original > Delete (from list).')
    print('  6. Operations > Add File(s)... choose %s' % WAV_OUT)
    print('  7. Rename the added file to: data\\global\\sfx\\cursor\\hostile.wav')
    print('     (right-click > Rename, keep the full path).')
    print('  8. File > Save (or it saves on close - answer Yes).')
    print('  9. Start the game, run ".playsound 16" to confirm money, then:')
    print('     python install_money_sound.py --link-filter')
    print('     (then Numpad 0 in game to reload the filter)')
    print(' NOTE: if a game update re-verifies the MPQs, re-run steps 2-8 once.')

def main():
    env = load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seconds', type=int, default=DEFAULT_SECONDS)
    ap.add_argument('--link-filter', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--skip-convert', action='store_true')
    args = ap.parse_args()
    if args.revert:
        return revert()
    if args.link_filter:
        return link_filter()
    if not args.skip_convert:
        if not convert(args.seconds):
            return 1
    if not place_loose(env):
        return 1
    print('\nTEST (10 seconds): start the game, type in chat:  .playsound 16')
    print('  - money plays?  -> run:  python install_money_sound.py --link-filter')
    print('                    then press Numpad 0 in game. Done - Tier 3 jackpot = money.')
    print('  - blip/nothing? -> run the script again with --skip-convert to see the')
    print('                    WinMPQ fallback steps below.')
    print_winmpq_steps(env)
    return 0

if __name__ == '__main__':
    sys.exit(main())
