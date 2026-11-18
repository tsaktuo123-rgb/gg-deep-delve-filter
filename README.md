# GG DEEP DELVE — Project Diablo 2 Season 14 Loot Filter & Price Engine

A metadata-driven **price-check engine** built on the HiimFilter (Hyper) Season 14 filter.
Every drop's ground label and hover tooltip reflects live Season 14 market value standards:
3-tier drop audio, pre-ID metadata appraisals on drop, post-ID roll/slam appraisals on
identify, and a hands-free watcher that prices your whole stash (all shared-stash pages
and character personal stashes) with pd2trader's own deep-delve engine and keeps your
**top-45 market rotation full automatically**. Current release: **v0.2.0** (full history
in [CHANGELOG.md](CHANGELOG.md)).

> **Working on this repo? Read [VISION.md](VISION.md) first** — every change here has
> one law: make the player's session better. The backlog there is where the next QoL
> feature gets filed.

- Live economy source: [pd2trader.com](https://pd2trader.com) public API (ladder, softcore, 7-day average)
- Season: **S14 "Alliance"** (verified against the official patch notes and live API on 2026-11-17)
- Base filter: HiimFilter (Hyper), Season 14 build 247 — every original leveling rule,
  map-immunity block, vendor-gold tag and item rule is preserved; all changes are additive.
- **Public-repo hygiene:** this README contains no machine-specific paths or usernames.
  Wherever you see `<PD2 install>` it means your own Diablo II / ProjectD2 folder.

---

## 1. Quick Start & Installation

### 1.1 Filter placement

1. Copy `filter/GG_DEEP_DELVE.filter` into
   `<PD2 install>\ProjectD2\filters\local\GG_DEEP_DELVE.filter`
2. Open the **PD2 Launcher** → *Item Filter Profiles* → under **Local**, select
   **GG_DEEP_DELVE** → Play.
3. In game: **PD2 Options → Loot Filter → Enable Loot Filter**, choose a
   **Filter Level** (strictness, see section 4).
4. Reload the filter live any time with **Numpad 0** (default PD2 hotkey).

Local filters are **never auto-updated or overwritten by the launcher** (verified against
the official PD2 wiki), so your customization is safe there.

### 1.2 The money sound — honest verdict

PD2's filter engine accepts **only numeric `%SOUNDID-n%` indexes** (verified in the
BH engine source), and its data archives are **actively protected**: a loose-file
override is ignored, the vanilla `d2sfx.mpq` carries a 20,000-entry decoy flood per
filename, and a valid entry written into PD2's own `patch_d2.mpq` was still not
resolved by the game's loader. Three independent routes were tested in game to
ground truth — **a custom `money.mp3` inside PD2 is not achievable with today's
knowledge**, and this repo says so instead of pretending otherwise.

