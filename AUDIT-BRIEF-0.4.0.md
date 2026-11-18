# AUDIT BRIEF — GG DEEP DELVE pricer (v0.4.0 pricing engine)
**Prepared for:** an audit/corrective agent, starting fresh.
**Prepared by:** the previous agent's session, at the owner's direction.
**Date:** 2026-11-18. **Repo:** `gg-deep-delve-filter` (local, git, pushed to GitHub).
**You inherit:** tags v0.2.0 → v0.4.0 of a working loot-filter + auto-listing product for Project Diablo 2 S14.

---

## 1. What the owner demanded (the spec you are auditing against)

Since v0.2.0 the owner redefined the pricing engine. Implement ALL of it, measured and verified:

1. **Gate 1 (sellable, tiered sold volume — "the most important gate"):** an item is
   listable only if its TYPE shows real sold demand, measured in tiers: exact-name
   solds (named uniques) → base+quality solds (types) → category sold volume
   (broad fallback). Config: `GG_SOLD_MIN_VOL` (default 3 type-solds, or 1 name-sold).
2. **Gate 2 (price floor + volume exception):** price must be **≥ 0.25 HR**
   (`GG_LIST_FLOOR`) — EXCEPT an item under the floor still lists when its sold
   volume ranks in the **top 75%** of the gate-1 pool (`GG_VOL_TOP_PCT`).
   "Nothing left unturned — all items get a chance to pass the gate if they have
   high enough volume."
3. **Pricing = SOLD evidence, never asks:** the ask anchors to the **median of
   recent PRICED solds** of the same type when ≥2 exist. The owner's words:
   the engine "should only be listing items that have recently sold", with
   "measured and verified evidence from market api that this item has been sold
   recently", and it must **never undercut anything that sold recently**.
4. **Volume-first rotation:** the listing queue is ranked by the sold volume of
   the item's type (highest volume first), NOT by price.
5. **Stackables:** priced from the sold **unit** median × our stack size; the
   TOTAL stack value is what the gates judge; the listing posts the unit price
   with the quantity in the note. VERIFIED stacks that pass gate 1 list directly
   at the fixed price — no extra rules.
6. **Scope:** the watcher scans ONLY the shared stash (all pages) + character
   personal stashes. Inventories/cube/equipped are never scanned.
7. Everything must be **measured and verified from the market API** before
   asserting. No hand-tuned numbers.

## 2. The measured defects (confirmed by the previous session — re-verify, then fix)

### D1 — SELF-REFERENTIAL SOLD CORPUS (the big one)
The sold corpus (`sold_market.json`, built by `fetch_sold_market()` in
`scripts/stash_pricer.py`) does **not store the seller's user id** (measured: row
keys are `name, base, quality, cat, qty, sockets, eth, corrupted, hr, at`). The
owner's account (`user_id 69ecf7868520c558413ca110`) sold 45+ items in the window —
so the corpus "evidence" is dominated by the owner's own fire-sale listings.
The engine then prices everything at the median of its own fire sales.
**Fix:** store the seller id in each row (the listing doc carries `user_id`) and
**exclude the owner's own solds from evidence** (they may stay for sale-event
tracking). Then **rebuild the corpus** and re-derive all prices.

