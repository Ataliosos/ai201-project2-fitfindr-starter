"""
tests/test_tools.py — FitFindr tool test suite

Organised in three layers:
  1. Required tests from the Milestone 3 spec (exact names from the instructions)
  2. Additional failure-mode tests (one per documented failure mode)
  3. Stretch tool tests (compare_price, search_outfit_set, style memory)
  4. Integration test (full happy-path flow without LLM)

Run with:
    pytest tests/ -v
"""

import os
import sys
import time

# Make project root importable regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import search_listings, compare_price, search_outfit_set
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# =============================================================================
# ── LAYER 1: Required tests from Milestone 3 spec (exact names) ──────────────
# =============================================================================

def test_search_returns_results():
    """From the milestone spec — broad query returns at least one result."""
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    """From the milestone spec — impossible query returns [], no exception."""
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    """From the milestone spec — all returned items respect max_price."""
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


# =============================================================================
# ── LAYER 2: Failure mode tests (one per documented failure mode) ─────────────
# =============================================================================

class TestSearchListingsFailureModes:

    def test_empty_description_returns_empty_list(self):
        """Empty description string → [] without crashing."""
        results = search_listings("", size=None, max_price=None)
        assert results == []

    def test_whitespace_description_returns_empty_list(self):
        """Whitespace-only description → [] without crashing."""
        results = search_listings("   ", size=None, max_price=None)
        assert results == []

    def test_size_filter_respected(self):
        """All returned items must match requested size or be OS/ONE SIZE."""
        results = search_listings("top", size="S", max_price=None)
        for item in results:
            assert item["size"].upper() in ("S", "OS", "ONE SIZE"), (
                f"{item['title']} has size {item['size']!r}, expected S or OS"
            )

    def test_no_size_filter_returns_more_results(self):
        """Removing size filter should return >= results vs. with filter."""
        without_size = search_listings("tee", size=None, max_price=None)
        with_size    = search_listings("tee", size="XXL", max_price=None)
        assert len(without_size) >= len(with_size)

    def test_result_items_have_required_fields(self):
        """Every returned listing must have all documented schema fields."""
        required_fields = {
            "id", "title", "description", "category", "style_tags",
            "size", "condition", "price", "colors", "brand", "platform",
        }
        results = search_listings("vintage", size=None, max_price=100)
        assert len(results) > 0
        for item in results:
            missing = required_fields - set(item.keys())
            assert not missing, f"Item {item.get('id')} missing fields: {missing}"

    def test_results_sorted_by_relevance(self):
        """Title matches should appear before body-only matches."""
        results = search_listings("hoodie", size=None, max_price=None)
        if results:
            titles = [r["title"].lower() for r in results[:3]]
            assert any("hoodie" in t for t in titles), (
                "Expected hoodie in title of top results"
            )

    def test_internal_score_key_not_exposed(self):
        """The _score key used for sorting must not be returned to callers."""
        results = search_listings("vintage tee", size=None, max_price=100)
        for item in results:
            assert "_score" not in item, "Internal _score key leaked into results"


class TestSuggestOutfitFailureModes:
    """
    suggest_outfit calls the LLM, so we test only the pure-Python guards here.
    LLM-dependent tests require a live API key and are documented separately.
    """

    def test_none_item_returns_error_string_not_exception(self):
        """None new_item → informative error string, not crash."""
        # We mock the LLM to avoid needing an API key in CI
        import unittest.mock as mock
        import tools as tools_module

        original = tools_module._llm
        tools_module._llm = mock.MagicMock(return_value="some outfit suggestion")
        try:
            from tools import suggest_outfit
            result = suggest_outfit(None, get_example_wardrobe())
            assert isinstance(result, str)
            assert len(result.strip()) > 0
            # Should be the guard message, not an LLM call
            assert "unavailable" in result.lower() or "no item" in result.lower()
        finally:
            tools_module._llm = original

    def test_empty_wardrobe_does_not_crash(self):
        """Empty wardrobe → general styling advice, not crash or empty string."""
        import unittest.mock as mock
        import tools as tools_module

        tools_module._llm = mock.MagicMock(
            return_value="Pair this with slim dark jeans and white sneakers."
        )
        try:
            from tools import suggest_outfit
            results = search_listings("vintage tee", size=None, max_price=50)
            assert len(results) > 0
            result = suggest_outfit(results[0], get_empty_wardrobe())
            assert isinstance(result, str)
            assert result.strip() != ""
        finally:
            # Restore original
            import importlib
            import tools
            importlib.reload(tools)

    def test_empty_wardrobe_calls_llm_with_adjusted_prompt(self):
        """With empty wardrobe, the LLM should be called (not skipped)."""
        import unittest.mock as mock
        import tools as tools_module

        mock_llm = mock.MagicMock(return_value="Great with slim jeans and white sneakers.")
        tools_module._llm = mock_llm
        try:
            from tools import suggest_outfit
            results = search_listings("vintage tee", size=None, max_price=50)
            suggest_outfit(results[0], get_empty_wardrobe())
            assert mock_llm.called, "LLM should be called even with empty wardrobe"
        finally:
            import importlib
            import tools
            importlib.reload(tools)


