"""
utils/memory.py — Style Profile Memory System

Persists user style preferences, wardrobe, and interaction history across
sessions using a local JSON file. Enables Query 4-style "business casual look
that is me" queries where the agent already knows the user's aesthetic.

Storage format: data/user_profiles/{user_id}.json
"""

import json
import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

PROFILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "user_profiles"
)


def _profile_path(user_id: str) -> str:
    os.makedirs(PROFILES_DIR, exist_ok=True)
    safe_id = "".join(c for c in user_id if c.isalnum() or c in "-_")
    return os.path.join(PROFILES_DIR, f"{safe_id}.json")


def _empty_profile(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "created_at": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
        "style_preferences": {
            "aesthetics": [],        # e.g. ["grunge", "streetwear", "vintage"]
            "occasions": [],         # e.g. ["casual", "business casual", "going out"]
            "avoided_styles": [],    # e.g. ["overly preppy", "all-black"]
            "fit_preferences": [],   # e.g. ["oversized", "slim", "relaxed"]
            "color_palette": [],     # e.g. ["neutrals", "earth tones", "black"]
            "price_sensitivity": None,  # "budget" | "mid-range" | "flexible"
        },
        "wardrobe": {
            "style_profile": [],
            "items": [],
        },
        "interaction_history": [],   # last N queries + outcomes
        "favorite_items": [],        # items the user saved/loved
        "total_sessions": 0,
    }


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------