### D2 — DOWNWARD PRICE RATCHET
In `scan_once()`'s sold-median override:
`it2['hr'] = round(min(sc['median'], pre2) if pre2 < 1.0 else sc['median'], 4)`
— the `min(..., pre2)` takes the LOWER of the sold median and the current price.
Every repricing lowers the price, the next median is computed from a corpus that
now includes that lower sale, repeat → death spiral.
**Fix:** price = the sold median, full stop. Never `min()` with the current
price. Never price below the recent sold median of the type (owner rule #3).

### D3 — THE 0.25 CONVERGENCE (the owner's report, measured)
**All 50 of the account's live listings are priced at exactly 0.25 HR**
(measured histogram: `{0.25: 50}` across jewels, charms, helms, everything).
This is D1+D2 converging at a floor. After fixing D1/D2, re-price and confirm
the histogram is no longer degenerate.

### D4 — STALE CORPUS vs CURRENT STATE
The corpus is cached 30 min (`SOLD_TTL`) and the evidence for items being priced
was fetched before the owner's recent sales — verify the refresh cadence is
sufficient and that a forced rebuild (`delete sold_market.json`) changes the
derived prices materially. If it does, the cache was serving stale evidence.

### D5 — VERIFY THE GATES MATCH THE SPEC (read the code, test the behavior)
- Gate 1 tiers: `sold_comps()` returns `vol_name/vol_type/vol_cat`; gate =
  `vol_name >= 1 or vol_type >= GG_SOLD_MIN_VOL`. Confirm the category tier is
  NOT wrongly gating items in/out (it should be a fallback, not a blocker).
- Gate 2: `list_ok = sale_ok and (hr >= floor or eff_vol >= cutoff)`; cutoff =
  the (100-top_pct) percentile of gate-1 volumes. Confirm "top 75%" direction is
  right (raising the number LOOSENS; the owner may tune it — keep it configurable).
- **Note:** the owner may choose to lower `GG_VOL_TOP_PCT` to 50 — that makes it
  STRICTER (cutoff at the median), not looser. Flag any code that implies otherwise.

### D6 — THE OWNER'S REMAINING COMPLAINT
"the prices are not being ran through the api for measured and verified proof that
this item will sell for this price" — every listed item's price must trace to sold
records (name/base + count + median + last sold date). Add that provenance to the
dashboard row (already partially in `confidence`) so the owner can audit any price
at a glance.

## 3. Files you may touch
- `scripts/stash_pricer.py` — the engine (pricing, gates, rotation, sold corpus)
- `sold_market.json`, `stash_cache.json` — caches (gitignored; delete to force rebuild)
- `.env` — config only (`GG_*` keys), never commit, never print the token
- `CHANGELOG.md`, `README.md` — document every change (owner rule: each push updates the README)

## 4. Files you must NOT touch
- `filter/GG_DEEP_DELVE.filter` and the deployed copies at
  `E:\D2\Diablo II\Diablo II\ProjectD2\filters\local\GG_DEEP_DELVE.filter` +
  `E:\D2\Diablo II\Diablo II\ProjectD2\loot.filter` — the v0.3.0 slam system is
  verified working; pricing lives in the watcher, not the filter.
- `scripts/sync_pd2_prices.py` — the slam-table generator is verified (145 bases).
- `.env.example`, `README.md` sections covering the filter (you may add a pricing
  section to the README, not rewrite it).

## 5. Acceptance tests (run these; each must pass)

1. **Corpus hygiene:** after rebuild, `sold_market.json` rows carry a seller id;
   the count of owner-sold rows excluded is printed; evidence medians change
   materially vs the polluted corpus.
2. **No degenerate pricing:** histogram of the account's live listing prices has
   ≥10 distinct values (measured via
   `GET /market/listing?user_id=<uid>&type=item&$limit=100`).
3. **No sub-sold-median listings:** for every live listing, the sold corpus's
   recent priced median for that type (excluding owner sales) is ≤ the listing's
   unit price. Spot-check 10 listings by name via the API.
4. **Gates match spec:** an item whose type sold 3+ times in the window lists;
   an item whose type has zero solds does not list; an item priced <0.25 lists
   only if its type volume is in the top 75% of the gate-1 pool. Script the
   three cases and show the output.
5. **Watch mode still runs:** `python scripts/stash_pricer.py --once --discover`
   exits 0 and the dashboard updates; the watcher relaunches via
   `scripts/Run_Watcher_Hidden.vbs` and `watcher.log` shows cycles.
6. **Commit + push** with a message listing each defect fixed (D1–D6), and update
   CHANGELOG.md + README.md.

## 6. Landmines (things that broke before — do not re-step on them)
- `http_json()` returns a `(status, data)` TUPLE — unpack it (this broke twice).
- Sold records: `hr_price` is None for o.b.o. solds (they prove demand, not price).
- The market API rate-limits: sleep between pagination pages; retry on 429.
- The watcher's watch loop gates on `game_running()` — a manual `--once` works
  regardless; don't "fix" the game gate.
- `python -m py_compile` before every restart; the watcher must be restarted via
  `scripts/Run_Watcher_Hidden.vbs` (hidden window), never with a visible console.
- The user_id is in `stash_cache.json` (`user_id` key) — never hardcode it.

## 7. Ground truth references
- Live listings: `GET https://api.projectdiablo2.com/market/listing?user_id=<uid>&type=item&$limit=100`
- Sold corpus: same endpoint + `accepted_offer_id[$ne]=null`, `$sort[updated_at]=-1`
- Catalog (per-item clean medians): `https://pd2trader.com/item-prices/average/all?isLadder=true&isHardcore=false&hours=168`
- Corruption feed: `https://pd2trader.com/item-prices/corruption-prices?baseCode=<code>&...`
- Stackable/weapon classification: stash API item `base.type` (e.g. Flying Axe =
  `7ta`, type 'Throwing Axe', category weapon, stackable true — a WEAPON stack,
  not a panel mat).