What ships instead: **Tier 3 plays `%SOUNDID-4733%`**, the built-in jackpot chime
(the same sound the base filter reserves for Vial of Lightsong / Lilith's Mirror).
You can audition it with `.playsound 4733`.

Kept for the future: `assets/money.mp3` (the full research trail lives in
`VISION.md` — if a season changes how PD2 loads its MPQs, an agent starting from
that backlog skips three dead ends). The tooling
(`scripts/install_money_sound.py`) is left in place but is not part of the
supported flow.

### 1.3 Python tooling — three scripts, one folder

Everything lives in this repository — nothing needs to sit on your Desktop. Right-click
any `.bat` below → **Create shortcut** → move the shortcut wherever you like.

| Script | What it does |
|---|---|
| `scripts\Sync_PD2_Filter.bat` | One-click **price refresh**: rewrites ~170 value aliases in the filter (and thresholds for the tier sounds) from live S14 market prices. Run it whenever, then press `Numpad 0` in game. |
| `scripts\Price_My_Stash.bat --watch` | **Hands-free watcher** (runs hidden via the logon task): scans every 90 s *only while the game is open*, prices items, maintains your **top-45 market rotation automatically** (posts high-value items, delists what falls out), updates the dashboard. (`--once` = one scan, `--check` = price the item on your clipboard) |
| `scripts\Monitor_Watcher.cmd` | **Live feed window**: double-click to watch everything the watcher prices and posts in real time (tails `watcher.log`). Close it whenever — the watcher keeps running hidden. |
| `scripts\Run_Watcher_Hidden.vbs` | Launches the watcher with **no visible window** — this is what the logon task runs. |
| `scripts\Install_Auto_Watcher.bat` | Registers the watcher as a logon scheduled task (named *"GG Deep Delve Auto Watcher"* — remove with `schtasks /Delete /TN "GG Deep Delve Auto Watcher"`). |

Setup:

```bat
cd <this repo folder>
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env          (then see section 2 for the token)
```

### 1.4 Making your own Desktop shortcut (no files on the Desktop)

1. Open the repo folder in Explorer, go into `scripts\`.
2. Right-click `Sync_PD2_Filter.bat` → **Show more options → Create shortcut** (or
   right-click → Send to → Desktop).
3. Drag the shortcut anywhere. Running it syncs prices; the actual scripts stay in the
   repo and travel with the git history.
4. Same trick works for `Price_My_Stash.bat` (add `--watch` via the shortcut's
   *Target* field if you want the watcher on demand).
5. Different PD2 install location? Set `PD2_FILTER_DIR` (and optionally the file
   names) in `.env` — both scripts read them. See `.env.example`.

---

## 2. Getting the PD2 auth token (step by step)

The watcher prices **your own stash**, so it needs **your own PD2 session token** —
a Bearer JWT tied to your account (the same token the official trade site uses;
pd2trader's own documentation confirms this design). Only you can obtain it, because
it requires **your** PD2 login. Never share your `.env`.

### Method A — browser DevTools (most reliable)

1. Log into the PD2 trade site (`projectdiablo2.com/market`) in your browser.
2. Press **F12** → open the **Network** tab.
3. Click anything that loads data on the market page (a search, a category, a refresh).
4. In the request list, click a request going to `api.projectdiablo2.com`.
5. In **Request Headers**, find `Authorization: Bearer eyJhbG...` — copy
   **everything after `Bearer `** (a long string starting with `eyJ`).
6. Open `.env` (in the repo folder) and fill in:
   ```
   PD2_AUTH_TOKEN=eyJhbGciOi...   (paste the whole thing, one line)
   PD2_ACCOUNT_NAME=your-account-name
   ```
7. Test: `scripts\Price_My_Stash.bat --once` — it should report items priced and open
   the dashboard. For the full hands-free loop run `--watch` (or rely on the logon task).

### Method B — pd2trader desktop app

1. Install the [pd2trader overlay](https://pd2trader.com/overlay) (free, Windows).
2. Use its **read-only PD2 sign-in** — it logs you in through the official PD2 login
   and manages its own token automatically.
3. If you use it anyway, Method A above is still what feeds `stash_pricer.py`;
   the app's sign-in confirms your account is token-capable.

### Token expiry

These JWTs expire. When the dashboard shows the red banner
**"Token Expired — Refresh PD2_AUTH_TOKEN in .env"**, repeat Method A (log in fresh →
copy the new token → paste). The watcher **pauses itself** during expiry — no API spam.

> Note: PD2 does not publish its authenticated API paths. `stash_pricer.py` tries a
> list of candidate endpoints and logs every attempt; if none resolve, edit
> `PD2_STASH_PATHS` in `.env` to match what your token source documents. Pricing uses
> pd2trader's **public** endpoints and works regardless.

---

## 3. Feature & Appraisal Architecture

### 3.1 The 3-tier market audio system

Tier thresholds are **alias-driven** (`*_RUNE_VALUE` etc.), so
`scripts\Sync_PD2_Filter.bat` shifts the whole hierarchy when the economy moves.
Tier 1's lower cutoff is anchored dynamically to the **live Um rune price**
(`UM_RUNE_VALUE`) — not a static decimal.

| Tier | Sound | Visual | Qualifies when |
|---|---|---|---|
| **Tier 1** | `%SOUNDID-4715%` | `%MAP-62%` dot | value·QTY `> UM_RUNE_VALUE` and `< 0.5 HR` |
| **Tier 2** | `%SOUNDID-4719%` | `%BORDER-4A%%MAP-4A%` | `0.5 HR` ≤ value·QTY `≤ 1.0 HR` (inclusive), **plus** all variable/unid items whose floor ≤ 1.0 HR but upside > 1.0 (unid Rainbow Facets, unid rings/amulets with high ALVL, 3-star/4-star unid uniques) |
| **Tier 3 (money)** | `%SOUNDID-4733%` | `%BORDER-9B%%MAP-7D%` | value·QTY **strictly > 1.0 HR** — zero false positives |

**Stack math (`QTY>1`):** every tier condition is written as
`$f(<VALUE_ALIAS>*QTY > threshold)`, so a stack's combined value is evaluated, not the
unit floor. Live examples on 2026-11-17: 3× Um = 0.45 HR → Tier 1; 8× Um = 1.2 HR →
**money**; Ohm at exactly 1.00 HR → Tier 2; Lo at 1.15 HR → **money**.

Items below the live Um floor (Lem, Pul, wss…) are **silent** — the legacy `4715`
sounds were stripped from them (zero false positives).

### 3.2 Pre-ID vs Post-ID tooltips

- **Pre-ID (on drop):** every rune, WSS-family material, essence, boss mat, uber key and
  stackable shows `Market Range` + `WSS` + `HR Estimated Value` (per unit **and** per
  stack) from its base code + `ETH` + `SOCK` + `QTY` metadata. Unidentified uniques,
  sets and high-value bases show callout visuals and, for 3/4-star drops, the star
  system with the tier-2-family sound; boss-chase uniques (Cage of the Unsullied,
  Aidan's Scar class, S14 new uniques) play the **jackpot** on drop.
- **Post-ID (on identify):**
  - Uniques/sets: conditional rules for key variable stats (ED%, -Enemy Res,
    +Skills, sockets, ethereal, `STAT360` corruption / `STAT206` desecration IDs)
    display `Low/Avg Roll Est`, `High/Near-Perf Roll Est` and `GG Slam Est` — measured
    from pd2trader price-check percentiles (refreshed by the sync script).
  - Rainbow Facets: 5/5 rolls per element are detected by `STAT329-336` and play the
    jackpot (5/5 facets measured 2.5–4.2 HR); 4/5 ≈ 0.95 HR.
  - Rares/crafts/charms/jewels/jewelry: the point system (`PTSRING_*` / `PTSAMU_*` /
    `PTSJEW_*` / `PTSSLOT_*`) displays concrete HR brackets on hover:
    orange (3–5 pts) → `Est: <p75> HR (Top 25%)`, purple (>5 pts) →
    `Est: <p90>–<p99> HR (Top 10%)`, and a **≥8-pt JACKPOT** rule →
    `Est: <p99>+ HR (Top 1 PERCENT)` with the jackpot sound. Brackets come from
    pd2trader's per-slot S14 asking-price ladders (10k–23k listings per slot).
- **Season 14 priority bases:** Eth + non-eth elite chests (0/3/4 sockets, DEF>750),
  magic/rare elite chests ILVL>84 and the S14 runeword bases
  (**Madness** 4os weapons = Sur+Mal+Vex+Shael, **Finesse** 3os armor = Nef+Ral+Dol,
  **Deception** 3os helms = Fal+Amn+Hel, **Jealousy** 3os Amazon throwing = Lo+Gul+Thul)
  are visible at every strictness, with slam-target tooltips. An identified/slammed eth
  elite chest with `+2 All Skills` (`STAT360=50/27`, `ALLSK>1`) or 4 sockets on a
  top-half base plays the **money** sound.

### 3.3 The slam system (v0.3.0) — corrupt with a Worldstone Shard

Corruption is random, so the system tells you **what the measured market pays for the
money outcomes** — then it's your roll:

- **In game, on the ground (any strictness, including Lvl 6):** a red **[SLAM]** tag
  marks every drop you should not walk past — magic weapons/amulets/rings/circlets
  ILVL 84+ (corruption slam targets: +2 skills / sockets), 4-socket shields (Spirit /
  Phoenix bases — Monarch is top-half), 3-socket claws (Chaos), 0-socket ethereal
  elite weapons (Larzuk-slam into runeword bases), and rare helms/chests ILVL 84+
  (the watcher prices and lists these).
- **In game, on hover:** identified **uncorrupted** slam-target uniques show a
  `[SLAM]` tag with the **measured post-corruption asks** (clean median vs each
  corruption's median, from live market comps, 2026-11-18): Shako +skills → 30 HR
  (300× clean), Mara's +skills → 13 HR (26×), Crown of Ages CBF → 15 HR (20×),
  Verdungo's maxfire → 3.5 HR (20×), Stormshield fire-res → 2.5 HR (16.8×),
  Nightwing's +skills → 5 HR (12.5×), War Traveler maxcold → 3.5 HR (4.7×),
  Kira's CBF → 1.5 HR (8.6×), Griffon's CBF → 2.5 HR (2.7×). Corruption is random —
  the shown values are the money outcomes, not guarantees.
- **From the watcher:** every newly-seen, uncorrupted GG unique/craft pops the
  in-game toast + chime — *SLAM IT* — with the item's price.
- Some rows rest on thin samples (a single ask); the sync refreshes them and the
  sample-size gate keeps unmeasured items out of the warnings.

### 3.4 The automation loop

- **`sync_pd2_prices.py`** pulls live prices (ladder SC, 7-day average), rewrites the
  value aliases in `GG_DEEP_DELVE.filter`, refreshes the rare-guide ladder brackets and
  unique roll estimates, and updates `CURRENCY_TIMESTAMP` + the cube header date.
  Refuses to write on any mismatch; `--dry-run` previews; rolling `*.sync-bak`
  backups are made before every write.
- **`stash_pricer.py --watch`** polls your authenticated stash every 90 s
  **only while the game is running** (zero overhead when closed), hashes each item into
  `stash_cache.json` (gitignored) so only **new or newly identified** items trigger API
  queries, prices them against public pd2trader endpoints and updates
  `stash_prices.html` (auto-refresh 60 s) + `stash_prices.md`, grouped by character and
  stash tab with grid positions and one-click copyable listing notes.
- **Deep-delve pricing (v0.2.0):** one call to pd2trader's server-side
  `networth/sync` — the same engine the desktop app's hook uses — prices the whole
  stash and appraises **every rare server-side** (verdict keep/check/vendor, predicted
  rank percentile within its slot, market ask band with sample size). The watcher's
  own deep-delve calls and join keys are verified against the live stash (item-hash
  and page/position joins, 100% match).
- **What gets scanned (v0.2.0 scope):**
  - **Shared stash — every page.** The only auto-listed source by default.
  - **Character personal stashes** — read over the signed-in socket (the same flow
    the desktop app uses); anything there is priced and listed automatically.
    PD2 only serves these to the owner's socket, so REST alone cannot see them.
  - **Character inventory & cube — deliberately not scanned** (user decision:
    nothing from the inventory is priced or listed). Equipped gear and
    mercenaries are never scanned as inventory; worn *valuations* live in the
    separate F5 toggle below.
  - The game's currency-dict counts are **not** treated as holdings (they are a
    stale save snapshot and have reported items that no longer exist).
- **The top-45 market rotation (auto-sale):** every tick the watcher re-ranks
  everything by live value and diffs against your live market listings — new
  high-value items are posted and whatever fell out of the top 45 is delisted
  (caps: 10 posts / 12 actions per tick; the market's own cap is 50). **Only
  real item objects may be listed** — an item must exist in a stash the market
  can deliver from, so phantom/unowned listings cannot happen (the rotation
  audits ownership every cycle).
- **Equipped gear (F5 toggle, standalone):** worn gear is priced with its
  **socketed jewels and runes rolled into each item's price** (verified: a
  Kira's Guardian with 3× Lightning Facet 3/4 prices at 4.19 HR with the
  facets counted). It appears in a standalone dashboard section — *"worn, NOT
  for sale"* — and is structurally excluded from standings and the rotation,
  so it never spends a listing slot. F5 turns it on/off (saved to `.env`).
- **Filter fallback:** if the live filter file is missing, run
  `python scripts/stash_pricer.py --init-filter` — it backs up the source filter
  (only if no backup exists yet) and re-initializes from it.

---

## 4. Complete Strictness Reference (`FILTLVL` 0–11)

Level names as defined by the filter (in-game dropdown order):
`0 Filter Off/Campaign · 1 Campaign · 2 Campaign-Strict · 3 Base · 4 Semi-Strict ·
5 Strict · 6 Stricter · 7 Extremely Strict · 8 Extremely Strict+ · 9 High roller ·
10 3 Min Mapper · 11 No Items Out of Town`

| Lvl | Name | Verified behaviour in this filter |
|---|---|---|
| 0 | Filter Off | Plain names only; no tier audio (sounds live in rules at 1+); warnings shown for hidden potions |
| 1 | Campaign | Leveling-focused: gold hidden under 500 (CLVL>79); first gear hides begin (`FILTLVL>1` non-eth weapons etc.); all runes visible |
| 2 | Campaign-Strict | Gold < 1000; more non-eth gear hidden; **unid magic at Hell (DIFF=2) hidden from lvl 2+** |
| 3 | Base | Gold < 5000; HP5 fades out (shown < lvl 3, condensed < lvl 5); sockets/runeword-base info emphasized |
| 4 | Semi-Strict | Gold < 5000; rejuv shown with 35% tag (< lvl 5); NMAG weapon/armor/quiver fully hidden from lvl 5+ |
| 5 | Strict | Gold < 7500; **unid magic hidden from lvl 5+** (ILVL>84 magic chests explicitly protected by GG rules); stat-scored magic/rare gear trimmed |
| 6 | Stricter | Gold < 7500; tighter gear hides (`FILTLVL>6` stat gates on weapons/armor/belts/shields); rejuv hidden |
| 7 | Extremely Strict | Gold < 10000; mid runes styled/condensed; magic gear only with valuable stat combos |
| 8 | Extremely Strict+ | Gold < 10000; Lem–Pul stack tooltips switch to HR-only; gem stack icons trimmed |
| 9 | High roller | **Runes El–Fal (RUNE<18) hidden**; gold < 10000; only value + runeword bases remain |
| 10 | 3 Min Mapper | Fastest mapping view; gold < 10000; 3-star unid sounds suppressed |
| 11 | No Items Out of Town | Everything hidden out of town — **except S14 priority chests** (eth/non-eth elite 0/3/4os, magic/rare ILVL>84), which stay visible by design; rejuv shown as `powerade`; all gold hidden |

Protected across **all** strictnesses: all uniques & sets, all runes Um+ (with tier
audio), WSS-family materials, essences, uber keys/organs, S14 priority chests, quest
items, vendor-gold-tag items, and every 3-tier-qualified drop. Potion hiding can
additionally be forced from the launcher (the BH loot-filter settings
*hide healing/mana potions* — those override filter rules).

---

## 5. Hotkeys & On-Demand API Controls

| Key | Where | What happens |
|---|---|---|
| **`Ctrl + C`** | in game (hovering an item) | PD2 copies the hovered item's full stats (JSON) to the Windows clipboard — native PD2 behaviour |
| **`F5`** | global (watcher running) | **Equipped-gear toggle**: turns the standalone worn-gear valuation on/off (saved to `.env`). When on, the dashboard gains an *"Equipped gear — worn, NOT for sale"* section where every item's price includes its socketed jewels/runes. Never spends a market slot |
| **`F6`** | global (watcher running) | **Scan & price everything now**: full scan (shared stash all pages + character personal stashes), deep-delve pricing, standings and dashboard refresh — no market actions — then a topmost toast with the totals (and your equipped-gear value when F5 is on) |
| **`F7`** | global (watcher running) | **Instant-sell flow** for the item you're hovering |
| **`F8`** | global (watcher running) | **Link & reload**: detects the *running* client's own folder, makes sure `GG_DEEP_DELVE.filter` is installed in that install's `filters\local`, then synthesizes the filter-reload keystroke into the game. One keypress links the whole engine together |
| **`F9`** | global (watcher running) | **Sync & reload**: forces an immediate full cycle — re-pulls your stash, re-runs the price sync (live economy → filter aliases + thresholds), rebuilds the dashboard, plays a chime, then sends the reload keystroke automatically |
| **`F10`** | global (watcher running) | reads the `Ctrl + C` clipboard copy, queries pd2trader's public **rare appraisal** (rares/crafts) and **price-check** (exact-stat comps) endpoints and pops a non-focus-stealing topmost toast with the live HR range + verdict. No alt-tabbing |
| **`Numpad 0`** | in game | Native PD2 filter-reload (the F8/F9 hotkeys send exactly this) |

The watcher's hotkeys need `pip install -r requirements.txt`. The same link+reload
loop also works without the watcher: `python scripts/stash_pricer.py --detect`
detects the running client, links the filter into its install and reloads — it reads
the client's `ProjectDiablo.cfg` for the actual reload binding (falls back to
Numpad 0). A client restart exists only as `--restart-client` — **explicit opt-in
only**, never wired to a hotkey, because killing the client risks a hardcore
character and is never needed to reload a filter.

Hotkeys use the `keyboard` package (in `requirements.txt`). If PD2 runs as
Administrator, the watcher must run elevated too (re-register the scheduled task from
an admin prompt with `-RunLevel Highest` if your game is elevated). Without hotkeys the
watcher still polls normally; F9/F10 are conveniences, not requirements.

---

## 6. The pricing dashboard

`stash_prices.html` opens in any browser and **auto-refreshes every 60 s**:

- Grouped by **character › stash tab**, each row shows grid position, item, quality,
  iLvl, live value in **HR** and ≈ **WSS**, and a **click-to-copy listing note**
  (paste straight into trade chat or the market listing).
- The header shows the last update time and your **total stash value in HR**.
- Character **personal stashes** appear as their own groups when they hold items.
- **Equipped gear (F5 toggle on):** a standalone *"worn, NOT for sale"* section
  where every worn item's price **includes its socketed jewels/runes** — your
  gear's true value at a glance, kept out of the market rotation.
- `stash_prices.md` mirrors it for pasting into Discord.

---

## 7. Troubleshooting & Maintenance

- **"Token Expired — Refresh PD2_AUTH_TOKEN in .env"** — see section 2; paste a fresh
  token and rerun the watcher. It stays paused (no API spam) until then.
- **"no stash endpoint resolved"** — PD2's authenticated API paths are not publicly
  documented; the script logs every candidate it tried. Edit `PD2_STASH_PATHS` in
  `.env`. Pricing works regardless (public endpoints).
- **Filter didn't change in game** — press `Numpad 0`; confirm the launcher has
  *GG_DEEP_DELVE* selected under **Local**.
- **Filter shows odd characters** — switch the filter file encoding to ANSI if you edit
  special characters yourself (stock file is fine as shipped).
- **Multiple PD2 installs** — the filter must be in the `ProjectD2\filters\local`
  folder of the install your launcher uses.
- **Restore** — the original filter backup is never overwritten by the sync script;
  rolling `*.sync-bak` files capture the pre-sync state; this repo's git history tracks
  every filter release.
- **Tier sounds wrong?** — run `scripts\Sync_PD2_Filter.bat`; it re-anchors Tier 1 to
  the live Um price and re-tunes every tier, printing the full tier table.
- **money.mp3** — see §1.2: PD2 protects its archives; the built-in jackpot
  `%SOUNDID-4733%` ships as Tier 3. Custom audio is not currently achievable.

---

## 8. Verification (what was actually checked)

- Live API pulls (ladder SC, 168 h): 663 tracked items; Zod 4.75 / Ber 3.3 / Jah 2.15 /
  Sur 1.65 / cwss 1.25 / Ohm 1.00 / imrn 0.80 / lbox 0.50 / essences 0.10–0.25 HR.
- Rare guide ladders (S14, 2026-11-16) and price-check percentiles fetched and written
  into the filter's appraisal aliases; the sync script exits 0 and is idempotent.
- Filter lint: original lines preserved; bracket/brace and alias-resolution checks
  pass; the repo copy is byte-identical to the live filter at release time.
- Season 14 mechanics verified against wiki.projectdiablo2.com (STAT360 corruption IDs
  27/50/32, `%SOUNDID-n%` limitations, local-filter overwrite safety) and the official
  S14 patch notes (runewords, corruption changes).
- **Deep-delve pricing (v0.2.0, measured 2026-11-17/18):** one `networth/sync` call
  returned the whole stash priced (join keys verified 297/297 by item hash) and 73
  rares appraised server-side (57 CHECK / 16 VENDOR); a verified cycle priced 353
  items / 73.74 HR with the rotation at 45/45 owned listings.
- **Ownership audit (measured):** every live market listing checked against the
  current stash by item id — 0 of 45 reference unowned items; the phantom-listing
  class (synthetic rows) is structurally excluded.
- **Quiver stacks are real** (user-confirmed disbelief, resolved by data): the game
  reuses arrow bases as stackable quivers — e.g. a unique Razor Arrows stack
  (`aqv3`, qty 131) and War Bolts ×235 both exist and price as qty × unit.
- **Known limitation, stated honestly:** ring/jewel unique names (SoJ, Raven Frost,
  Wisp…) share the `rin`/`jew` base code, so per-unique ID'd tooltips exist only for
  uniques distinguishable by base code or stat signature (Shako, Griffon's, Mara's,
  Verdungo's, War Traveler, Gore Rider, Stormshield, Gheed's, facets). Ring uniques
  fall back to the generic unique modifier rules and the watcher's live pricing.
