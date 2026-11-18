# Changelog — GG DEEP DELVE

All notable changes to the GG DEEP DELVE Project Diablo 2 filter project.
Base: HiimFilter (Hyper) Season 14 build 247. Every original rule is preserved;
all changes are additive or value/threshold updates.

## v0.2.1 — 2026-11-18

### Stacked listings post at UNIT price (user convention)
- Stacked items (qty > 1) post with `hr_price = ask / qty` — the per-unit
  price people search by — while the quantity stays in the listing note
  ('Nx o.b.o.'); buyers do the math at trade time. Ranking still uses the
  stack total, so rotation priority is unchanged.
- Verified live: Sigil of Madawc 0.06 (17x), Sigil of Korlic 0.09 (8x),
  Larzuk's Puzzlepiece 0.10 (5x) — previously posted at stack totals
  (0.96 / 0.72 / 0.50), delisted and reposted at unit.

## v0.2.0 — 2026-11-18

### Equipped gear: F5 toggle, standalone (user rule: never wastes a rotation slot)
- **F5 toggles the equipped-gear scan** (persists in .env as GG_SCAN_EQUIPPED;
  F6's toast includes the equipped line only when on). Worn gear is priced
  into a STANDALONE dashboard section — "Equipped gear — worn, NOT for sale"
  — and is structurally excluded from discovery, standings and rotation.
- Pricing measured on live data: pd2trader's builds API prices each equipped
  item INCLUDING its socketed jewels/runes (Kira's Guardian 4.19 HR with 3x
  Rainbow Facet (Lightning 3/4) inside, priceBasis=corruption; Arkaine's 2.58,
  Jade Talon 2.35, CTA 1.38 with runes; ChiTea's worn gear 13.35 HR total).

### Value from ALL tabs (user rule, then narrowed by user decision)
- **Character personal stashes are now reachable** — verified live via the
  hook's own socket flow (`wss://api.projectdiablo2.com`, websocket transport
  REQUIRED: polling 400s 'Session ID unknown' behind the load balancer;
  python-socketio needs a TUPLE for flat multi-args). Personal-stash items
  merge into the scan as listable (the hook lists from them; PD2 reports them
  as 'Shared Stash' page 0 in listings). Verified live: 35.26 HR found in
  ChiTea's personal stash, 4 items posted from it.
- **Character inventory/cube: REMOVED entirely per user decision** ("don't
  price anything from the inventory") — not scanned, not priced. Equipped
  gear ('Unknown' storage), mercenaries: never scanned (worn, not tradeable).
- **F6 hotkey: scan + price everything NOW** (shared stash all pages +
  personal stashes, deep-delve pricing, standings, dashboard refresh, toast)
  without touching the market.
- **The PD2 currency dict is NOT a holdings source**: its counts are a stale
  game-save snapshot (claimed 15x Sigil of Madawc / 5x Festering Essence of
  Destruction AFTER those stacks had sold; user confirmed not owning them).
  It remains priced for the dashboard, but ownership/listing comes only from
  real item objects (stash pages + character personal stashes).

### Ownership + rotation
- **Only real item objects may be listed** (shared stash pages + character
  personal stashes). Synthetic currency-walk rows (raw-less) previously posted
  phantom listings with unnamed cards for mats the user could not trade from
  the stash tab — 4 phantoms delisted 2026-11-18; post-fix audit: 0 of 45
  listings reference items not owned.
- **Per-tick rotation raised**: watch cycles re-rank and diff with caps
  10 posts / 12 actions per 90 s tick — new items displace the bottom of the
  top-45 every tick.
- **Empty-market refill deadlock fixed**: a successful lookup with zero
  listings is a valid answer (all 45 had sold); only a failed lookup twice
  blocks posting. Verified: 45 reposted in one cycle.
- Verified cycle: 353 items, 73.74 HR, 3 GG / 78 CHECK.

## v0.1.3 — 2026-11-18

### Ownership fix (user-reported: "items I don't even own")
- **Only items physically in the shared stash may be listed.** The rotation
  previously allowed synthetic currency-walk rows (raw-less), which posted 4
  phantom listings (unnamed cards) for mats whose PD2 currency-dict counts
  cover holdings OUTSIDE the shared stash — measured: dict says 5x Festering
  Essence of Destruction / 15x Sigil of Madawc / 5x Burning Essence of Terror,
  shared stash has 0 of each. Those mats live on characters; they were never
  tradeable from the stash tab. The 4 phantoms were delisted and replaced
  with real items; post-fix audit: 0 of 45 listings reference items not in
  the stash.
- The walk rows still price for dashboard/net-worth visibility, but can no
  longer enter the rotation.
- **Per-tick rotation raised**: watch cycles now re-rank and diff with caps
  10 posts / 12 actions per 90 s tick (was 5/5) — new items displace the
  bottom of the top-45 every tick.

## v0.1.2 — 2026-11-18

### Watcher (`scripts/stash_pricer.py`)
- **Watch-mode log tee**: the hidden watcher now mirrors every print (cycles,
  standings, posts, delists, hotkeys) into `watcher.log`, line-buffered,
  fresh per run. Monitor live with `scripts/Monitor_Watcher.cmd`.
- **Currency-walk fixes (measured)**: boss-subtree mats get proper path-aware
  names (leaf codes truncated: `destruction` was displaying for BOTH the
  Festering Essence of Destruction AND the map-craft orb; `Madawc` for the
  Sigil). Craft infusions named `X Craft Infusion` (matches the valuation
  API's resolution). Uber sigils + Black Soulstone no longer walked — they
  already exist in `items[]` with hashes; walking them DOUBLE-COUNTED the
  same physical stack ('Madawc' 0.9 + 'Sigil of Madawc' 0.66) and could
  list it twice.
- **Empty-market refill**: the own-listings guard treated a successful HTTP
  response with zero listings as a failure and refused to post forever.
  Measured: market hit 0 (all 45 listings gone), rotation deadlocked.
  Success+empty is now a valid answer; only a failed lookup twice blocks
  posting. Refill verified: 45 posted in one cycle.
- Festering Essence of Destruction (x5) now correctly priced GG at 1.3 HR
  (its old truncated name priced it at 0.52 and hid the jackpot tier).

### Launchers
- `scripts/Run_Watcher_Hidden.vbs` — scheduled task now launches the watcher
  with zero visible window (task action verified:
  `wscript.exe "...\Run_Watcher_Hidden.vbs"`).
- `scripts/Monitor_Watcher.cmd` — double-click for a live feed window
  (tails `watcher.log`).


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