def load_profile(user_id: str) -> dict:
    """
    Load a user profile from disk. Creates a new empty profile if none exists.

    Args:
        user_id (str): Unique user identifier (e.g. "default", "alice").

    Returns:
        dict: The user's full profile dict.
    """
    path = _profile_path(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Couldn't load profile for {user_id}: {e}. Starting fresh.")
    return _empty_profile(user_id)


def save_profile(profile: dict) -> bool:
    """
    Save a profile to disk.

    Args:
        profile (dict): The full profile dict (must have 'user_id' key).

    Returns:
        bool: True on success, False on failure.
    """
    user_id = profile.get("user_id", "default")
    path = _profile_path(user_id)
    profile["last_updated"] = datetime.now().isoformat()
    try:
        with open(path, "w") as f:
            json.dump(profile, f, indent=2)
        return True
    except IOError as e:
        logger.error(f"Couldn't save profile for {user_id}: {e}")
        return False


def profile_exists(user_id: str) -> bool:
    """Check if a saved profile exists for this user."""
    return os.path.exists(_profile_path(user_id))


# ---------------------------------------------------------------------------
# Style preference updates
# ---------------------------------------------------------------------------

def update_style_preferences(user_id: str, updates: dict) -> dict:
    """
    Merge new style preference data into an existing profile.

    Args:
        user_id (str): User identifier.
        updates (dict): Keys matching style_preferences fields. List fields
                        are merged (deduped), scalar fields are replaced.

    Returns:
        dict: Updated profile.

    Example:
        update_style_preferences("alice", {
            "aesthetics": ["business casual", "smart casual"],
            "color_palette": ["navy", "white", "beige"],
            "price_sensitivity": "mid-range",
        })
    """
    profile = load_profile(user_id)
    prefs = profile["style_preferences"]

    list_fields = {"aesthetics", "occasions", "avoided_styles", "fit_preferences", "color_palette"}
    scalar_fields = {"price_sensitivity"}

    for key, value in updates.items():
        if key in list_fields and isinstance(value, list):
            existing = set(prefs.get(key, []))
            existing.update(v.lower().strip() for v in value)
            prefs[key] = sorted(existing)
        elif key in scalar_fields:
            prefs[key] = value

    profile["style_preferences"] = prefs
    save_profile(profile)
    return profile


def update_wardrobe(user_id: str, wardrobe: dict) -> dict:
    """
    Replace or merge wardrobe data into the user profile.

    Args:
        user_id (str): User identifier.
        wardrobe (dict): Wardrobe dict with 'style_profile' and 'items' keys.

    Returns:
        dict: Updated profile.
    """
    profile = load_profile(user_id)

    # Merge style_profile tags
    existing_tags = set(profile["wardrobe"].get("style_profile", []))
    new_tags = set(wardrobe.get("style_profile", []))
    profile["wardrobe"]["style_profile"] = sorted(existing_tags | new_tags)

    # Merge items (deduplicate by title/name)
    existing_items = {
        item.get("title", item.get("name", item.get("id"))): item
        for item in profile["wardrobe"].get("items", [])
}

    for item in wardrobe.get("items", []):
        key = item.get("title", item.get("name", item.get("id")))
        existing_items[key] = item

    profile["wardrobe"]["items"] = list(existing_items.values())

# ---------------------------------------------------------------------------
# Interaction history
# ---------------------------------------------------------------------------

def record_interaction(user_id: str, query: str, selected_item: Optional[dict],
                       outcome: str = "success") -> dict:
    """
    Append an interaction to the user's history.

    Args:
        user_id (str): User identifier.
        query (str): The original user query.
        selected_item (dict | None): The listing that was selected, if any.
        outcome (str): "success" | "no_results" | "error"

    Returns:
        dict: Updated profile.
    """
    profile = load_profile(user_id)
    history = profile.get("interaction_history", [])

    entry = {
        "timestamp": datetime.now().isoformat(),
        "query": query,
        "outcome": outcome,
        "item_title": selected_item.get("title") if selected_item else None,
        "item_price": selected_item.get("price") if selected_item else None,
        "item_platform": selected_item.get("platform") if selected_item else None,
        "item_style_tags": selected_item.get("style_tags", []) if selected_item else [],
    }

    # Keep last 20 interactions
    history.append(entry)
    profile["interaction_history"] = history[-20:]
    profile["total_sessions"] = profile.get("total_sessions", 0) + 1

    save_profile(profile)
    return profile


def add_favorite(user_id: str, item: dict) -> dict:
    """Save an item to the user's favorites."""
    profile = load_profile(user_id)
    favorites = profile.get("favorite_items", [])
    ids = {f.get("id") for f in favorites}
    if item.get("id") not in ids:
        favorites.append(item)
        profile["favorite_items"] = favorites[-50:]  # keep last 50
    save_profile(profile)
    return profile


# ---------------------------------------------------------------------------
# Profile summary for LLM context
# ---------------------------------------------------------------------------

def get_profile_context(user_id: str) -> str:
    """
    Generate a concise text summary of the user's style profile for injection
    into LLM prompts. Returns empty string if no meaningful data exists.

    Args:
        user_id (str): User identifier.

    Returns:
        str: Human-readable profile summary, or "" if profile is empty/new.
    """
    if not profile_exists(user_id):
        return ""

    profile = load_profile(user_id)
    prefs = profile.get("style_preferences", {})
    wardrobe = profile.get("wardrobe", {})
    history = profile.get("interaction_history", [])

    lines = []

    # Style preferences
    if prefs.get("aesthetics"):
        lines.append(f"Style aesthetics: {', '.join(prefs['aesthetics'])}")
    if prefs.get("occasions"):
        lines.append(f"Common occasions: {', '.join(prefs['occasions'])}")
    if prefs.get("fit_preferences"):
        lines.append(f"Fit preferences: {', '.join(prefs['fit_preferences'])}")
    if prefs.get("color_palette"):
        lines.append(f"Color palette: {', '.join(prefs['color_palette'])}")
    if prefs.get("avoided_styles"):
        lines.append(f"Avoids: {', '.join(prefs['avoided_styles'])}")
    if prefs.get("price_sensitivity"):
        lines.append(f"Price sensitivity: {prefs['price_sensitivity']}")

    # Wardrobe summary
    wardrobe_items = wardrobe.get("items", [])
    if wardrobe_items:
        item_titles = [item["title"] for item in wardrobe_items[:6]]
        lines.append(f"Known wardrobe pieces: {', '.join(item_titles)}")

    # Recent style tags from history
    if history:
        recent_tags = []
        for entry in history[-5:]:
            recent_tags.extend(entry.get("item_style_tags", []))
        if recent_tags:
            unique_tags = list(dict.fromkeys(recent_tags))[:6]
            lines.append(f"Recently interested in: {', '.join(unique_tags)}")

    if not lines:
        return ""

    sessions = profile.get("total_sessions", 0)
    header = f"[Returning user — {sessions} session(s) on record]\n"
    return header + "\n".join(lines)


def extract_and_save_style_from_query(user_id: str, query: str, session: dict) -> None:
    """
    After a successful agent run, extract style signals from the query and
    selected item and persist them to the user's profile automatically.

    Called by run_agent() at the end of every successful interaction.
    """
    updates = {}

    # Extract style tags from the selected item
    selected = session.get("selected_item")
    if selected:
        tags = selected.get("style_tags", [])
        category = selected.get("category", "")

        # Map item tags → style preferences
        aesthetic_tags = [t for t in tags if t in {
            "vintage", "grunge", "streetwear", "cottagecore", "y2k", "preppy",
            "minimalist", "academia", "old money", "bohemian", "punk", "rock",
            "athletic", "utilitarian", "romantic", "feminine", "edgy",
            "business casual", "smart casual", "casual"
        }]
        if aesthetic_tags:
            updates["aesthetics"] = aesthetic_tags

        fit_tags = [t for t in tags if t in {
            "oversized", "baggy", "slim", "relaxed", "fitted", "wide-leg", "cropped"
        }]
        if fit_tags:
            updates["fit_preferences"] = fit_tags

    # Extract occasion hints from query text
    query_lower = query.lower()
    occasion_keywords = {
        "interview": "interviews",
        "internship": "internship",
        "work": "work",
        "business casual": "business casual",
        "casual": "casual",
        "going out": "going out",
        "date": "date night",
        "party": "parties",
        "everyday": "everyday",
        "hang": "hanging out",
    }
    found_occasions = []
    for kw, label in occasion_keywords.items():
        if kw in query_lower:
            found_occasions.append(label)
    if found_occasions:
        updates["occasions"] = found_occasions

    # Extract price sensitivity
    params = session.get("query_params", {}) or {}
    max_price = params.get("max_price")
    if max_price:
        if max_price <= 50:
            updates["price_sensitivity"] = "budget"
        elif max_price <= 150:
            updates["price_sensitivity"] = "mid-range"
        else:
            updates["price_sensitivity"] = "flexible"

    if updates:
        update_style_preferences(user_id, updates)