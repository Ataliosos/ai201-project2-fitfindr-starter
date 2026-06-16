# FitFindr — Planning Document

> Written before implementation. Updated before each stretch feature.
> This document was used to direct Claude to generate all tool implementations,
> the planning loop, and the Gradio UI.

---

## Tools

### Tool 1: `search_listings`

**What it does:**
Searches the mock listings dataset (`data/listings.json`) for items that match a natural language description, optionally filtered by clothing size and maximum price. Returns a relevance-scored list of matching listing dicts sorted best-first.

**Input parameters:**
- `description` (str): Natural language keywords to match against a listing's `title`, `description`, `style_tags`, `category`, and `brand` fields. Example: `"vintage graphic tee"`.
- `size` (str | None): Clothing size to filter by, e.g. `"M"`, `"S"`, `"28"`, `"XL"`. Items with size `"OS"` or `"ONE SIZE"` always pass the size filter. Pass `None` to skip size filtering.
- `max_price` (float | None): Maximum price in USD. Items with `price > max_price` are excluded. Pass `None` to apply no price ceiling.

**What it returns:**
`list[dict]` — a list of listing dicts from `listings.json`, filtered and sorted by relevance score. Each dict contains:
- `id` (str): unique listing ID e.g. `"L001"`
- `title` (str): short name e.g. `"Faded Nirvana Band Tee"`
- `description` (str): longer item description
- `category` (str): one of `tops`, `bottoms`, `outerwear`, `dresses`, `shoes`, `accessories`
- `style_tags` (list[str]): aesthetic tags e.g. `["vintage", "grunge", "band tee"]`
- `size` (str): e.g. `"M"`, `"OS"`
- `condition` (str): `"Like New"`, `"Good"`, or `"Fair"`
- `price` (float): price in USD
- `colors` (list[str]): dominant colors
- `brand` (str): brand name or `"Unknown"`
- `platform` (str): where it's listed e.g. `"Depop"`, `"Poshmark"`

Relevance scoring weights: title keyword match = 3 pts, style_tag match = 2 pts, body/other field match = 1 pt. Items with score = 0 are excluded. Returns `[]` (empty list) on no matches — **never raises an exception**.

**What happens if it fails or returns nothing:**
If `results == []`, the planning loop sets `session["error"]` to a specific actionable message — e.g. *"No listings found for 'vintage graphic tee' in size M under $30. Try: removing the size filter; raising your max price above $30; using broader keywords like 'graphic tee'."* — and returns the session immediately. `suggest_outfit` and `create_fit_card` are **never called** when search returns empty.

---

### Tool 2: `suggest_outfit`

**What it does:**
Given a thrifted item the user is considering buying and their current wardrobe, calls the LLM (Claude) to suggest one or more complete outfit combinations. Returns a detailed styling suggestion as a plain string.

**Input parameters:**
- `new_item` (dict): A single listing dict exactly as returned by `search_listings` — the item the user wants to style.
- `wardrobe` (dict): The user's wardrobe with two keys:
  - `"style_profile"` (list[str]): broad aesthetic tags e.g. `["grunge", "streetwear"]`
  - `"items"` (list[dict]): wardrobe pieces, each with `title`, `category`, `colors`, `style_tags`
- `occasion` (str): Optional occasion hint e.g. `"internship interview"`, `"going out"`. Pass `""` if none.
- `profile_context` (str): Optional style memory summary from `memory.py`. Pass `""` if none.

**What it returns:**
`str` — a 3–5 sentence outfit suggestion that names specific wardrobe pieces by their exact titles, describes the overall vibe, and includes 1–2 practical styling tips (e.g. how to tuck, roll, or layer). On any failure, returns a descriptive error string — **never raises an exception**.

**What happens if it fails or returns nothing:**
- *Empty wardrobe:* if `wardrobe["items"] == []`, the LLM prompt is adjusted to suggest general versatile pieces a person might already own. The tool does not crash or return `""`.
- *LLM API failure:* returns `"Outfit suggestion unavailable right now — try again in a moment."` The planning loop catches this and stops — `create_fit_card` is not called.

---

### Tool 3: `create_fit_card`

**What it does:**
Given a complete outfit suggestion and the thrifted item's listing dict, calls the LLM to generate a short (1–3 sentence) casual, shareable social-media caption in the style of a real Instagram or TikTok post. LLM temperature is set to 0.95 to ensure each call produces different output.

