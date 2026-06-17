"""
agent.py — FitFindr planning loop

run_agent() orchestrates all tools through a conditional planning loop.
Supports both single-item and multi-item (outfit set) modes.
Integrates style memory, trend awareness, price comparison, and retry logic.
"""

import json
import logging
import re

import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

from tools import (
    search_listings, suggest_outfit, create_fit_card,
    compare_price, search_outfit_set, get_trend_report,
)
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe
from utils.memory import (
    load_profile, save_profile, record_interaction,
    get_profile_context, extract_and_save_style_from_query,
    update_wardrobe,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not found in .env")
        _client = Groq(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Query intelligence — parse intent from natural language
# ---------------------------------------------------------------------------

def _parse_query(user_query: str, profile_context: str = "") -> dict:
    """
    Extract structured parameters from a free-form user query.

    Returns dict with keys:
        description, size, max_price, total_budget,
        occasion, style_hints, is_outfit_request,
        required_categories
    """
    memory_hint = f"\nUser style memory:\n{profile_context}" if profile_context else ""

    prompt = f"""Extract shopping parameters from this thrift/fashion query.
Return ONLY valid JSON with exactly these keys:
- "description": item type + style keywords (str)
- "size": clothing size like "S","M","L","XL","28","OS" — null if not mentioned
- "max_price": per-item price ceiling as number — null if not mentioned
- "total_budget": total outfit budget as number — null if not mentioned
- "occasion": occasion context like "internship interview", "going out", "work" — null if none
- "style_hints": list of style descriptors like ["business casual","smart casual"] — [] if none
- "is_outfit_request": true if user wants a COMPLETE outfit (multiple items), false for single item
- "required_categories": list of categories needed for outfit — ["tops","bottoms","shoes"] if outfit request, [] otherwise
{memory_hint}

The user is male. For style hints, preserve masculine or gender-neutral style language.
If the query says "business casual look that is me", use style memory to infer business casual, smart casual, streetwear, coat, hoodie, black, brown, or vintage if present.

Query: "{user_query}"


JSON only, no explanation:"""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.1,
        )

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        params = json.loads(raw)

        return {
            "description": str(params.get("description", user_query)),
            "size": params.get("size"),
            "max_price": float(params["max_price"]) if params.get("max_price") is not None else None,
            "total_budget": float(params["total_budget"]) if params.get("total_budget") is not None else None,
            "occasion": params.get("occasion") or "",
            "style_hints": params.get("style_hints") or [],
            "is_outfit_request": bool(params.get("is_outfit_request", False)),
            "required_categories": params.get("required_categories") or [],
        }
    
    except Exception as e:
        logger.warning(f"Query parsing failed ({e}), using defaults")
        return {
            "description": user_query, "size": None, "max_price": None,
            "total_budget": None, "occasion": "", "style_hints": [],
            "is_outfit_request": False, "required_categories": [],
        }


# ---------------------------------------------------------------------------
# Main planning loop
# ---------------------------------------------------------------------------

def run_agent(
    user_query: str,
    wardrobe: dict | None = None,
    user_id: str = "default",
    enable_price_comparison: bool = True,
    enable_trend_awareness: bool = True,
) -> dict:
    """
    Orchestrate FitFindr tools in response to a natural language user query.

    Args:
        user_query (str): Free-form user request.
        wardrobe (dict | None): User's wardrobe. If None, loads from memory or uses example.
        user_id (str): User identifier for style memory persistence.
        enable_price_comparison (bool): Run compare_price stretch tool.
        enable_trend_awareness (bool): Run get_trend_report stretch tool.

    Returns:
        dict: Full session state with all tool outputs.
    """
    session = {
        "user_id": user_id,
        "query_params": None,
        "search_results": None,
        "selected_item": None,
        "outfit_set": None,          # multi-item result
        "price_verdict": None,
        "trend_report": None,
        "outfit_suggestion": None,
        "fit_card": None,
        "error": None,
        "fallback_applied": False,
        "fallback_note": "",
        "used_memory": False,
    }

    # ------------------------------------------------------------------
    # Step 1: Load style memory
    # ------------------------------------------------------------------
    profile_context = get_profile_context(user_id)
    if profile_context:
        session["used_memory"] = True
        logger.info(f"Step 1: Loaded style memory for '{user_id}'")
    else:
        logger.info(f"Step 1: No style memory for '{user_id}' — fresh session")

    # Use memory wardrobe if no wardrobe supplied
    if wardrobe is None:
        profile = load_profile(user_id)
        mem_wardrobe = profile.get("wardrobe", {})
        if mem_wardrobe.get("items"):
            wardrobe = mem_wardrobe
            logger.info(f"  Using wardrobe from memory ({len(mem_wardrobe['items'])} items)")
        else:
            wardrobe = get_example_wardrobe()
            logger.info(f"  Using example wardrobe (no memory wardrobe found)")

    # ------------------------------------------------------------------
    # Step 2: Parse query (with memory context injected)
    # ------------------------------------------------------------------
    logger.info("Step 2: Parsing user query...")
    params = _parse_query(user_query, profile_context)
    session["query_params"] = params
    logger.info(
        f"  description='{params['description']}' | size={params['size']} | "
        f"max_price={params['max_price']} | total_budget={params['total_budget']} | "
        f"occasion='{params['occasion']}' | is_outfit={params['is_outfit_request']}"
    )

    # ------------------------------------------------------------------
    # Step 3 (Stretch): Trend awareness — runs before search to inform it
    # ------------------------------------------------------------------
    if enable_trend_awareness and (params["style_hints"] or params["occasion"]):
        style_query = " ".join(params["style_hints"]) or params["description"]
        logger.info(f"Step 3: Getting trend report for '{style_query}'...")
        trend = get_trend_report(style_query, params["occasion"])
        session["trend_report"] = trend
        logger.info(f"  Trending pieces: {trend.get('trending_pieces', [])[:2]}")
    else:
        logger.info("Step 3: Trend awareness skipped (no style hints or disabled)")

    # ------------------------------------------------------------------
    # Step 4: Search — two modes: outfit set vs single item
    # ------------------------------------------------------------------
    if params["is_outfit_request"] and params["total_budget"]:
        # ---- MULTI-ITEM OUTFIT MODE ----
        logger.info(f"Step 4: Building outfit set (budget=${params['total_budget']})...")
        outfit_result = search_outfit_set(
            occasion=params["occasion"] or params["description"],
            total_budget=params["total_budget"],
            required_categories=params["required_categories"] or ["tops", "bottoms", "shoes"],
            style_hints=params["style_hints"],
            size=params["size"],
        )
        session["outfit_set"] = outfit_result

        # Track fallback
        if outfit_result["fallback_applied"]:
            session["fallback_applied"] = True
            session["fallback_note"] = outfit_result["fallback_note"]
            logger.info(f"  Fallback applied: {outfit_result['fallback_note']}")

        # Fail if we couldn't fill any categories
        if not outfit_result["items"]:
            cats = ", ".join(params["required_categories"] or ["tops", "bottoms", "shoes"])
            budget = params["total_budget"]
            session["error"] = (
                f"Couldn't find items for a complete outfit ({cats}) under ${budget:.0f}. "
                f"Try raising your budget or using broader style terms."
            )
            logger.info(f"  No outfit items found. Stopping. Error: {session['error']}")
            return session

        # Use the primary item (top or first item) as selected_item
        primary = next(
            (i for i in outfit_result["items"] if i.get("category") == "tops"),
            outfit_result["items"][0]
        )
        session["selected_item"] = primary
        session["search_results"] = outfit_result["items"]

        filled = ", ".join(outfit_result["categories_filled"])
        missing = outfit_result["categories_missing"]
        logger.info(
            f"  Outfit set: {len(outfit_result['items'])} items | "
            f"total=${outfit_result['total_price']} | filled={filled}"
        )
        if missing:
            logger.info(f"  Missing categories: {missing}")

    else:
        # ---- SINGLE ITEM MODE ----
        logger.info(f"Step 4: Searching single item...")
        results = search_listings(
            description=params["description"],
            size=params["size"],
            max_price=params["max_price"],
        )
        session["search_results"] = results

        # BRANCH: No results → retry with loosened constraints
        if not results and params["size"]:
            logger.info(f"  No results with size={params['size']} — retrying without size filter")
            results = search_listings(
                description=params["description"],
                size=None,
                max_price=params["max_price"],
            )
            if results:
                session["fallback_applied"] = True
                session["fallback_note"] = (
                    f"No results found in size {params['size']} — "
                    f"showing closest match (different size). "
                    f"Consider checking sizing or searching broader."
                )
                session["search_results"] = results
                logger.info(f"  Fallback found {len(results)} result(s) without size filter")

        # BRANCH: Still no results → stop
        if not results:
            size_hint = f" in size {params['size']}" if params["size"] else ""
            price_hint = f" under ${params['max_price']:.0f}" if params["max_price"] else ""
            tips = []
            if params["size"]:
                tips.append("remove the size filter")
            if params["max_price"]:
                tips.append(f"raise your max price above ${params['max_price']:.0f}")
            tips.append("use broader keywords")
            session["error"] = (
                f"No listings found for '{params['description']}'{size_hint}{price_hint}. "
                f"Try: {'; '.join(tips)}."
            )
            logger.info(f"  No results. Stopping. Error: {session['error']}")
            record_interaction(user_id, user_query, None, outcome="no_results")
            return session

        session["selected_item"] = results[0]
        logger.info(f"  Found {len(results)} result(s). Top: {results[0]['title']} (${results[0]['price']})")

    # ------------------------------------------------------------------
    # Step 5 (Stretch): Price comparison
    # ------------------------------------------------------------------
    if enable_price_comparison and session["selected_item"]:
        logger.info("Step 5: Comparing price...")
        pv = compare_price(session["selected_item"])
        session["price_verdict"] = pv
        logger.info(f"  Verdict: {pv['verdict']} — {pv['reasoning']}")

    # ------------------------------------------------------------------
    # Step 6: Suggest outfit
    # ------------------------------------------------------------------
    logger.info("Step 6: Suggesting outfit...")

    # For outfit-set mode, pass all items as context
    if session["outfit_set"] and session["outfit_set"]["items"]:
        # Build a richer item description for the LLM
        items_desc = "\n".join(
            f"- {i['title']} (${i['price']}, {i['category']})"
            for i in session["outfit_set"]["items"]
        )
        augmented_item = {
            **session["selected_item"],
            "description": f"Complete outfit set:\n{items_desc}\n\n{session['selected_item'].get('description', '')}",
        }
    else:
        augmented_item = session["selected_item"]

    outfit = suggest_outfit(
        new_item=augmented_item,
        wardrobe=wardrobe,
        occasion=params["occasion"],
        profile_context=profile_context,
    )
    session["outfit_suggestion"] = outfit

    # BRANCH: LLM failure → stop
    if outfit.startswith("Outfit suggestion unavailable"):
        session["error"] = outfit
        logger.info("  Outfit suggestion failed. Stopping.")
        return session

    logger.info(f"  Outfit suggestion received ({len(outfit)} chars)")

    # ------------------------------------------------------------------
    # Step 7: Create fit card
    # ------------------------------------------------------------------
    logger.info("Step 7: Creating fit card...")
    fit_card = create_fit_card(
        outfit=outfit,
        new_item=session["selected_item"],
        outfit_set=session["outfit_set"]["items"] if session["outfit_set"] else None,
    )
    session["fit_card"] = fit_card
    logger.info(f"  Fit card: {fit_card[:80]}...")

    # ------------------------------------------------------------------
    # Step 8: Persist to style memory
    # ------------------------------------------------------------------
    logger.info("Step 8: Saving to style memory...")
    record_interaction(user_id, user_query, session["selected_item"], outcome="success")
    extract_and_save_style_from_query(user_id, user_query, session)

    # Also save outfit-set items to wardrobe context if it was a full outfit
    if session["outfit_set"] and session["outfit_set"]["items"]:
        faux_wardrobe = {
            "style_profile": params["style_hints"],
            "items": [
                {
                    "id": i.get("id", ""),
                    "title": i["title"],
                    "category": i["category"],
                    "colors": i.get("colors", []),
                    "style_tags": i.get("style_tags", []),
                    "notes": f"From outfit: {params['occasion']}",
                }
                for i in session["outfit_set"]["items"]
            ],
        }
        update_wardrobe(user_id, faux_wardrobe)

    logger.info("✅ Agent completed successfully.")
    return session


# ---------------------------------------------------------------------------
# Quick test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("\n" + "="*60)
    print("TEST 1: Multi-item outfit — casual summer internship interview")
    print("="*60)
    r1 = run_agent(
        "I need an outfit for a casual summer internship interview under $150.",
        user_id="demo_user",
    )
    if r1["outfit_set"]:
        print(f"\nItems found:")
        for item in r1["outfit_set"]["items"]:
            print(f"  {item['title']} — ${item['price']} ({item['category']})")
        print(f"Total: ${r1['outfit_set']['total_price']}")
    print(f"\nOutfit:\n{r1['outfit_suggestion']}")
    print(f"\nFit card:\n{r1['fit_card']}")

    print("\n" + "="*60)
    print("TEST 2: Style memory — 'business casual look that is me'")
    print("="*60)
    r2 = run_agent(
        "Business casual look that is me.",
        user_id="demo_user",  # same user — should use memory from Test 1
    )
    print(f"Used memory: {r2['used_memory']}")
    print(f"\nOutfit:\n{r2['outfit_suggestion']}")
    print(f"\nFit card:\n{r2['fit_card']}")

    print("\n" + "="*60)
    print("TEST 3: Error path — impossible query")
    print("="*60)
    r3 = run_agent("designer ballgown size XXS under $5")
    print(f"Error: {r3['error']}")
    print(f"Fit card is None: {r3['fit_card'] is None}")

    print("\n" + "="*60)
    print("TEST 4: Retry logic — niche size triggers fallback")
    print("="*60)
    r4 = run_agent("vintage graphic tee size XXL under $20")
    print(f"Fallback applied: {r4['fallback_applied']}")
    if r4['fallback_note']:
        print(f"Fallback note: {r4['fallback_note']}")
    if r4['selected_item']:
        print(f"Selected: {r4['selected_item']['title']} (size {r4['selected_item']['size']})")