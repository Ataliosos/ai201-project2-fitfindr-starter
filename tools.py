"""
tools.py

FitFindr tools — required tools match the starter repo's exact interface.
LLM backend: Groq (llama-3.3-70b-versatile) via GROQ_API_KEY in .env
             Falls back to Anthropic (claude-sonnet-4-6) if GROQ_API_KEY is absent.

Required tools  (match starter repo signatures exactly):
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str

Stretch tools   (additional parameters are keyword-only with defaults):
    compare_price(item)                             → dict
    search_outfit_set(occasion, total_budget, ...)  → dict
    get_trend_report(style, occasion)               → dict
"""

import os
import re
import json
import logging

from dotenv import load_dotenv

from utils.data_loader import load_listings

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── LLM client (Groq primary, Anthropic fallback) ────────────────────────────

def _llm(prompt: str, system: str = "", temperature: float = 0.7) -> str | None:
    """
    Single-turn LLM call. Tries Groq first; falls back to Anthropic.
    Returns the response text, or None if both fail.
    """
    groq_key = os.environ.get("GROQ_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    # ── Groq path ──
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=temperature,
                max_tokens=1024,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Groq call failed ({e}), trying Anthropic fallback...")

    # ── Anthropic fallback ──
    if anthropic_key:
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=anthropic_key)
            kwargs = {
                "model": "claude-sonnet-4-6",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            response = client.messages.create(**kwargs)
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Anthropic fallback also failed: {e}")

    logger.error("No LLM available — set GROQ_API_KEY or ANTHROPIC_API_KEY in .env")
    return None


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description (str): Keywords describing what the user is looking for
                           (e.g., "vintage graphic tee"). Matched against title,
                           description, style_tags, category, and brand.
        size (str | None): Size string to filter by, or None to skip size
                           filtering. Items sized "OS" or "ONE SIZE" always pass.
        max_price (float | None): Maximum price (inclusive), or None to skip
                                  price filtering.

    Returns:
        list[dict]: Matching listing dicts sorted by relevance (best match first).
        Each dict contains: id, title, description, category, style_tags (list),
        size, condition, price (float), colors (list), brand, platform.
        Returns [] if nothing matches — never raises an exception.
    """
    try:
        listings = load_listings()
    except Exception as e:
        logger.error(f"search_listings: failed to load data — {e}")
        return []

    if not description or not description.strip():
        return []

    keywords = [kw.lower().strip() for kw in re.split(r"[\s,]+", description) if kw.strip()]

    results = []
    for item in listings:
        # ── Size filter ──
        if size is not None:
            item_size = item.get("size", "").upper()
            if item_size not in ("OS", "ONE SIZE") and item_size != size.upper():
                continue

        # ── Price filter ──
        if max_price is not None:
            if item.get("price", 0) > max_price:
                continue

        # ── Relevance scoring ──
        # Title keyword match  = 3 pts (most specific signal)
        # Style_tags match     = 2 pts (curated aesthetic match)
        # Body/other match     = 1 pt  (broader corpus match)
        score = 0
        search_corpus = " ".join([
            str(item.get("title") or ""),
            str(item.get("description") or ""),
            str(item.get("category") or ""),
            " ".join(item.get("style_tags") or []),
            str(item.get("brand") or ""),
        ]).lower()

        for kw in keywords:
            if kw in search_corpus:
                if kw in item.get("title", "").lower():
                    score += 3
                elif kw in " ".join(item.get("style_tags", [])).lower():
                    score += 2
                else:
                    score += 1

        if score > 0:
            results.append({**item, "_score": score})

    # Sort best-first, strip internal score key before returning
    results.sort(key=lambda x: x["_score"], reverse=True)
    for item in results:
        item.pop("_score", None)
    return results


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(
    new_item: dict,
    wardrobe: dict,
    # keyword-only stretch params (don't break starter-repo callers)
    *,
    occasion: str = "",
    profile_context: str = "",
) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item (dict): A listing dict — the item the user is considering buying.
        wardrobe (dict): Wardrobe dict with keys:
                         'style_profile' (list[str]) and
                         'items' (list[dict], may be empty).
        occasion (str): Optional occasion hint e.g. "internship interview".
        profile_context (str): Optional style memory summary from memory.py.

    Returns:
        str: Non-empty outfit suggestion string, 3–5 sentences. If the wardrobe
             is empty, returns general styling advice. Returns a descriptive
             error string on LLM failure — never raises an exception.
    """
    if not new_item:
        return "Outfit suggestion unavailable — no item was provided."

    wardrobe_items = wardrobe.get("items", [])
    style_profile  = wardrobe.get("style_profile", [])
    has_wardrobe   = len(wardrobe_items) > 0

    # ── Build wardrobe context for the prompt ──
    if has_wardrobe:
        wardrobe_lines = "\n".join(
            f"- {it.get('name', 'Unnamed wardrobe item')} "
            f"({it.get('category', 'unknown')}, "
            f"colors: {', '.join(it.get('colors') or [])}, "
            f"style tags: {', '.join(it.get('style_tags') or [])})"
            for it in wardrobe_items
        )
        style_line = (
            f"Their overall style profile: {', '.join(style_profile)}."
            if style_profile else ""
        )
        wardrobe_context = f"The user's current wardrobe:\n{wardrobe_lines}\n{style_line}"
    else:
        wardrobe_context = (
            "The user has not described their wardrobe. Suggest versatile general "
            "pieces that would commonly pair well with this item — think about what "
            "most people might already own or could easily find secondhand."
        )

    memory_section   = f"\nStyle memory from past sessions:\n{profile_context}\n" if profile_context else ""
    occasion_section = f"\nTarget occasion: {occasion}" if occasion else ""

    prompt = f"""You are a creative thrift-fashion stylist helping someone put together an outfit.

    The user is male. Use masculine or gender-neutral language.
    Do NOT refer to the user as "girl", "sis", "queen", or make assumptions about identity.

The user is considering buying this thrifted item:
- Title: {new_item.get('title')}
- Category: {new_item.get('category')}
- Style tags: {', '.join(new_item.get('style_tags', []))}
- Colors: {', '.join(new_item.get('colors', []))}
- Condition: {new_item.get('condition')}
- Price: ${new_item.get('price')}
- Platform: {new_item.get('platform')}
{occasion_section}
{memory_section}
{wardrobe_context}

Suggest 1–2 complete outfit combinations that incorporate this new item. Be specific — \
reference wardrobe pieces by their exact names when the wardrobe is provided. Include the \
overall vibe and 1–2 practical styling tips (how to tuck, roll, layer, etc.). \
3–5 sentences total. Write in a friendly, practical style like a personal stylist, not a product description.."""

    result = _llm(prompt, temperature=0.75)
    if result is None:
        return "Outfit suggestion unavailable right now — try again in a moment."
    return result


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(
    outfit: str,
    new_item: dict,
    # keyword-only stretch param
    *,
    outfit_set: list[dict] | None = None,
) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit (str): The outfit suggestion string from suggest_outfit().
        new_item (dict): The listing dict for the primary thrifted item.
        outfit_set (list[dict] | None): Full item set for multi-item outfits.

    Returns:
        str: 1–3 sentence casual caption (lowercase, 1–2 emojis, no hashtags).
             Mentions item name, price, and platform naturally.
             Returns a descriptive error string on failure — never raises.
    """
    # ── Guard: empty outfit ──
    if not outfit or not outfit.strip():
        return (
            "Can't create a fit card without an outfit — "
            "make sure the outfit suggestion ran first."
        )
    if not new_item:
        return "Can't create a fit card — the item details are missing."

    # ── Build price/platform line ──
    if outfit_set and len(outfit_set) > 1:
        total    = sum(i.get("price", 0) for i in outfit_set)
        platforms = list(dict.fromkeys(
            i.get("platform", "") for i in outfit_set if i.get("platform")
        ))
        price_line = f"Total outfit cost: ${total:.0f} across {', '.join(platforms)}"
    else:
        price_line = f"${new_item.get('price')} on {new_item.get('platform')}"

    prompt = f"""Write in the voice of a young man who likes vintage streetwear and business casual fashion.
Use masculine or gender-neutral language.
Avoid phrases like "I'm obsessed," "adorable," or references to defining the waist.
The tone should sound relaxed, confident, and natural, like:
"This blazer was a solid thrift find."
"Clean fit for work and still comfortable."
"Perfect for grabbing coffee or hanging out."

The outfit:
{outfit}

Pricing info:
{price_line}

Requirements:
- All lowercase
- 1–3 sentences, personal and specific to this exact outfit
- Include the price and platform naturally (not forced)
- 1–2 relevant emojis
- NO hashtags
- Sound like a real person posting their OOTD, not a brand or product description
- Make it feel specific to THIS outfit, not generic thrift-post language"""

    # High temperature → variation across calls
    result = _llm(prompt, temperature=0.95)
    if result is None:
        return "Fit card generation failed — but your outfit suggestion is above!"
    return result


# ── Tool 4 (Stretch): compare_price ──────────────────────────────────────────

def compare_price(item: dict) -> dict:
    """
    Estimate price fairness by comparing against similar listings in the dataset.

    Args:
        item (dict): A listing dict with 'category', 'style_tags', 'price', 'id'.

    Returns:
        dict: {verdict, avg_comparable_price, comparable_count,
               reasoning, comparable_titles}
        verdict is one of: "steal" | "fair" | "overpriced" | "unknown"
    """
    if not item:
        return {
            "verdict": "unknown", "avg_comparable_price": None,
            "comparable_count": 0, "reasoning": "No item provided.",
            "comparable_titles": [],
        }

    try:
        listings = load_listings()
    except Exception as e:
        return {
            "verdict": "unknown", "avg_comparable_price": None,
            "comparable_count": 0, "reasoning": f"Couldn't load data: {e}",
            "comparable_titles": [],
        }

    target_category = item.get("category", "").lower()
    target_tags     = set(t.lower() for t in item.get("style_tags", []))
    target_id       = item.get("id", "")
    target_price    = item.get("price", 0)

    comparables = [
        l for l in listings
        if l.get("id") != target_id
        and l.get("category", "").lower() == target_category
        and len({t.lower() for t in l.get("style_tags", [])} & target_tags) >= 1
    ]

    if len(comparables) < 2:
        return {
            "verdict": "unknown", "avg_comparable_price": None,
            "comparable_count": len(comparables),
            "reasoning": "Not enough comparable listings to assess price.",
            "comparable_titles": [c["title"] for c in comparables],
        }

    avg_price = round(sum(c["price"] for c in comparables) / len(comparables), 2)
    diff_pct  = (target_price - avg_price) / avg_price * 100

    if diff_pct <= -15:
        verdict   = "steal"
        reasoning = (
            f"At ${target_price}, this is {abs(diff_pct):.0f}% below the average "
            f"comparable price of ${avg_price} — a great deal."
        )
    elif diff_pct >= 20:
        verdict   = "overpriced"
        reasoning = (
            f"At ${target_price}, this is {diff_pct:.0f}% above the average "
            f"comparable price of ${avg_price} — you may find better value."
        )
    else:
        verdict   = "fair"
        reasoning = (
            f"At ${target_price}, this is within normal range of the average "
            f"comparable price of ${avg_price} ({diff_pct:+.0f}%)."
        )

    return {
        "verdict": verdict, "avg_comparable_price": avg_price,
        "comparable_count": len(comparables), "reasoning": reasoning,
        "comparable_titles": [c["title"] for c in comparables[:4]],
    }


# ── Tool 5 (Stretch): search_outfit_set ──────────────────────────────────────

def search_outfit_set(
    occasion: str,
    total_budget: float,
    required_categories: list[str] | None = None,
    style_hints: list[str] | None = None,
    size: str | None = None,
) -> dict:
    """
    Find a complete multi-item outfit under a total budget.

    Args:
        occasion (str): e.g. "casual summer internship interview"
        total_budget (float): Total spend ceiling across all items.
        required_categories (list[str] | None): Defaults to ["tops","bottoms","shoes"].
        style_hints (list[str] | None): Tags to prefer e.g. ["business casual"].
        size (str | None): Applied to tops/bottoms only.

    Returns:
        dict: {items, total_price, categories_filled, categories_missing,
               budget_remaining, fallback_applied, fallback_note}
    """
    if required_categories is None:
        required_categories = ["tops", "bottoms", "shoes"]
    if style_hints is None:
        style_hints = []

    search_terms = occasion + " " + " ".join(style_hints)

    def _best_for_category(
        category: str, remaining_budget: float, apply_size: bool
    ) -> dict | None:
        try:
            listings = load_listings()
        except Exception:
            return None

        all_kw = list(dict.fromkeys(
            [kw.lower().strip() for kw in re.split(r"[\s,]+", search_terms) if kw.strip()]
            + [category.lower()]
            + [s.lower() for s in style_hints]
        ))

        candidates = []
        for item in listings:
            if item.get("category", "").lower() != category.lower():
                continue
            if item.get("price", 0) > remaining_budget:
                continue
            if apply_size and size and category in ("tops", "bottoms"):
                item_size = item.get("size", "").upper()
                if item_size not in ("OS", "ONE SIZE") and item_size != size.upper():
                    continue

            score = 0
            corpus = " ".join([
                str(item.get("title") or ""),
                str(item.get("description") or ""),
                " ".join(item.get("style_tags") or []),
                str(item.get("brand") or ""),
            ]).lower()
            for kw in all_kw:
                if kw in corpus:
                    score += 3 if kw in item.get("title", "").lower() else (
                        2 if kw in " ".join(item.get("style_tags", [])).lower() else 1
                    )
            if score > 0:
                candidates.append({**item, "_score": score})

        if not candidates:
            return None
        candidates.sort(key=lambda x: x["_score"], reverse=True)
        result = candidates[0].copy()
        result.pop("_score", None)
        return result

    selected       = {}
    total_spent    = 0.0
    fallback_applied = False
    fallback_notes = []

    for cat in required_categories:
        apply_size = cat in ("tops", "bottoms")
        remaining  = total_budget - total_spent

        item = _best_for_category(cat, remaining, apply_size)

        # Retry 1 — loosen size constraint
        if item is None and size and apply_size:
            item = _best_for_category(cat, remaining, apply_size=False)
            if item:
                fallback_applied = True
                fallback_notes.append(
                    f"No {cat} in size {size} under ${remaining:.0f} — "
                    f"showing '{item['title']}' (size {item['size']})"
                )

        # Retry 2 — loosen price constraint (+40%)
        if item is None:
            item = _best_for_category(cat, remaining * 1.4, apply_size=False)
            if item:
                fallback_applied = True
                fallback_notes.append(
                    f"No {cat} under ${remaining:.0f} — "
                    f"showing '{item['title']}' at ${item['price']} (slightly over budget)"
                )

        # Retry 3 — if still no semantic match, choose the cheapest item
        # from the required category that fits the remaining budget.
        if item is None:
            try:
                listings = load_listings()
                category_items = [
                    x for x in listings
                    if x.get("category", "").lower() == cat.lower()
                    and x.get("price", 0) <= remaining
                ]

                if category_items:
                    item = min(category_items, key=lambda x: x.get("price", 999999))
                    fallback_applied = True
                    fallback_notes.append(
                        f"No strong match for {cat}; using closest affordable option "
                        f"'{item['title']}' at ${item['price']}."
                    )
            except Exception:
                pass

        if item:
            selected[cat] = item
            total_spent  += item["price"]
    return {
        "items": list(selected.values()),
        "total_price": round(total_spent, 2),
        "categories_filled": list(selected.keys()),
        "categories_missing": [c for c in required_categories if c not in selected],
        "budget_remaining": round(total_budget - total_spent, 2),
        "fallback_applied": fallback_applied,
        "fallback_note": " | ".join(fallback_notes),
    }

# ── Tool 6 (Stretch): get_trend_report ───────────────────────────────────────

def get_trend_report(style: str, occasion: str = "") -> dict:
    """
    Generate a structured trend awareness report for a style/occasion.

    Args:
        style (str): e.g. "business casual", "streetwear", "cottagecore"
        occasion (str): Optional context e.g. "summer internship"

    Returns:
        dict: {trending_pieces, trending_colors, avoid_right_now,
               style_tip, vibe_summary, source_note}
    """
    occasion_line = f" for {occasion}" if occasion else ""

    prompt = f"""You are a fashion trend analyst.

Give a concise trend report for the style: "{style}"{occasion_line}

Respond ONLY with valid JSON — no preamble, no markdown fences:
{{
  "trending_pieces": ["piece1", "piece2", "piece3", "piece4"],
  "trending_colors": ["color1", "color2", "color3"],
  "avoid_right_now": ["item1", "item2"],
  "style_tip": "one specific actionable styling tip for this aesthetic right now",
  "vibe_summary": "one sentence capturing the current energy of this style",
  "source_note": "Based on current street style trends and fashion cycle analysis"
}}"""

    result = _llm(prompt, temperature=0.6)
    _safe_fallback = {
        "trending_pieces": [], "trending_colors": [], "avoid_right_now": [],
        "style_tip": "Trend data unavailable right now.",
        "vibe_summary": "", "source_note": "LLM unavailable",
    }

    if result is None:
        return _safe_fallback

    try:
        clean = re.sub(r"```(?:json)?", "", result).strip().rstrip("`").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        _safe_fallback["style_tip"] = result[:200] if result else "Trend data unavailable."
        _safe_fallback["source_note"] = "Parsed from unstructured response"
        return _safe_fallback   