**Input parameters:**
- `outfit` (str): The outfit suggestion string returned by `suggest_outfit`.
- `new_item` (dict): The primary listing dict — used to ground the caption with real price, platform, and item name.
- `outfit_set` (list[dict] | None): If a multi-item outfit was built by `search_outfit_set`, pass all items here so the caption can reference the total price and all platforms. Pass `None` for single-item mode.

**What it returns:**
`str` — 1–3 sentences, all lowercase, personal tone, includes price and platform naturally, 1–2 emojis, no hashtags. Each call on the same input should produce a noticeably different caption. On any failure, returns a descriptive error string — **never raises an exception**.

**What happens if it fails or returns nothing:**
- *Empty outfit string:* if `outfit` is `""` or whitespace-only, returns `"Can't create a fit card without an outfit — make sure the outfit suggestion ran first."` No LLM call is made.
- *LLM API failure:* returns `"Fit card generation failed — but your outfit suggestion is above!"` so the user still has something useful.

---

### Tool 4 (Stretch): `compare_price`

**What it does:**
Compares a listing's price against other items in the same category that share at least one style tag, and returns a price fairness verdict with reasoning.

**Input parameters:**
- `item` (dict): A listing dict to evaluate. Must have `category`, `style_tags`, `price`, `id`.

**What it returns:**
`dict` with keys: `verdict` (str: `"steal"` / `"fair"` / `"overpriced"` / `"unknown"`), `avg_comparable_price` (float | None), `comparable_count` (int), `reasoning` (str), `comparable_titles` (list[str]). Verdict logic: 15%+ below avg = steal; 20%+ above avg = overpriced; in between = fair. Returns `"unknown"` if fewer than 2 comparables exist.

**What happens if it fails or returns nothing:**
Returns `{"verdict": "unknown", "reasoning": "Not enough comparable listings to assess price."}` — never raises.

---

### Tool 5 (Stretch): `search_outfit_set`

**What it does:**
Finds a complete multi-item outfit under a total budget by selecting the highest-relevance item for each required category (tops, bottoms, shoes, outerwear) sequentially, using remaining budget after each pick. Implements retry logic — if no item is found at the strict size/price constraint, it automatically retries with loosened constraints and records what was adjusted.

**Input parameters:**
- `occasion` (str): e.g. `"casual summer internship interview"` — used as search keywords.
- `total_budget` (float): Total spend ceiling across all items combined.
- `required_categories` (list[str] | None): Categories to fill. Defaults to `["tops", "bottoms", "shoes"]`.
- `style_hints` (list[str] | None): Style tags to prefer e.g. `["business casual", "smart casual"]`.
- `size` (str | None): Size filter applied to tops and bottoms only.

**What it returns:**
`dict` with keys: `items` (list[dict] — one per category filled), `total_price` (float), `categories_filled` (list[str]), `categories_missing` (list[str]), `budget_remaining` (float), `fallback_applied` (bool), `fallback_note` (str explaining what was loosened).

**Retry logic (built-in):** For each category, if the strict search (with size filter) returns nothing, the tool automatically retries without the size constraint. If that also fails, it retries with a 40% price increase. Each retry sets `fallback_applied = True` and appends a human-readable note to `fallback_note`.

**What happens if it fails or returns nothing:**
If no items are found at all, returns an empty `items` list. The planning loop catches this and sets `session["error"]` with specific suggestions.

---

### Tool 6 (Stretch): `get_trend_report`

**What it does:**
Calls the LLM to generate a structured trend awareness report for a given style aesthetic and optional occasion. Simulates what a real trend-scraping tool would return — trending pieces, colors, what to avoid, and a vibe summary.

**Input parameters:**
- `style` (str): Style descriptor e.g. `"business casual"`, `"streetwear"`, `"cottagecore"`.
- `occasion` (str): Optional context e.g. `"summer internship"`. Pass `""` if none.

**What it returns:**
`dict` with keys: `trending_pieces` (list[str]), `trending_colors` (list[str]), `avoid_right_now` (list[str]), `style_tip` (str), `vibe_summary` (str), `source_note` (str). Returns a safe fallback dict (empty lists, error message in `style_tip`) if the LLM fails or JSON parsing fails.

**What happens if it fails or returns nothing:**
Returns a safe default dict with empty lists and `"Trend data unavailable right now."` in `style_tip`. Agent continues without trend data rather than stopping.

---

## Planning Loop