class TestCreateFitCardFailureModes:

    def test_empty_outfit_returns_error_string(self):
        """Empty outfit string → descriptive error, no LLM call, no crash."""
        from tools import create_fit_card
        results = search_listings("vintage tee", size=None, max_price=50)
        assert len(results) > 0
        result = create_fit_card("", results[0])
        assert isinstance(result, str)
        assert result.strip() != ""
        # Must be the guard message
        assert "can't" in result.lower() or "outfit" in result.lower()

    def test_whitespace_outfit_returns_error_string(self):
        """Whitespace-only outfit → same guard as empty string."""
        from tools import create_fit_card
        results = search_listings("vintage tee", size=None, max_price=50)
        result = create_fit_card("   ", results[0])
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_none_item_returns_error_string(self):
        """None new_item → error string, not crash."""
        from tools import create_fit_card
        result = create_fit_card("some outfit description", None)
        assert isinstance(result, str)
        assert result.strip() != ""

    def test_empty_outfit_does_not_call_llm(self):
        """Guard should fire before any LLM call when outfit is empty."""
        import unittest.mock as mock
        import tools as tools_module

        mock_llm = mock.MagicMock(return_value="caption text")
        tools_module._llm = mock_llm
        try:
            from tools import create_fit_card
            results = search_listings("tee", size=None, max_price=50)
            create_fit_card("", results[0])
            mock_llm.assert_not_called()
        finally:
            import importlib
            import tools
            importlib.reload(tools)


# =============================================================================
# ── LAYER 3: Stretch tool tests ───────────────────────────────────────────────
# =============================================================================

class TestComparePrice:

    def test_returns_dict_with_required_keys(self):
        required = {"verdict", "avg_comparable_price", "comparable_count",
                    "reasoning", "comparable_titles"}
        results = search_listings("vintage tee", size=None, max_price=100)
        assert len(results) > 0
        result = compare_price(results[0])
        assert isinstance(result, dict)
        missing = required - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_verdict_is_valid_value(self):
        results = search_listings("vintage tee", size=None, max_price=100)
        result = compare_price(results[0])
        assert result["verdict"] in ("steal", "fair", "overpriced", "unknown")

    def test_none_item_returns_unknown_not_exception(self):
        result = compare_price(None)
        assert result["verdict"] == "unknown"
        assert isinstance(result["reasoning"], str)

    def test_empty_dict_returns_unknown(self):
        result = compare_price({})
        assert result["verdict"] == "unknown"

    def test_reasoning_is_nonempty_string(self):
        results = search_listings("vintage", size=None, max_price=100)
        result = compare_price(results[0])
        assert isinstance(result["reasoning"], str)
        assert len(result["reasoning"]) > 0

    def test_comparable_count_is_nonnegative_int(self):
        results = search_listings("jacket", size=None, max_price=100)
        assert len(results) > 0
        result = compare_price(results[0])
        assert isinstance(result["comparable_count"], int)
        assert result["comparable_count"] >= 0


class TestSearchOutfitSet:

    def test_returns_required_keys(self):
        result = search_outfit_set("casual interview", 150.0)
        required = {"items", "total_price", "categories_filled",
                    "categories_missing", "budget_remaining", "fallback_applied"}
        assert required.issubset(set(result.keys()))

    def test_fills_tops_bottoms_shoes_under_budget(self):
        result = search_outfit_set(
            "casual summer internship interview", 150.0,
            required_categories=["tops", "bottoms", "shoes"],
            style_hints=["business casual"],
        )
        assert len(result["items"]) == 3
        assert result["total_price"] <= 150.0

    def test_4_category_outfit(self):
        result = search_outfit_set(
            "going out smart streetwear", 200.0,
            required_categories=["tops", "outerwear", "bottoms", "shoes"],
            style_hints=["streetwear"],
        )
        assert len(result["items"]) >= 3

    def test_fallback_triggered_on_impossible_size(self):
        result = search_outfit_set(
            "casual top", 200.0,
            required_categories=["tops"],
            size="XXXS",
        )
        # Should find something via fallback rather than returning nothing
        if result["items"]:
            assert result["fallback_applied"] is True

    def test_graceful_on_impossible_budget(self):
        """Very low budget should not crash — returns what it can."""
        result = search_outfit_set("luxury gown", 1.0)
        assert isinstance(result["items"], list)
        assert isinstance(result["fallback_applied"], bool)

    def test_budget_remaining_is_correct(self):
        result = search_outfit_set("casual", 200.0,
                                   required_categories=["tops", "bottoms"])
        expected_remaining = round(200.0 - result["total_price"], 2)
        assert abs(result["budget_remaining"] - expected_remaining) < 0.01


