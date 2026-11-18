# Changelog — GG DEEP DELVE

All notable changes to the GG DEEP DELVE Project Diablo 2 filter project.
Base: HiimFilter (Hyper) Season 14 build 247. Every original rule is preserved;
all changes are additive or value/threshold updates.

## v0.1.1 — 2026-11-18

### Pricer (`scripts/stash_pricer.py`)
- **Deep-delve pricing via the hook's own endpoint** (measured live 2026-11-17,
  HTTP 200): `POST /api/networth/sync` with the PD2 token — the server fetches
  the stash from PD2 itself and returns the whole stash priced
  (`valuation.lines`, keyed by item hash) plus every rare appraised server-side
  with verdict keep/check/vendor, predicted rank percentile within its slot,
  and a market ask band (lowHr–highHr, sample size).
- Join keys verified against the live stash: valuation `itemHashes` match the
  stash item `hash` field 297/297; server rare tabs (`Shared N`) match
  `location.stash_page`, positions `{column,row}` match exactly.
- Rare standings now come from the server's verdict + band (via the existing
  classify verdict/band path); the synthesized `/api/rares/appraisal/batch`
  shape is fallback only. Legacy valuate/price-check paths remain fallback for
  anything the server leaves unpriced.
- Result measured: 73 CHECK rows (was 32–37) matching the hook's verdicts;
  live market 40 listings / 31.91 HR total ask (was 16 / 24.46).
- `Basilisk's Quill` (unique Bow Quiver stack, `aqv3`, qty 131): earlier
  "fuzzy-match" claim REFUTED by measurement — valuate resolves the name to
  itself and prices qty × unit exactly (1→0.14, 10→1.40, 131→18.34 HR);
  price-check comps show other sellers' stacks asking 18.34 HR. Listing kept.


## v0.1.0 — 2026-11-17

### Filter (`filter/GG_DEEP_DELVE.filter`, live at `E:\...\filters\local\`)
- Renamed in-game: Horadric Cube header now reads **"GG DEEP DELVE - Season 14"**.
- **3-tier market audio engine** anchored to live economy values:
  - Tier 1 `%SOUNDID-4715%` (>= live Um floor, < 0.5 HR), Tier 2 `%SOUNDID-4719%`
    (0.5–1.0 HR inclusive + variable/unid items), Tier 3 `%SOUNDID-4733%` jackpot
    (strictly > 1.0 HR).
  - All value conditions are `QTY`-aware: stacks are priced as `QTY × unit value`.
  - Legacy sounds stripped from sub-floor items (Lem/Pul, wss) and from items whose
    live price moved tiers (cwss promoted to jackpot at 1.25 HR, Ohm held at Tier 2
    at 1.00 HR, Lo promoted at 1.15 HR).
- **Post-ID roll & slam appraisals**: named-unique tooltips (Shako, Griffon's Eye,
  Mara's, Verdungo's, War Traveler, Gore Rider, Stormshield, Gheed's) with
  Low/Avg + High/Near-Perf + GG-Slam HR estimates from pd2trader price-check
  percentiles; Rainbow Facet 5/5 detection per element (jackpot) + 4/5 estimate;
  generic eth/socketed/corrupted unique & set modifier tooltips.
- **Rare/craft/jewel HR brackets**: all `PTSRING/PTSAMU/PTSJEW/PTSSLOT` hover
  tooltips now show measured S14 asking-price brackets (pd2trader rare guide,
  2026-11-16 ladders) plus a new ≥8-point TOP-1% jackpot rule with sound.
- **S14 priority bases**: eth/non-eth elite chests (0/3/4 sockets, DEF>750),
  magic/rare elite chests ILVL>84 protected at every strictness (including a
  carve-out above the No-Items-Out-Of-Town kill rule), slam-target tooltips,
  money sound on identified `+2 All Skills` / 4os top-base eth chests.
- **Value metadata refresh**: all rune/WSS/essence/mats/key aliases updated to live
  S14 ladder prices; Vex market-range alias + tooltip row added (was missing).

### Automation
- `sync_pd2_prices.py`: one-click live economy sync (113+ aliases across both
  filters), rare-guide + unique-estimate refresh, `--dry-run`, abort-on-mismatch
  guards, rolling `.sync-bak` backups.
- `stash_pricer.py`: hands-free `--watch` stash/inventory appraiser (90 s game-aware
  polling, sha1 diff cache, public pd2trader pricing, auto-refreshing HTML/MD
  dashboard, 401/403 token-expired pause + banner, F9 force-sync, F10 clipboard
  price-check with topmost toast), `--init-filter` fallback initialization.
- `Install_Auto_Watcher.bat` (logon Task Scheduler registration) and
  `Price_My_Stash.bat` (manual runner).

### Known limitations (stated, not hidden)
- PD2 supports only `%SOUNDID-n%`; `money.mp3` requires MPQ replacement
  (ffmpeg not available on this machine at build time).
- Ring/jewel uniques share base codes — per-unique ID'd tooltips limited to
  signature-identifiable uniques; generic rules + watcher pricing cover the rest.
- PD2's authenticated stash API paths are not publicly documented; candidate paths
  are configurable in `.env` and every attempt is logged.