The planning loop lives in `run_agent()` in `agent.py`. It uses a single `session` dict to track state. Here is the exact conditional logic, step by step:

```
Step 1 — Load style memory
  profile_context = get_profile_context(user_id)
  IF profile_context is non-empty:
      session["used_memory"] = True
  IF wardrobe is None:
      load wardrobe from memory profile; fall back to get_example_wardrobe()

Step 2 — Parse query
  params = _parse_query(user_query, profile_context)
  Extracts: description, size, max_price, total_budget, occasion,
            style_hints, is_outfit_request, required_categories
  Stores as session["query_params"]

Step 3 — Trend awareness (stretch, conditional)
  IF enable_trend_awareness AND (style_hints OR occasion):
      trend = get_trend_report(style_hints or description, occasion)
      session["trend_report"] = trend
  ELSE: skip

Step 4A — Outfit set mode (IF is_outfit_request AND total_budget is set)
  outfit_result = search_outfit_set(occasion, total_budget, required_categories,
                                    style_hints, size)
  session["outfit_set"] = outfit_result
  IF outfit_result["fallback_applied"]:
      session["fallback_applied"] = True
      session["fallback_note"] = outfit_result["fallback_note"]
  IF outfit_result["items"] == []:
      session["error"] = "Couldn't find items for a complete outfit under $X..."
      RETURN session  ← stop, don't call suggest_outfit

  session["selected_item"] = first "tops" item, or items[0]
  session["search_results"] = outfit_result["items"]
  → continue to Step 5

Step 4B — Single item mode (ELSE)
  results = search_listings(description, size, max_price)
  IF results == [] AND size is set:
      RETRY: results = search_listings(description, size=None, max_price)
      IF results found:
          session["fallback_applied"] = True
          session["fallback_note"] = "No results in size X — showing closest match..."
  IF results == [] after retry:
      session["error"] = "No listings found for '...' in size X under $Y. Try: ..."
      record_interaction(user_id, query, None, outcome="no_results")
      RETURN session  ← stop, don't call suggest_outfit
  session["search_results"] = results
  session["selected_item"] = results[0]
  → continue to Step 5

Step 5 — Price comparison (stretch, conditional)
  IF enable_price_comparison AND selected_item:
      session["price_verdict"] = compare_price(selected_item)

Step 6 — Suggest outfit
  outfit = suggest_outfit(selected_item, wardrobe, occasion, profile_context)
  session["outfit_suggestion"] = outfit
  IF outfit starts with "Outfit suggestion unavailable":
      session["error"] = outfit
      RETURN session  ← stop, don't call create_fit_card

Step 7 — Create fit card
  fit_card = create_fit_card(outfit, selected_item, outfit_set items or None)
  session["fit_card"] = fit_card

Step 8 — Persist to memory
  record_interaction(user_id, query, selected_item, outcome="success")
  extract_and_save_style_from_query(user_id, query, session)
  IF outfit_set: update_wardrobe(user_id, outfit items as wardrobe entries)

  RETURN session  ← success
```

**Key adaptive behaviors — what makes this a real planning loop, not a fixed sequence:**
- Step 4A vs 4B branch: the agent follows a completely different code path depending on whether the user asked for a full outfit or a single item. This is detected by `_parse_query()` returning `is_outfit_request: true/false`.
- Step 4B retry: if `search_listings` returns `[]` with a size filter, the agent automatically retries without the size filter before giving up.
- Step 4A/4B early return: if no items are found after all retries, `suggest_outfit` and `create_fit_card` are **never called**. The loop truly stops.
- Step 6 gate: if `suggest_outfit` returns an error string, `create_fit_card` is never called with broken input.
- Step 3 conditional: trend awareness only runs when there is style context to query — it doesn't fire for bare keyword searches.

---

## State Management

All state is stored in a single `session` dict created at the top of `run_agent()` and passed through every step:

```python
session = {
    "user_id":            str,          # user identifier for memory
    "query_params":       dict | None,  # parsed search params
    "search_results":     list | None,  # all items from search/outfit_set
    "selected_item":      dict | None,  # the primary item (results[0] or tops item)
    "outfit_set":         dict | None,  # full multi-item result from search_outfit_set
    "price_verdict":      dict | None,  # from compare_price
    "trend_report":       dict | None,  # from get_trend_report
    "outfit_suggestion":  str  | None,  # from suggest_outfit
    "fit_card":           str  | None,  # from create_fit_card
    "error":              str  | None,  # set if any tool fails or returns empty
    "fallback_applied":   bool,         # True if retry logic kicked in
    "fallback_note":      str,          # human-readable explanation of fallback
    "used_memory":        bool,         # True if style memory was loaded
}
```