class TestStyleMemory:

    def setup_method(self):
        self.uid = f"pytest_{int(time.time() * 1000)}"

    def teardown_method(self):
        from utils.memory import _profile_path
        path = _profile_path(self.uid)
        if os.path.exists(path):
            os.remove(path)

    def test_new_user_has_empty_context(self):
        from utils.memory import get_profile_context, profile_exists
        assert not profile_exists(self.uid)
        assert get_profile_context(self.uid) == ""

    def test_save_and_reload_profile(self):
        from utils.memory import load_profile, save_profile
        profile = load_profile(self.uid)
        assert profile["user_id"] == self.uid
        save_profile(profile)
        reloaded = load_profile(self.uid)
        assert reloaded["user_id"] == self.uid

    def test_update_style_preferences_saves_to_disk(self):
        from utils.memory import update_style_preferences, load_profile
        update_style_preferences(self.uid, {
            "aesthetics": ["grunge", "streetwear"],
            "price_sensitivity": "budget",
        })
        profile = load_profile(self.uid)
        assert "grunge" in profile["style_preferences"]["aesthetics"]
        assert profile["style_preferences"]["price_sensitivity"] == "budget"

    def test_preferences_merge_across_sessions(self):
        """Second update merges with first — doesn't overwrite."""
        from utils.memory import update_style_preferences, load_profile
        update_style_preferences(self.uid, {"aesthetics": ["grunge"]})
        update_style_preferences(self.uid, {"aesthetics": ["streetwear"]})
        profile = load_profile(self.uid)
        prefs = profile["style_preferences"]["aesthetics"]
        assert "grunge" in prefs
        assert "streetwear" in prefs

    def test_record_interaction_increments_session_count(self):
        from utils.memory import record_interaction, load_profile
        item = {"title": "Test Tee", "price": 20,
                "platform": "Depop", "style_tags": ["vintage"]}
        record_interaction(self.uid, "vintage tee under $30", item, "success")
        profile = load_profile(self.uid)
        assert len(profile["interaction_history"]) == 1
        assert profile["total_sessions"] == 1

    def test_context_string_populated_after_preferences_saved(self):
        from utils.memory import update_style_preferences, record_interaction, get_profile_context
        update_style_preferences(self.uid, {
            "aesthetics": ["business casual"],
            "occasions": ["work", "interviews"],
        })
        item = {"title": "Oxford Shirt", "price": 35,
                "platform": "Poshmark", "style_tags": ["business casual"]}
        record_interaction(self.uid, "interview outfit", item, "success")
        ctx = get_profile_context(self.uid)
        assert len(ctx) > 0
        assert "business casual" in ctx

    def test_history_never_exceeds_20_entries(self):
        from utils.memory import record_interaction, load_profile
        item = {"title": "T", "price": 10, "platform": "Depop", "style_tags": []}
        for i in range(25):
            record_interaction(self.uid, f"query {i}", item)
        profile = load_profile(self.uid)
        assert len(profile["interaction_history"]) <= 20


# =============================================================================
# ── LAYER 4: Integration test (pure Python, no LLM needed) ───────────────────
# =============================================================================

class TestIntegrationFlow:

    def test_search_to_compare_price_pipeline(self):
        """
        search_listings → compare_price: output of Tool 1 flows directly
        into Tool 4 without user re-entry.
        """
        results = search_listings("vintage graphic tee", size="M", max_price=30)
        assert len(results) > 0, "Need results for integration test"

        item = results[0]
        verdict = compare_price(item)

        # The same item dict flows through unchanged
        assert verdict["verdict"] in ("steal", "fair", "overpriced", "unknown")
        assert isinstance(verdict["reasoning"], str)

    def test_error_path_never_reaches_fit_card(self):
        """
        Impossible query → search returns [] → create_fit_card with empty
        inputs returns error strings, not exceptions. This mirrors what the
        planning loop enforces: tools after a failed search are never called.
        """
        from tools import create_fit_card, suggest_outfit

        results = search_listings("designer ballgown", size="XXS", max_price=5)
        assert results == [], "Error path test requires zero results"

        # Simulate what would happen if the planning loop incorrectly continued
        outfit = suggest_outfit(None, get_example_wardrobe())
        assert isinstance(outfit, str)   # error string, not exception

        card = create_fit_card("", None)
        assert isinstance(card, str)     # error string, not exception
        assert "can't" in card.lower() or "outfit" in card.lower()

    def test_outfit_set_items_have_correct_categories(self):
        """search_outfit_set returns one item per requested category."""
        result = search_outfit_set(
            "business casual interview", 200.0,
            required_categories=["tops", "bottoms", "shoes"],
            style_hints=["business casual"],
        )
        returned_cats = {item["category"] for item in result["items"]}
        for cat in result["categories_filled"]:
            assert cat in returned_cats