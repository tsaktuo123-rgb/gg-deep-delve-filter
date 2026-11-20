# BUILD BRIEF — Stat-aware deterministic pricing (v0.5.0)
**For:** an implementation agent, starting fresh.
**Repo:** `gg-deep-delve-filter` — a loot filter + auto-listing engine for Project Diablo 2 S14.
**The engine:** `scripts/stash_pricer.py` (single-file watcher; scans the shared stash via the PD2 API, prices items, gates them, and maintains a market rotation). Read this brief fully before touching anything.

---

## 1. The owner's spec (verbatim intent)

The pricing gates must produce a **deterministic, stat-aware price check** for every item that passes gate 1. Current state: items price from a *pooled* median of their base+quality+corruption type — which flattens per-stat value (a top-3.5% rare jewel priced 0.48 while its true market value is ~40 HR; junk jewels of the same type also priced 0.48). The owner:

- "we need to find a way to better optimize gate 1 and 2... it runs full checks for every single item it wants to list that passes gate 1"
- "if it runs full checks on items that have sold with the exact values or even just closely matching recently it should be able to make a deterministic price check"
- Example proof: an ED 40/40 rare jewel — "the only one similar on the market is around 40hr"
- Everything must be **measured and verified from the market API** before asserting. No hand-tuned prices.

## 2. The verified data sources (all measured live by the previous sessions)

| Source | Endpoint | What it gives |
|---|---|---|
| Rare appraisal | `POST https://pd2trader.com/api/rares/appraisal` `{text: <item JSON>, isLadder, isHardcore}` | Per-stat stars, `verdict` keep/check/vendor, `rank` (0..1 percentile), `band` (often EMPTY for top ranks) |
| **Stat-conditioned market query** | `GET https://api.projectdiablo2.com/market/listing?type=item&item.base.name[$regex]=...&item.quality.name=...&item.corrupted=...&item.modifiers[$all][0][$elemMatch][name]=maxdamage_percent&item.modifiers[$all][0][$elemMatch][values.0][$gte]=30&...` | **Listings matching specific stats** — THE key capability for this build. NOT YET VERIFIED LIVE — verify first (see §5 step 1). |
| Live listings | `GET /market/listing?user_id=<uid>&type=item&$limit=100` | Our own listings |
| Sold corpus | same + `accepted_offer_id[$ne]=null`, `$sort[updated_at]=-1`, paginate `$skip` | What actually sold; **only priced solds have `hr_price`** (o.b.o. solds prove demand, not price) |
| Server valuation | `POST https://pd2trader.com/api/networth/sync` | Per-item values for runes/currency/uniques/sets (hash-keyed), rare appraisal bands, `totalHr` (THE net worth) |
| Corruption feed | `GET https://pd2trader.com/item-prices/corruption-prices?baseCode=<catalog-code>&...` | Per-corruption medians for uniques |
| Catalog | `GET https://pd2trader.com/item-prices/average/all?...` | Per-item clean vs corrupted medians + `baseCode` (name-style, e.g. `steel_carapace`) |

Auth: PD2 endpoints need `Authorization: Bearer <PD2_AUTH_TOKEN>` from `.env`. pd2trader endpoints are public.

## 3. The current pricing flow (what you are modifying)

In `scan_once()` of `scripts/stash_pricer.py`:
1. Fetch the shared stash → `fresh` items (stackables qty>1 are EXCLUDED from pricing — owner decision, do not reverse).
2. `networth/sync` → server valuation lines hash-joined to items; server rare appraisal bands by (stash_page, col, row); panel rows for currency mats. **Server-priced values are the verified anchor — nothing overrides them.**
3. The steal pass: posting price = Fair × `GG_FAIR_DISCOUNT` (0.87). For rares/crafts: the sold corpus GATES the type (≥2 priced solds of base+quality+corruption required, else the item is NOT listed) and the server's per-item band PRICES it.
4. Gate 2: floor 0.25 HR (`GG_LIST_FLOOR`) with a volume exception judged per evidence kind (rares/crafts by sold volume ≥ `GG_SOLD_MIN_VOL`=3 or 1 name-sold; server lines by sampleCount percentile ≥ `GG_VOL_TOP_PCT`=75).
5. Keep-rank floor: server-verified `keep` at rank ≥0.9 never prices below the type's top-decile sold price.
6. **Manual-listing protection (CRITICAL, do not break):** the rotation only reprices/displaces listings the WATCHER posted (tracked by listing id in `cache['listings']`). The owner's manual listings are never touched. Measured: the owner's 7 HR manual jewel listing was exposed to a downward reprice before this protection existed.
7. Hysteresis: a new target within 15% of the currently posted price keeps the sticky price (a rebaselining churn emptied the market once — do not remove).