**How data flows between tools:**
1. `search_listings` / `search_outfit_set` → result stored in `session["selected_item"]`
2. `session["selected_item"]` → passed directly into `suggest_outfit` — user never re-enters it
3. `suggest_outfit` result → stored in `session["outfit_suggestion"]`
4. `session["outfit_suggestion"]` + `session["selected_item"]` → passed directly into `create_fit_card`
5. Final `session` dict → returned to `handle_query()` in `app.py`, which maps each key to its output panel

**Cross-session memory** (stretch): `memory.py` persists style signals to `data/user_profiles/{user_id}.json` after each successful interaction. On the next session for the same `user_id`, `get_profile_context()` loads that file and injects a text summary into both `_parse_query()` and `suggest_outfit()`, so the agent already knows the user's aesthetic, occasions, and price sensitivity without them re-entering it.

---

## Error Handling

| Tool | Failure mode | Agent response |
|---|---|---|
| `search_listings` | Returns `[]` — no keyword matches with current filters | Sets `session["error"]`: *"No listings found for '[description]' in size [X] under $[Y]. Try: removing the size filter; raising your max price above $[Y]; using broader keywords (e.g. 'graphic tee' instead of 'vintage band tee')."* Agent stops — `suggest_outfit` never called. |
| `search_listings` | Returns `[]` after size-filtered search (single-item mode) | **Automatically retries** without the size filter first. If results found, sets `session["fallback_note"]` = *"No results found in size [X] — showing closest match (different size). Consider checking sizing."* If still empty after retry, sets error and stops. |
| `search_outfit_set` | Can't fill a category within budget | Automatically retries: first without size filter, then with 40% price increase. Sets `fallback_applied = True` and `fallback_note` explaining which category and what was loosened. If all retries fail, sets `session["error"]` and stops. |
| `search_listings` | File read error | Returns `[]` and logs the error. Same early-stop behavior as no-results. User sees: *"Couldn't load the listings database. Check that data/listings.json exists."* |
| `suggest_outfit` | `wardrobe["items"]` is empty | LLM prompt is rewritten to omit wardrobe references and instead suggest versatile general pieces. Returns useful styling advice — does not crash or return `""`. |
| `suggest_outfit` | LLM API failure (network error, rate limit, etc.) | Returns `"Outfit suggestion unavailable right now — try again in a moment."` Planning loop catches this error string, sets `session["error"]`, and stops. `create_fit_card` is not called. |
| `create_fit_card` | `outfit` param is `""` or whitespace-only | Returns `"Can't create a fit card without an outfit — make sure the outfit suggestion ran first."` No LLM call is made. User still sees the search results and price data. |
| `create_fit_card` | LLM API failure | Returns `"Fit card generation failed — but your outfit suggestion is above!"` so the user still has the outfit suggestion even if the caption fails. |
| `compare_price` | Fewer than 2 comparable listings | Returns `{"verdict": "unknown", "reasoning": "Not enough comparable listings to assess price."}` Agent continues — price verdict is optional, not a gate. |
| `get_trend_report` | LLM failure or bad JSON response | Returns safe fallback dict with empty lists. Agent continues — trend data is optional. |

---

## Architecture

