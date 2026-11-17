# GG DEEP DELVE — Project Diablo 2 Season 14 Loot Filter & Price Engine

A metadata-driven **price-check engine** built on the HiimFilter (Hyper) Season 14 filter.
Every drop's ground label and hover tooltip reflects live Season 14 market value standards:
3-tier drop audio, pre-ID metadata appraisals on drop, post-ID roll/slam appraisals on
identify, and a hands-free stash/inventory watcher that keeps a live pricing dashboard.

- Live economy source: [pd2trader.com](https://pd2trader.com) public API (ladder, softcore, 7-day average)
- Season: **S14 "Alliance"** (verified against the official patch notes and live API on 2026-11-17)
- Base filter: Hiim_Hyper.filter, Season 14 build 247 — every original leveling rule,
  map-immunity block, vendor-gold tag and item rule is preserved; all changes are additive.

---

## 1. Quick Start & Installation

### 1.1 Filter placement

1. Copy `filter/GG_DEEP_DELVE.filter` to
   `E:\D2\Diablo II\Diablo II\ProjectD2\filters\local\GG_DEEP_DELVE.filter`
2. Open the **PD2 Launcher** → *Item Filter Profiles* → under **Local**, select
   **GG_DEEP_DELVE** → Play.
3. In game: **PD2 Options → Loot Filter → Enable Loot Filter**, choose a
   **Filter Level** (strictness, see section 4).
4. Reload the filter live any time with **Numpad 0** (default PD2 hotkey).

Local filters are **never auto-updated or overwritten by the launcher** (verified against
the official PD2 wiki), so your customization is safe there. The original
`Hiim_Hyper.filter` is preserved untouched next to a pristine `Hiim_Hyper.filter.bak`.

### 1.2 The `money.mp3` jackpot sound — the honest truth

PD2's BH filter engine supports **only `%SOUNDID-n%` indexes from the game's
`sounds.txt`** — custom file paths (a Path of Diablo feature) are **not supported in
Season 14** (verified against wiki.projectdiablo2.com/wiki/Item_Filtering). Therefore:

- Tier 3 (jackpot) plays **`%SOUNDID-4733%`** — the built-in jackpot chime
  (the same sound the original filter reserves for Vial of Lightsong / Lilith's Mirror).
- If you want the literal `money.mp3`, you need an MPQ replacement: convert it to a
  **16-bit PCM 22 kHz mono `.wav`**, then replace that sound's WAV inside an MPQ
  archive with an MPQ editor. `ffmpeg` was **not available** on this machine
  (verified), so `assets/money.mp3` is stored here ready for a future conversion:
  `ffmpeg -i money.mp3 -acodec pcm_s16le -ar 22050 -ac 1 money.wav`
- You can audition any sound ID in game with `.playsound 4733`.

### 1.3 Python tooling

```bat
cd C:\Users\tsakt\Desktop\Projects\gg-deep-delve-filter
python -m pip install -r requirements.txt
copy .env.example .env      then edit .env and paste PD2_AUTH_TOKEN=...
```

Register the hands-free watcher (runs at logon, sleeps while the game is closed):

```bat
scripts\Install_Auto_Watcher.bat
```

Manual runs: `scripts\Price_My_Stash.bat --watch` (watcher) or `--once` (one scan),
`scripts\Price_My_Stash.bat --check` (clipboard price check), and on the Desktop
`Sync_PD2_Filter.bat` refreshes all economy prices + audio thresholds on demand.

---

## 2. Feature & Appraisal Architecture

### 2.1 The 3-tier market audio system

Tier thresholds are **alias-driven** (`*_RUNE_VALUE` etc.), so `Sync_PD2_Filter.bat`
shifts the whole hierarchy when the economy moves. Tier 1's lower cutoff is anchored
dynamically to the **live Um rune price** (`UM_RUNE_VALUE`) — not a static decimal.

| Tier | Sound | Visual | Qualifies when |
|---|---|---|---|
| **Tier 1** | `%SOUNDID-4715%` | `%MAP-62%` dot | value·QTY `> UM_RUNE_VALUE` and `< 0.5 HR` |
| **Tier 2** | `%SOUNDID-4719%` | `%BORDER-4A%%MAP-4A%` | `0.5 HR` ≤ value·QTY `≤ 1.0 HR` (inclusive at both ends), **plus** all variable/unid items whose floor ≤ 1.0 HR but upside > 1.0 (unid Rainbow Facets, unid rings/amulets with high ALVL, 3-star/4-star unid uniques) |
| **Tier 3 (money)** | `%SOUNDID-4733%` | `%BORDER-9B%%MAP-7D%` | value·QTY **strictly > 1.0 HR** — zero false positives |

**Stack math (`QTY>1`):** every tier condition is written as
`$f(<VALUE_ALIAS>*QTY > threshold)`, so a stack's combined value is evaluated, not the
unit floor. Live examples on 2026-11-17: 3× Um = 0.45 HR → Tier 1; 8× Um = 1.2 HR →
**money**; Ohm at exactly 1.00 HR → Tier 2; Lo at 1.15 HR → **money**.

Items below the live Um floor (Lem 0.05, Pul 0.05, wss 0.01…) are **silent** — the
legacy `4715` sounds were stripped from them (zero false positives).

### 2.2 Pre-ID vs Post-ID tooltips

- **Pre-ID (on drop):** every rune, WSS-family material, essence, boss mat, uber key and
  stackable shows `Market Range` + `WSS` + `HR Estimated Value` (per unit **and** per
  stack) from its base code + `ETH` + `SOCK` + `QTY` metadata. Unidentified uniques,
  sets and high-value bases show callout visuals and, for 3/4-star drops, the filter's
  star system with the tier-2-family sound; boss-chase uniques (Cage of the Unsullied,
  Aidan's Scar class, S14 new uniques) play the **jackpot** on drop (their ID'd medians
  are 25–196 HR; see session report for the measured basis).
- **Post-ID (on identify):**
  - Uniques/sets: conditional rules for key variable stats (ED%, -Enemy Res,
    +Skills, sockets, ethereal, `STAT360` corruption / `STAT206` desecration IDs)
    display `Low/Avg Roll Est`, `High/Near-Perf Roll Est` and `GG Slam Est` — measured
    from pd2trader price-check 25th/50th/75th percentiles (refreshed by the sync script).
  - Rainbow Facets: 5/5 rolls per element are detected by `STAT329-336` and play the
    jackpot (5/5 facets measured 2.5–4.2 HR); 4/5 ≈ 0.95 HR.
  - Rares/crafts/charms/jewels/jewelry: the filter's `PTSRING_*` / `PTSAMU_*` /
    `PTSJEW_*` / `PTSSLOT_*` point system now displays concrete HR brackets on hover:
    orange (3–5 pts) → `Est: <p75> HR (Top 25%)`, purple (>5 pts) →
    `Est: <p90>–<p99> HR (Top 10%)`, and a new **≥8-pt JACKPOT** rule →
    `Est: <p99>+ HR (Top 1 PERCENT)` with the jackpot sound. Brackets come from
    pd2trader's per-slot S14 asking-price ladders (12,000–23,000 listings per slot).
- **Season 14 priority bases:** Eth + non-eth elite chests (0/3/4 sockets, DEF>750),
  magic/rare elite chests ILVL>84 and the S14 runeword bases
  (**Madness** 4os weapons = Sur+Mal+Vex+Shael, **Finesse** 3os armor = Nef+Ral+Dol,
  **Deception** 3os helms = Fal+Amn+Hel, **Jealousy** 3os Amazon throwing = Lo+Gul+Thul)
  are visible at every strictness, with slam-target tooltips. An identified/slammed eth
  elite chest with `+2 All Skills` (`STAT360=50/27`, `ALLSK>1`) or 4 sockets on a
  top-half base plays the **money** sound.

### 2.3 The automation loop

- **`sync_pd2_prices.py`** pulls live prices (ladder SC, 7-day average), rewrites ~113
  value aliases in `GG_DEEP_DELVE.filter` (and the WSS-family/stack aliases), refreshes
  the rare-guide ladder brackets and unique roll estimates, and rewrites
  `CURRENCY_TIMESTAMP` + the cube header date. Refuses to write on any mismatch;
  `--dry-run` previews.
- **`stash_pricer.py --watch`** polls your authenticated stash/inventory every 90 s
  **only while `Game.exe`/`Diablo II.exe` is running** (zero overhead when closed),
  hashes each item into `stash_cache.json` (gitignored) so only **new or newly
  identified** items trigger API queries, prices them against the public pd2trader
  endpoints and updates `stash_prices.html` (auto-refresh 60 s) + `stash_prices.md`,
  grouped by character and stash tab with grid positions and one-click copyable
  listing notes. HTTP 401/403 pauses polling and shows a red
  **"Token Expired — Refresh PD2_AUTH_TOKEN in .env"** banner in the dashboard,
  console and README section 6 — it never spams the API.
- **Filter fallback:** if `GG_DEEP_DELVE.filter` is missing on E:, run
  `python scripts/stash_pricer.py --init-filter` — it backs up `Hiim_Hyper.filter` to
  `Hiim_Hyper.filter.bak` (only if missing) and re-initializes from it.

---

## 3. Files

| File | Purpose |
|---|---|
| `filter/GG_DEEP_DELVE.filter` | master filter (synced to `E:\...\filters\local\`) |
| `scripts/sync_pd2_prices.py` | live economy + audio threshold updater (also on Desktop) |
| `scripts/stash_pricer.py` | hands-free `--watch` stash & inventory appraiser |
| `scripts/Install_Auto_Watcher.bat` | registers the logon scheduled task |
| `scripts/Price_My_Stash.bat` | manual one-click runner (`--watch` / `--once` / `--check`) |
| `assets/money.mp3` | jackpot audio source (for future MPQ replacement) |
| `Desktop: Sync_PD2_Filter.bat` | one-click economy sync |
| `Desktop: sync_pd2_prices.py` | the sync engine the .bat runs |

---

## 4. Complete Strictness Reference (`FILTLVL` 0–11)

Level names as defined by the filter (in-game dropdown order):
`0 Filter Off/Campaign · 1 Campaign · 2 Campaign-Strict · 3 Base · 4 Semi-Strict ·
5 Strict · 6 Stricter · 7 Extremely Strict · 8 Extremely Strict+ · 9 High roller ·
10 3 Min Mapper · 11 No Items Out of Town`

| Lvl | Name | Verified behaviour in this filter |
|---|---|---|
| 0 | Filter Off | Plain names only (`ItemDisplay[FILTLVL=0]: %NAME%{%NAME%}`); no tier audio (sounds live in rules at 1+); warnings shown for hidden potions |
| 1 | Campaign | Leveling-focused: gold hidden under 500 (CLVL>79); super-strict gear hides begin (`FILTLVL>1 NMAG WEAPON !ETH` etc.); rejuv tier-1 style; all runes visible |
| 2 | Campaign-Strict | Gold < 1000; more non-eth gear hidden; **unid magic at Hell (DIFF=2) hidden from lvl 2+** |
| 3 | Base | Gold < 5000; HP5 fades out (shown < lvl 3, condensed < lvl 5); sockets/runeword-base info emphasized |
| 4 | Semi-Strict | Gold < 5000 (shared band); rejuv shown with 35% tag (< lvl 5); NMAG weapon/armor/quiver fully hidden from lvl 5+ |
| 5 | Strict | Gold < 7500; **unid magic hidden from lvl 5+** (ILVL>84 magic chests explicitly protected by GG rules); stat-scored magic/rare gear trimmed (the `FILTLVL>4 <7` valueless-gear hides) |
| 6 | Stricter | Gold < 7500; tighter gear hides (`FILTLVL>6` stat gates on weapons/armor/belts/shields); rejuv hidden |
| 7 | Extremely Strict | Gold < 10000; mid runes styled/condensed; magic gear only with valuable stat combos |
| 8 | Extremely Strict+ | Gold < 10000; Lem–Pul stack tooltips switch to HR-only; gem stack icons trimmed |
| 9 | High roller | **Runes El–Fal (RUNE<18) hidden** (`RUNE<18 RUNE>0 FILTLVL>8`); gold < 10000; only value + runeword bases remain |
| 10 | 3 Min Mapper | Fastest mapping view; gold < 10000; 3-star unid sounds suppressed (`FILTLVL<10` gates) |
| 11 | No Items Out of Town | Everything hidden out of town (`FILTLVL>10` kill rule) — **except S14 priority chests** (eth/non-eth elite 0/3/4os, magic/rare ILVL>84), which stay visible by design; rejuv shown as `powerade`; all gold hidden |

Protected across **all** strictnesses: all uniques & sets, all runes Um+ (with tier
audio), WSS-family materials, essences, uber keys/organs, S14 priority chests, quest
items (with `4721` quest ding), vendor-gold-tag items, and every 3-tier-qualified drop.
Potion hiding can additionally be forced from the launcher (BH.json
`hide_healing_potions` / `hide_mana_potions` — those override filter rules).

---

## 5. Hotkeys & On-Demand API Controls

| Key | Where | What happens |
|---|---|---|
| **`Ctrl + C`** | in game (hovering an item) | PD2 copies the hovered item's full stats (JSON) to the Windows clipboard — this is native PD2 behaviour |
| **`F10`** | global (watcher running) | `stash_pricer.py` reads that clipboard copy, queries pd2trader's public **rare appraisal** (for rares/crafts) and **price-check** (exact-stat comps) endpoints and pops a non-focus-stealing topmost toast with the live HR range + verdict. No alt-tabbing |
| **`F9`** | global (watcher running) | Forces an immediate full cycle: re-pulls your stash/inventory, re-runs `sync_pd2_prices.py` (live economy → `GG_DEEP_DELVE.filter` aliases + thresholds), rebuilds `stash_prices.html`, plays a confirmation chime |
| **`Numpad 0`** | in game | Native PD2 filter-reload — press it right after `F9` to load the refreshed prices in-game |

Hotkeys use the `keyboard` package (in `requirements.txt`). If PD2 runs as
Administrator, the watcher must run elevated too (Task Scheduler task is registered
with `/RL LIMITED`; re-run `Install_Auto_Watcher.bat` from an admin prompt with
`/RL HIGHEST` if your game is elevated). Without hotkeys the watcher still polls
normally; F9/F10 are conveniences, not requirements.

---

## 6. Troubleshooting & Maintenance

- **"Token Expired — Refresh PD2_AUTH_TOKEN in .env"** — the watcher received 401/403
  from the authenticated endpoint. Get a fresh session token for your account, paste it
  into `.env` as `PD2_AUTH_TOKEN=...`, then re-run `scripts\Price_My_Stash.bat --watch`.
  The watcher stays paused (no API spam) until then; the dashboard keeps showing the
  banner.
- **"no stash endpoint resolved"** — PD2's authenticated API paths are not publicly
  documented. `stash_pricer.py` logs every candidate it tried; edit `PD2_STASH_PATHS`
  in `.env` to match whatever your token source documents. Pricing itself uses public
  endpoints and works regardless.
- **Filter didn't change in game** — press `Numpad 0` to reload; confirm the launcher
  has *GG_DEEP_DELVE* selected under **Local**.
- **Restore** — `Hiim_Hyper.filter.bak` is the pristine original (never overwritten);
  rolling `*.sync-bak` files capture the pre-sync state of each filter; the git history
  in this repo tracks every filter release.
- **Tier sounds wrong?** — run `python sync_pd2_prices.py` (or the Desktop
  `Sync_PD2_Filter.bat`); it re-anchors Tier 1 to the live Um price and re-tunes every
  tier from the live economy, printing the full tier table.
- **money.mp3** — see §1.2: PD2 supports `%SOUNDID-n%` only; MPQ replacement required.

---

## 7. Verification (what was actually checked)

- Live API pulls (ladder SC, 168 h): 663 tracked items; Zod 4.75 / Ber 3.3 / Jah 2.15 /
  Sur 1.65 / cwss 1.25 / Ohm 1.00 / imrn 0.80 / lbox 0.50 / essences 0.10–0.25 HR.
- Rare guide ladders (S14, 2026-11-16) and price-check percentiles fetched and written
  into the filter's appraisal aliases; sync script exits 0 and is idempotent.
- Filter lint: 3,942 original lines preserved; 4,159+ lines after Part 1; bracket/brace
  and alias-resolution checks pass; GG copy is byte-identical to the live file.
- Season 14 mechanics verified against wiki.projectdiablo2.com (STAT360 corruption IDs
  27/50/32, `%SOUNDID-n%` limitations, local-filter overwrite safety) and the official
  S14 patch notes (runewords, corruption changes).
- **Known limitation, stated honestly:** ring/jewel unique names (SoJ, Raven Frost,
  Wisp…) share the `rin`/`jew` base code, so per-unique ID'd tooltips exist only for
  uniques distinguishable by base code or stat signature (Shako, Griffon's, Mara's,
  Verdungo's, War Traveler, Gore Rider, Stormshield, Gheed's, facets). Ring uniques fall
  back to the generic unique modifier rules and the watcher's live pricing.