The current pools are base+quality+corruption ONLY (see `sold_comps()` and the steal pass's comp path). **That pooling is what you are replacing with stat-aware comps.**

## 4. The build spec

**S1 — The two-tier stat-aware comp search (core):**
- Build each item's stat signature from the server appraisal's key stats (the `stats` array with `key` names and `stars` ≥ 2, or the top 2 stats by value) → 1-2 market modifier conditions (e.g. `maxdamage_percent ≥ 30`).
- **Tier A (precise):** query the market with base + quality + corruption + stat conditions — separately for LIVE asks and SOLD solds. Compute the stat-aware sold median and ask median.
- **Tier B (fallback):** the existing base+quality+corruption pools (unchanged behavior).
- **Price = the first tier that has ≥2 priced solds → its sold median; else Tier-A ask median × `GG_FAIR_DISCOUNT` (0.87); else Tier-B sold median × 0.87; else the server's per-item value.** Hard 2-decimal rounding on every posting price.
- Record provenance in `it['confidence']`: `stat-comps(n sold, m priced, sig=<stats>)` so the owner can audit any price.

**S2 — Rank-gated full checks:**
- Only items the server appraises `keep` or rank ≥0.9 get the Tier-A stat queries (the expensive per-item work goes to the items that matter).
- Ordinary items price at Tier B without stat queries.

**S3 — Sold-corpus stat enrichment:**
- Extend `fetch_sold_market()` rows with the sold item's top stats (from the sold doc's `item.modifiers`) and `item_level`/`base.id` if missing.
- The Tier-A *sold* median then computes client-side from the enriched corpus — no extra API queries for the sold side.

**S4 — The floor stack (keep as-is, verify it still holds):**
- keep-rank ≥0.9 → never below the type's top-decile sold price (the owner's ED jewel listed at 7 HR and drew immediate buyer spam — measured validation).
- Manual-listing protection + hysteresis + 2-decimal formatting: all live and verified — do not regress them.

## 5. Workflow — measured and verified at every step

1. **VERIFY the stat-conditioned query first** (this decides the whole architecture):
   `GET /market/listing?type=item&item.base.name[$regex]=^Jewel$&item.quality.name=Rare&item.corrupted=true&item.modifiers[$all][0][$elemMatch][name]=maxdamage_percent&item.modifiers[$all][0][$elemMatch][values.0][$gte]=30&accepted_offer_id=null&is_ladder=true&is_hardcore=false&hr_price[$gt]=0&$limit=30`
   - If it returns real listings → S1 is API-native.
   - If the server ignores the modifier filter (returns unfiltered rows) → fall back to a wider base+quality query and client-side stat filtering (still deterministic, more transfer).
   - Document the measured result in the CHANGELOG before building on it.
2. Implement S3 (corpus enrichment), then S2 gating, then S1 pricing — in that order, each verified.
3. Every change: `python -m py_compile scripts/stash_pricer.py` before anything else.

## 6. Acceptance tests (each must pass with printed evidence)

1. **Stat query verified:** the §5 test prints the matched listings with their `hr_price` and stats.
2. **The owner's jewel case:** a rank ≥0.9 ED-jewel-class item prices from the stat-aware comps (not the 0.48 pooled median) — show the stat-aware comp set and the computed price.
3. **Provenance:** every live listing's dashboard row shows its provenance (tier + sample counts); no price without a recorded source.
4. **Gates:** an item whose type has zero priced solds and no stat-comps is NOT listed; an item with Tier-A comps prices from them; the keep-rank floor holds.
5. **No regressions:** 2-decimal formatting, manual-listing protection, hysteresis, stackable exclusion, server-net-worth headline — all still pass a manual re-check.
6. The watcher relaunches via `scripts/Run_Watcher_Hidden.vbs` and `watcher.log` shows a clean cycle: `python -m py_compile scripts/stash_pricer.py` first.

## 7. Landmines (measured — do not step on these)

- `http_json()` returns a `(status, data)` TUPLE — always unpack (this crashed twice before).
- Sold records: `hr_price` is None for o.b.o. solds; `item.corruptions` is the corruption LABELS array.
- The sold corpus must keep the `seller_id` exclusion (the owner's own fire-sale listings polluted the evidence once — D1) and the `ilvl`/`base_id` fields.
- The market API rate-limits: sleep 0.2-0.3s between paginated calls, retry on 429.
- `market_comp_ask()` now takes `exclude_uid` and returns a DICT (`min/p25/med/n/unit_med/top_decile`) — callers are updated; keep it that way.
- The watch loop gates on `game_running()` — a manual `--once` works regardless; don't "fix" that.
- `python -m py_compile` before every restart; restart via `scripts/Run_Watcher_Hidden.vbs` only.
- The user_id is in `stash_cache.json` (`user_id`) — never hardcode it, never print the token.
- **The owner's manual listings (e.g. a 7 HR jewel) must never be repriced or displaced** — the manual-protection code paths are load-bearing.

## 8. Out of scope
- The loot filter files (the slam system was REMOVED by owner decision; the [85+] pickup tags, hide rules, and Ogre Maul/eth polearm rules are owner-approved — do not touch).
- `scripts/sync_pd2_prices.py` (the slam-table generator was removed; the alias-value refresh is stable).
- The net-worth headline = the server's `totalHr` — do not replace it with our sums (the owner rejected fabricated net values).