```
User query (natural language)
    │
    ▼
┌───────────────────────────────────────────────────────────────────┐
│                    run_agent()  —  Planning Loop                  │
│                         (agent.py)                                │
│                                                                   │
│  Step 1: get_profile_context(user_id)                             │
│          └─► session["used_memory"] = True/False                  │
│              wardrobe loaded from memory if available             │
│                                                                   │
│  Step 2: _parse_query(user_query, profile_context)                │
│          └─► session["query_params"]                              │
│              {description, size, max_price, total_budget,         │
│               occasion, style_hints, is_outfit_request}           │
│                                                                   │
│  Step 3: [STRETCH] get_trend_report(style, occasion)              │
│          └─► session["trend_report"]  (skipped if no style hint)  │
│                                                                   │
│  Step 4:                                                          │
│    ┌── is_outfit_request AND total_budget? ──────────────────┐    │
│    │ YES                                                      │ NO │
│    ▼                                                          ▼    │
│  search_outfit_set(...)              search_listings(desc,sz,price)│
│    │                                   │                          │
│    │  items==[]                         │  results==[]             │
│    ├──► session["error"]               ├──► RETRY without size    │
│    │    RETURN  ◄── early exit         │         │                │
│    │                                   │  still []                │
│    │  items found                      ├──► session["error"]      │
│    │  fallback_applied?                │    RETURN  ◄── exit      │
│    ├──► session["fallback_note"]       │                          │
│    │                                   │  results found           │
│    └─► session["selected_item"]        └─► session["selected_item"]│
│        session["search_results"]           session["search_results"]│
│                    │                                              │
│                    └────────────────────┬─────────────────────────┘
│                                         │
│  Step 5: [STRETCH] compare_price(selected_item)
│          └─► session["price_verdict"]   │
│                                         │
│  Step 6: suggest_outfit(selected_item, wardrobe, occasion,
│                          profile_context)
│          │
│          │  error string returned
│          ├──► session["error"]
│          │    RETURN  ◄── early exit
│          │
│          │  outfit string returned
│          └─► session["outfit_suggestion"]
│                         │
│  Step 7: create_fit_card(outfit_suggestion, selected_item,
│                           outfit_set items)
│          └─► session["fit_card"]
│                         │
│  Step 8: record_interaction() + extract_and_save_style_from_query()
│          └─► writes to data/user_profiles/{user_id}.json
│                         │
│                         ▼
│              RETURN session  ◄── success path
└───────────────────────────────────────────────────────────────────┘
                          │
                          ▼
             handle_query()  in  app.py
                          │
           ┌──────────────┼──────────────┬────────────────┐
           ▼              ▼              ▼                ▼
    [Search Results] [Price+Trends] [Outfit Panel]  [Fit Card Panel]
    session[          session[       session[         session[
    "search_results"] "price_verdict""outfit_         "fit_card"]
    session[          session[        suggestion"]
    "outfit_set"]     "trend_report"]
```

**Error exits** (marked ◄── early exit above): any `RETURN session` before Step 7 leaves `session["fit_card"] == None`, which the UI panels display as *"Skipped — earlier step didn't complete."*

---

## AI Tool Plan

### Milestone 3 — Individual tool implementations

**Tool 1: `search_listings`**
- **Input to Claude:** Tool 1 spec block from this document (inputs, return value, scoring logic, failure mode) + instruction to use `load_listings()` from `utils/data_loader.py`.
- **Expected output:** A Python function that filters by all three parameters and scores keyword matches with title/tag weighting.
- **Verification before use:** Manually check generated code filters size, price, AND keywords. Test 3 queries: (a) `("vintage graphic tee", "M", 30)` → expect ≥1 result; (b) `("designer ballgown", "XXS", 5)` → expect `[]`; (c) `("jacket", None, 10)` → verify all returned prices ≤ $10. Confirm no exception on empty result.

**Tool 2: `suggest_outfit`**
- **Input to Claude:** Tool 2 spec block (inputs with types, return value, both failure modes) + the Groq/Anthropic API call pattern + instruction to handle empty wardrobe by adjusting the prompt rather than crashing.
- **Expected output:** A function that constructs a styled prompt with wardrobe context and returns the LLM text response.
- **Verification before use:** Run with `get_example_wardrobe()` — confirm wardrobe item names appear in suggestion. Run with `get_empty_wardrobe()` — confirm non-empty string returned. Check that `None` item input returns error string, not exception.

**Tool 3: `create_fit_card`**
- **Input to Claude:** Tool 3 spec block + instruction to guard against empty `outfit` before LLM call + set temperature 0.95.
- **Expected output:** Function that returns a casual caption and varies on repeated calls.
- **Verification before use:** Run 3× on same input — confirm at least 2 distinct outputs. Test `create_fit_card("", item)` — confirm error string returned, not exception.

**Tools 4–6 (Stretch):**
- **`compare_price`:** Give Claude the Tool 4 spec + listings JSON schema. Verify comparable matching uses both category AND style_tag overlap. Test with a mid-priced item and a niche item (expect "unknown").
- **`search_outfit_set`:** Give Claude the Tool 5 spec + the Step 4A branch of the planning loop diagram. Verify it iterates categories sequentially and deducts from remaining budget. Test retry logic by passing an impossible size.
- **`get_trend_report`:** Give Claude the Tool 6 spec. Verify it prompts for JSON-only output and parses it safely. Test with a malformed LLM response — confirm fallback dict returned.

