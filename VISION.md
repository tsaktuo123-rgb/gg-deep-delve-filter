# VISION — GG DEEP DELVE

> **Agents and contributors: read this file before your first write to this repo.**
> It outranks every assumption you would otherwise make about what "done" means.

## The one law

**Every change must make the player's session better.** GG DEEP DELVE is a
quality-of-life project: the filter, the scripts, the hotkeys, the dashboard — all of
it exists so the user spends more time playing and less time alt-tabbing, price
checking, guessing at values, or squinting at cluttered loot.

A change that is technically impressive but does not shorten the distance between
"a drop happened" and "I know what it's worth, I heard the sound, I know where to put
it" is a change this repo does not want.

## How to work in this mode

1. **Read the current production filter first.** The live file is
   `<PD2 install>\ProjectD2\filters\local\GG_DEEP_DELVE.filter`; the master copy is
   `filter/GG_DEEP_DELVE.filter`. Never propose from memory — read what exists.
2. **Ask the QoL question before every write:** "Does this make the player faster,
   richer, or less distracted in-game?" If the answer is no, stop.
3. **After reading the current production filter, find the next QoL improvement** —
   a feature, an add-on, or an adjustment to what is already built — and **file it
   into the backlog section below** (one table row, following the template). Even if
   you do not build it: a filed idea survives context windows; a noticed-but-unfiled
   one does not.
4. **Build on top of what you just proved.** Measure your change (run the script,
   lint the filter, verify the output) before claiming it.

## Standing rules (they protect the QoL)

- **Zero destructive edits** to the base filter: additive rules, threshold updates and
  alias value updates only. The original `Hiim_Hyper.filter` and its `.bak` are
  permanent references.
- **Zero false positives on the money sound.** Tier 3 (`> 1.0 HR` strictly) must never
  fire on something the live market would not pay 1 HR for.
- **Prices come from measured sources** (pd2trader public API), never from memory or
  invention. If you could not verify it, say so where you wrote it.
- **Backups before every write** to a filter file (`.bak` originals permanent,
  rolling `*.sync-bak` for updates).
- **No secrets, no personal paths in public files.** `.env` carries credentials;
  machine-specific layout goes in `PD2_FILTER_DIR`, never in docs.
- **The player's character is sacred.** No automation may kill, restart, or interfere
  with the game client without an explicit, opt-in command — a filter reload is
  always `Numpad 0` (or the hotkey that sends it), never a client restart.

## What exists (as of 2026-11-17)

| Capability | Where | Status |
|---|---|---|
| 3-tier market audio (live-priced, stack-aware, UM-anchored) | filter auto-tier engine | shipped |
| Post-ID roll & slam tooltips (uniques, facets, rare HR brackets) | filter appraisal block | shipped |
| S14 priority bases visible at all strictnesses | filter chest block | shipped |
| One-click economy sync (113+ aliases, `--dry-run`, backups) | `scripts/sync_pd2_prices.py` | shipped |
| Hands-free stash watcher, 90 s game-aware polling, diff cache | `scripts/stash_pricer.py --watch` | shipped (needs `PD2_AUTH_TOKEN` in `.env`) |
| F10 clipboard price check with overlay toast | watcher hotkeys | shipped |
| F9 sync + auto reload keystroke into the running client | watcher hotkeys | shipped |
| F8 detect running client → link filter → live reload | watcher hotkeys + `--detect` | shipped |
| Real money.mp3 as Tier 3 (loose-file override / WinMPQ fallback) | `scripts/install_money_sound.py` | shipped, **awaiting the user's in-game `.playsound 16` test** |
| Client restart | `--restart-client` | available, **explicit-only by design** |

## QoL backlog — file proposals here (newest at the bottom)

Template: `| date | idea | why it helps (QoL) | status |`

| date | idea | why it helps (QoL) | status |
|---|---|---|---|
| 2026-11-17 | F8 hotkey: detect running client, ensure GG filter is installed in that install, synthesize the reload keystroke | zero manual path work when the user plays from any install | shipped (this session) |
| 2026-11-17 | F9 also sends the reload keystroke after a successful sync | "sync → hear it → back to playing" with zero keys beyond F9 | shipped (this session) |
| 2026-11-17 | Per-class Hellfire Torch unid → ID'd tooltip rows (10 torch variants priced per class) | torches are the most traded unid item; ID'd roll appraisal saves trade-site trips | proposed |
| 2026-11-17 | ID'd unique-ring tooltips via stat signatures (SoJ mana signature, Raven Frost CBF+DEX, Wisp absorb) | rings are the highest-traffic ID'd market today; every SoJ/Raven hover shows live value | proposed |
| 2026-11-17 | Charm appraisal rows (skiller grand charms by tab; small-charm lifer/res tiers) priced from rare-guide ladders | charms are the second-biggest unid market after torches | proposed |
| 2026-11-17 | Dashboard: sort/filter rows by value and quality, and mark items as listed/sold locally | the dashboard becomes the user's loot ledger, not just a price list | proposed |
| 2026-11-17 | `--check` (F10) falls back to text-format item parse when the clipboard is the older PD2 format | F10 keeps working across client versions | proposed |
| 2026-11-17 | Season migration helper: one command carries all GG value aliases + tier engine into the next season's fresh Hiim filter | each new ladder restarts the economy; the engine should survive it in one command | proposed |
| 2026-11-17 | Sound-pack variants: a small alias block letting the user pick Tier-3 among 3 candidate slots (hostile/questdone/cube) without reading MPQ docs | one-line personalization for friends | proposed |
| 2026-11-17 | Loose-file sfx override (`data\global\sfx\...` next to Game.exe) | would have been zero-MPQ-risk money sound | **tested, dead** — PD2 reads sfx only from the MPQ chain; removed |
| 2026-11-17 | Automated MPQ swap via Ladik MPQEditor CLI targeting the vanilla `d2sfx.mpq` (PD2 never rewrites vanilla archives) | plug-and-play real money sound that survives game updates | **crashed the game** — MPQEditor's default compression wrote a codec D2 cannot read ("file data is corrupt"); archive restored from backup |
| 2026-11-17 | StormLib-based swap with explicit ZLIB compression (ctypes, x64 DLL auto-downloaded) | the same plug-and-play money sound with a codec D2 1.13 provably reads; byte-verified on a copy | shipped, copy-verified (awaiting user apply with game closed) |