### Milestone 4 — Planning loop and state management

- **Input to Claude:** The full Architecture diagram above + the Planning Loop step-by-step logic + the State Management section.
- **Expected output:** `run_agent()` with the Step 4A/4B branch, both early-return gates, and all session assignments.
- **Verification before use:**
  - Confirm Step 4A branch exists and calls `search_outfit_set` when `is_outfit_request == True`.
  - Confirm Step 4B early return: run `run_agent("designer ballgown size XXS under $5")` and verify `session["fit_card"] is None` and `session["error"]` is set.
  - Confirm `suggest_outfit` is never reached when search returns `[]` (add print statement to verify).
  - Confirm `session["selected_item"]` at Step 6 is the **exact same dict** as `results[0]` — not a copy or re-query.

---

## A Complete Interaction (Step by Step)

### Query A — Single item (original required demo)

**User query:** `"I'm looking for a vintage graphic tee under $30, size M. I mostly wear baggy jeans and chunky sneakers."`

**Step 1 — Load memory:**
`get_profile_context("default")` → `""` (first session, no prior data). Uses `get_example_wardrobe()` which includes wide-leg Levi's, platform Docs, cargo pants, white tank, New Balance 550s, silver chain.

**Step 2 — Parse query:**
`_parse_query(...)` returns:
```json
{
  "description": "vintage graphic tee",
  "size": "M",
  "max_price": 30.0,
  "total_budget": null,
  "occasion": "",
  "style_hints": ["vintage"],
  "is_outfit_request": false,
  "required_categories": []
}
```
`session["query_params"]` = above dict.

**Step 3 — Trend awareness:**
`style_hints = ["vintage"]` → `get_trend_report("vintage", "")` called.
Returns e.g.: `{trending_pieces: ["oversized denim", "faded tees"], trending_colors: ["washed black", "off-white"], style_tip: "Roll one sleeve for asymmetry.", vibe_summary: "90s revival is peaking right now."}`.
`session["trend_report"]` = above dict.

**Step 4B — Search (single item mode):**
`search_listings("vintage graphic tee", "M", 30.0)` → scans 40 listings, scores each:
- L011 MTV Logo Tee ($15, M) → matches "vintage", "graphic tee" in title + tags → score 10
- L001 Nirvana Band Tee ($22, M) → matches "vintage", "graphic", "tee" → score 9
- L023 Vintage Sports Tee ($12, M) → matches "vintage" in title + tags → score 8

Returns `[L011, L001, L023]`. Results are non-empty → no early exit.
`session["selected_item"]` = L011 (MTV Logo Tee, $15).
`session["search_results"]` = `[L011, L001, L023]`.

**Step 5 — Price comparison:**
`compare_price(L011)` → finds 8 comparable tops with overlapping style tags. Avg price = $23.88. L011 at $15 is 37% below avg.
Returns: `{verdict: "steal", avg_comparable_price: 23.88, comparable_count: 8, reasoning: "At $15.0, this is 37% below the average comparable price of $23.88 — a great deal."}`.
`session["price_verdict"]` = above dict.

**Step 6 — Suggest outfit:**
`suggest_outfit(L011, example_wardrobe, occasion="", profile_context="")` called.
Prompt includes all wardrobe items by name + item details.
LLM returns: *"Pair the faded MTV tee with your wide-leg Levi's and platform Docs for a full 90s grunge moment. Tuck the front corner of the tee slightly for shape and leave the back loose. If you want to go more streetwear, swap the Docs for your New Balance 550s and add your silver chain."*
`session["outfit_suggestion"]` = above string.

**Step 7 — Fit card:**
`create_fit_card(outfit_suggestion, L011, outfit_set=None)` called.
LLM (temp 0.95) returns: *"thrifted this faded mtv tee off depop for $15 and it was made for my wide-legs 🖤 fit check incoming"*
`session["fit_card"]` = above string.

**Step 8 — Save to memory:**
`record_interaction("default", query, L011, "success")` → appends to history.
`extract_and_save_style_from_query(...)` → saves `aesthetics: ["vintage"]`, `fit_preferences: []`, `occasions: []`, `price_sensitivity: "budget"` to `data/user_profiles/default.json`.

**Final output to user:**
- **Search Results panel:** 3 listings shown, L011 marked ⭐ TOP PICK. Price $15, condition Fair, Depop.
- **Price + Trends panel:** 🟢 STEAL — $15 is 37% below avg $23.88. Trend report: 90s revival peaking, roll one sleeve.
- **Outfit Suggestion panel:** Full 3-sentence suggestion referencing Levi's, Docs, and New Balances by name.
- **Fit Card panel:** ✨ *"thrifted this faded mtv tee off depop for $15 and it was made for my wide-legs 🖤 fit check incoming"*

---

### Query B — Multi-item outfit (stretch demo)

**User query:** `"I need an outfit for a casual summer internship interview under $150."`

**Step 2 — Parse:** `is_outfit_request: true`, `total_budget: 150.0`, `occasion: "casual summer internship interview"`, `style_hints: ["business casual", "smart casual"]`, `required_categories: ["tops", "bottoms", "shoes"]`.

**Step 3 — Trend:** `get_trend_report("business casual smart casual", "summer internship")` → returns trending pieces like linen shirts, loafers; colors like navy, cream; tip about rolling sleeves.

**Step 4A — Outfit set:**
`search_outfit_set("casual summer internship interview", 150.0, ["tops","bottoms","shoes"], ["business casual","smart casual"], size=None)`:
- tops: remaining=$150 → finds L026 Light Blue Oxford ($35) → score highest for "business casual" + "interview" tags. `total_spent = $35`.
- bottoms: remaining=$115 → finds L031 Khaki Chinos ($38) → top scorer for "chinos" + "business casual". `total_spent = $73`.
- shoes: remaining=$77 → finds L035 Brown Leather Loafers ($55) → top scorer for "loafers" + "business casual". `total_spent = $128`.

Returns: `{items: [L026, L031, L035], total_price: 128.0, budget_remaining: 22.0, fallback_applied: false}`.
`session["selected_item"]` = L026 (the tops item).

**Step 6 — Suggest outfit:**
`suggest_outfit(L026_augmented, wardrobe, "casual summer internship interview", "")` called with all 3 items embedded in the item description.
Returns: *"The light blue oxford with khaki chinos and brown loafers is the classic summer internship formula — polished without trying too hard. Roll the oxford sleeves once for a relaxed feel, and leave the top button undone. The cognac loafers pull the warm tones together with the khaki."*

**Step 7 — Fit card:**
`create_fit_card(outfit, L026, outfit_set=[L026, L031, L035])` — uses total price $128 across Poshmark, ThredUp.
Returns: *"built a whole interview outfit on poshmark and thredUp for $128 and i might actually be overdressed 😭 full fit below"*

**Final output:** All 3 items listed with prices in Search panel. Outfit total $128, $22 under budget. Outfit suggestion + fit card populated.

---

### Query C — Style memory demo

**User query (second session, same user):** `"Business casual look that is me."`

**Step 1 — Load memory:** `get_profile_context("default")` → non-empty because Query A ran first. Returns: *"[Returning user — 1 session(s)] Style aesthetics: vintage / Occasions: / Price sensitivity: budget / Recently interested in: vintage, graphic tee"*. `session["used_memory"] = True`.

**Step 2 — Parse:** With profile context injected, LLM parses this vague query and infers `style_hints: ["business casual", "vintage"]`, `occasion: "work"`, `is_outfit_request: false`.

**Step 6 — Suggest outfit:** `profile_context` is injected into the `suggest_outfit` prompt, so the LLM knows the user's vintage aesthetic and budget sensitivity. Suggestion reflects their known style rather than generic business casual advice.

**What user sees:** Status bar shows "🧠 Style memory active". Outfit suggestion is personalized to their vintage sensibility rather than a generic navy blazer recommendation.

---

### Query D — Error path demo

**User query:** `"designer ballgown size XXS under $5"`

**Step 4B — Search:** `search_listings("designer ballgown", "XXS", 5.0)` → `[]`. Size is set → retry without size: `search_listings("designer ballgown", None, 5.0)` → `[]`. Still empty after retry.

`session["error"]` = *"No listings found for 'designer ballgown' in size XXS under $5. Try: removing the size filter; raising your max price above $5; using broader keywords."*

`record_interaction("default", query, None, outcome="no_results")`.

**RETURN session** — Steps 5, 6, 7, 8 never run.

**What user sees:** Search panel shows ❌ error message with suggestions. Price, Outfit, and Fit Card panels show *"Skipped — earlier step didn't complete."* Status bar shows no memory/trend indicators.