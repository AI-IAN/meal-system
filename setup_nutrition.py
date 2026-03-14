#!/usr/bin/env python3
"""
Setup nutrition data from USDA FoodData Central API.

For each ingredient in the catalog, searches USDA for the best match,
stores cal/protein/fiber per 100g in the ingredients table.

Packaged items (Perfect Bars, frozen gyoza, etc.) use label data directly.

Usage:
  python3 setup_nutrition.py              # look up all ingredients
  python3 setup_nutrition.py --review     # show what's stored

Cost: Free (USDA API with DEMO_KEY, 1000 req/hr)
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote

DB_PATH = Path(__file__).parent / "data" / "meals.db"
BASE_URL = "https://api.nal.usda.gov/fdc/v1"

# Load API key from .env
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

API_KEY = os.environ.get("USDA_API_KEY", "DEMO_KEY")

# Nutrient IDs we care about
NUTRIENT_IDS = {
    1008: "cal_per_100g",    # Energy (kcal)
    1003: "protein_per_100g", # Protein (g)
    1079: "fiber_per_100g",   # Fiber, total dietary (g)
}

# -----------------------------------------------------------------------
# Manual overrides for packaged/branded items and tricky ingredients.
# Values are per 100g unless noted otherwise.
# For items with default_grams, we also set a typical portion.
# -----------------------------------------------------------------------
MANUAL_ENTRIES = {
    "perfect bars": {
        "cal_per_100g": 462, "protein_per_100g": 22.0, "fiber_per_100g": 4.6,
        "source": "label", "default_unit": "bar (68g)", "default_grams": 68,
        "note": "Perfect Bar Dark Chocolate Chip PB — 330 cal, 15g pro, 3g fiber per bar",
    },
    "frozen gyoza": {
        "cal_per_100g": 200, "protein_per_100g": 8.0, "fiber_per_100g": 1.5,
        "source": "label", "default_unit": "6 pieces (140g)", "default_grams": 140,
    },
    "frozen rice": {
        "cal_per_100g": 140, "protein_per_100g": 2.8, "fiber_per_100g": 0.4,
        "source": "label", "default_unit": "bag (250g)", "default_grams": 250,
    },
    "frozen stir-fry veg": {
        "cal_per_100g": 30, "protein_per_100g": 2.0, "fiber_per_100g": 2.5,
        "source": "label", "default_unit": "cup (150g)", "default_grams": 150,
    },
    "frozen chicken strips": {
        "cal_per_100g": 170, "protein_per_100g": 20.0, "fiber_per_100g": 1.0,
        "source": "label", "default_unit": "serving (112g)", "default_grams": 112,
    },
    "frozen edamame": {
        "cal_per_100g": 122, "protein_per_100g": 11.9, "fiber_per_100g": 5.2,
        "source": "label", "default_unit": "cup (155g)", "default_grams": 155,
    },
    "frozen udon": {
        "cal_per_100g": 130, "protein_per_100g": 3.5, "fiber_per_100g": 1.5,
        "source": "label", "default_unit": "portion (200g)", "default_grams": 200,
    },
    "bulgogi (pre-marinated)": {
        "cal_per_100g": 170, "protein_per_100g": 18.0, "fiber_per_100g": 0.5,
        "source": "label", "default_unit": "serving (113g)", "default_grams": 113,
    },
    "collagen powder": {
        "cal_per_100g": 360, "protein_per_100g": 90.0, "fiber_per_100g": 0.0,
        "source": "label", "default_unit": "scoop (11g)", "default_grams": 11,
    },
    "trail mix": {
        "cal_per_100g": 480, "protein_per_100g": 15.0, "fiber_per_100g": 5.0,
        "source": "label", "default_unit": "big handful (60g)", "default_grams": 60,
    },
    "everything bagel seasoning": {
        "cal_per_100g": 250, "protein_per_100g": 10.0, "fiber_per_100g": 8.0,
        "source": "label", "default_unit": "tsp (3g)", "default_grams": 3,
    },
    "curry simmer pouch": None,  # REMOVED — user doesn't like curry
    "golden curry blocks": None,  # REMOVED
    # Common ingredients — enter manually to avoid USDA rate limits
    "apples": {
        "cal_per_100g": 52, "protein_per_100g": 0.3, "fiber_per_100g": 2.4,
        "source": "usda", "default_unit": "medium apple (182g)", "default_grams": 182,
    },
    "fresh ginger": {
        "cal_per_100g": 80, "protein_per_100g": 1.8, "fiber_per_100g": 2.0,
        "source": "usda", "default_unit": "tbsp grated (6g)", "default_grams": 6,
    },
    "garlic": {
        "cal_per_100g": 149, "protein_per_100g": 6.4, "fiber_per_100g": 2.1,
        "source": "usda", "default_unit": "2 cloves (6g)", "default_grams": 6,
    },
    "granola": {
        "cal_per_100g": 471, "protein_per_100g": 10.0, "fiber_per_100g": 5.0,
        "source": "usda", "default_unit": "half cup (60g)", "default_grams": 60,
    },
    "green onions": {
        "cal_per_100g": 32, "protein_per_100g": 1.8, "fiber_per_100g": 2.6,
        "source": "usda", "default_unit": "2 stalks (30g)", "default_grams": 30,
    },
    "hemp hearts": {
        "cal_per_100g": 553, "protein_per_100g": 31.6, "fiber_per_100g": 4.0,
        "source": "usda", "default_unit": "3 tbsp (30g)", "default_grams": 30,
    },
    "honey": {
        "cal_per_100g": 304, "protein_per_100g": 0.3, "fiber_per_100g": 0.2,
        "source": "usda", "default_unit": "tbsp (21g)", "default_grams": 21,
    },
    "jasmine rice": {
        "cal_per_100g": 130, "protein_per_100g": 2.7, "fiber_per_100g": 0.4,
        "source": "usda", "default_unit": "cup cooked (186g)", "default_grams": 186,
    },
    "lemon": {
        "cal_per_100g": 29, "protein_per_100g": 1.1, "fiber_per_100g": 2.8,
        "source": "usda", "default_unit": "juice of half (30ml)", "default_grams": 30,
    },
    "mayonnaise": {
        "cal_per_100g": 680, "protein_per_100g": 1.0, "fiber_per_100g": 0.0,
        "source": "usda", "default_unit": "tbsp (15g)", "default_grams": 15,
    },
    "mirin": {
        "cal_per_100g": 241, "protein_per_100g": 0.0, "fiber_per_100g": 0.0,
        "source": "label", "default_unit": "tbsp (15ml)", "default_grams": 15,
    },
    "miso paste": {
        "cal_per_100g": 199, "protein_per_100g": 12.8, "fiber_per_100g": 5.4,
        "source": "usda", "default_unit": "tbsp (18g)", "default_grams": 18,
    },
    "mixed berries": {
        "cal_per_100g": 49, "protein_per_100g": 0.7, "fiber_per_100g": 3.0,
        "source": "usda", "default_unit": "cup (140g)", "default_grams": 140,
    },
    "mixed nuts": {
        "cal_per_100g": 594, "protein_per_100g": 17.2, "fiber_per_100g": 7.2,
        "source": "usda", "default_unit": "quarter cup (35g)", "default_grams": 35,
    },
    "oat milk": {
        "cal_per_100g": 43, "protein_per_100g": 1.0, "fiber_per_100g": 0.8,
        "source": "label", "default_unit": "cup (240ml)", "default_grams": 240,
    },
    "peanut butter": {
        "cal_per_100g": 588, "protein_per_100g": 25.1, "fiber_per_100g": 6.0,
        "source": "usda", "default_unit": "2 tbsp (32g)", "default_grams": 32,
    },
    "pork belly": {
        "cal_per_100g": 518, "protein_per_100g": 9.3, "fiber_per_100g": 0.0,
        "source": "usda", "default_unit": "serving (150g)", "default_grams": 150,
    },
    "rice vinegar": {
        "cal_per_100g": 18, "protein_per_100g": 0.0, "fiber_per_100g": 0.0,
        "source": "usda", "default_unit": "tbsp (15ml)", "default_grams": 15,
    },
    "sake": {
        "cal_per_100g": 134, "protein_per_100g": 0.5, "fiber_per_100g": 0.0,
        "source": "usda", "default_unit": "tbsp (15ml)", "default_grams": 15,
    },
    "salmon fillets": {
        "cal_per_100g": 208, "protein_per_100g": 20.4, "fiber_per_100g": 0.0,
        "source": "usda", "default_unit": "fillet (170g)", "default_grams": 170,
    },
    "sesame oil": {
        "cal_per_100g": 884, "protein_per_100g": 0.0, "fiber_per_100g": 0.0,
        "source": "usda", "default_unit": "tbsp (14g)", "default_grams": 14,
    },
    "silken tofu": {
        "cal_per_100g": 55, "protein_per_100g": 4.8, "fiber_per_100g": 0.0,
        "source": "usda", "default_unit": "half block (150g)", "default_grams": 150,
    },
    "soy sauce": {
        "cal_per_100g": 53, "protein_per_100g": 8.1, "fiber_per_100g": 0.8,
        "source": "usda", "default_unit": "tbsp (15ml)", "default_grams": 15,
    },
    "star anise": {
        "cal_per_100g": 337, "protein_per_100g": 17.6, "fiber_per_100g": 14.6,
        "source": "usda", "default_unit": "1 star (2g)", "default_grams": 2,
    },
    "tahini": {
        "cal_per_100g": 595, "protein_per_100g": 17.0, "fiber_per_100g": 9.3,
        "source": "usda", "default_unit": "2 tbsp (30g)", "default_grams": 30,
    },
    "vanilla extract": {
        "cal_per_100g": 288, "protein_per_100g": 0.1, "fiber_per_100g": 0.0,
        "source": "usda", "default_unit": "tsp (4ml)", "default_grams": 4,
    },
    "white miso": {
        "cal_per_100g": 199, "protein_per_100g": 12.8, "fiber_per_100g": 5.4,
        "source": "usda", "default_unit": "tbsp (18g)", "default_grams": 18,
    },
    "whole grain bread": {
        "cal_per_100g": 252, "protein_per_100g": 12.5, "fiber_per_100g": 6.0,
        "source": "usda", "default_unit": "2 slices (56g)", "default_grams": 56,
    },
    "protein of choice": {
        # Generic — use chicken breast as default
        "cal_per_100g": 165, "protein_per_100g": 31.0, "fiber_per_100g": 0.0,
        "source": "estimate", "default_unit": "serving (150g)", "default_grams": 150,
    },
    "dashi stock": {
        "cal_per_100g": 3, "protein_per_100g": 0.5, "fiber_per_100g": 0.0,
        "source": "label", "default_unit": "cup (240ml)", "default_grams": 240,
    },
    "wakame": {
        "cal_per_100g": 45, "protein_per_100g": 3.0, "fiber_per_100g": 0.5,
        "source": "usda", "default_unit": "tbsp dried (2g)", "default_grams": 2,
    },
}

# USDA search query overrides — better search terms for some ingredients
SEARCH_OVERRIDES = {
    "apples": "apple raw",
    "canned chickpeas": "chickpeas canned drained",
    "canned salmon": "salmon canned drained",
    "dark chocolate": "dark chocolate 70-85%",
    "dates": "dates medjool",
    "eggs": "egg whole raw",
    "firm tofu": "tofu firm raw",
    "full-fat yogurt": "yogurt whole milk plain",
    "hemp hearts": "hemp seed hulled",
    "jasmine rice": "rice white cooked",
    "mixed berries": "berries mixed frozen",
    "mixed nuts": "nuts mixed",
    "oat milk": "oat milk",
    "pork belly": "pork belly raw",
    "salmon fillets": "salmon atlantic raw",
    "silken tofu": "tofu soft silken",
    "white miso": "miso soybean",
    "whole grain bread": "bread whole wheat",
    "chia seeds": "chia seeds dried",
    "star anise": "anise seed",
}

# Default portions for USDA-sourced items (ingredient_name → (unit, grams))
DEFAULT_PORTIONS = {
    "apples": ("medium apple", 182),
    "canned chickpeas": ("cup", 240),
    "canned salmon": ("can (170g)", 170),
    "dark chocolate": ("2 squares (20g)", 20),
    "dates": ("2 dates", 48),
    "eggs": ("large egg", 50),
    "firm tofu": ("half block (200g)", 200),
    "fresh ginger": ("tbsp grated (6g)", 6),
    "full-fat yogurt": ("cup (245g)", 245),
    "garlic": ("2 cloves (6g)", 6),
    "granola": ("half cup (60g)", 60),
    "green onions": ("2 stalks (30g)", 30),
    "hemp hearts": ("3 tbsp (30g)", 30),
    "honey": ("tbsp (21g)", 21),
    "jasmine rice": ("cup cooked (186g)", 186),
    "lemon": ("half lemon juice (30ml)", 30),
    "mayonnaise": ("tbsp (15g)", 15),
    "mirin": ("tbsp (15ml)", 15),
    "miso paste": ("tbsp (18g)", 18),
    "mixed berries": ("cup (140g)", 140),
    "mixed nuts": ("quarter cup (35g)", 35),
    "oat milk": ("cup (240ml)", 240),
    "peanut butter": ("2 tbsp (32g)", 32),
    "pork belly": ("serving (150g)", 150),
    "rice vinegar": ("tbsp (15ml)", 15),
    "sake": ("tbsp (15ml)", 15),
    "salmon fillets": ("fillet (170g)", 170),
    "sesame oil": ("tbsp (14g)", 14),
    "silken tofu": ("half block (150g)", 150),
    "soy sauce": ("tbsp (15ml)", 15),
    "tahini": ("2 tbsp (30g)", 30),
    "vanilla extract": ("tsp (4ml)", 4),
    "white miso": ("tbsp (18g)", 18),
    "whole grain bread": ("2 slices (56g)", 56),
    "chia seeds": ("2 tbsp (24g)", 24),
    "star anise": ("1 star (2g)", 2),
}


def usda_search(query: str) -> dict | None:
    """Search USDA FoodData Central, return top SR Legacy result."""
    url = f"{BASE_URL}/foods/search?api_key={API_KEY}&query={quote(query)}&dataType=SR%20Legacy&pageSize=3"
    try:
        req = Request(url, headers={"User-Agent": "MealSystem/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        foods = data.get("foods", [])
        if foods:
            return foods[0]
    except Exception as e:
        print(f"    API error: {e}")
    return None


def extract_nutrients(food: dict) -> dict:
    """Extract cal/protein/fiber per 100g from USDA food item."""
    result = {"cal_per_100g": 0, "protein_per_100g": 0, "fiber_per_100g": 0}
    for n in food.get("foodNutrients", []):
        nid = n.get("nutrientId")
        if nid in NUTRIENT_IDS:
            result[NUTRIENT_IDS[nid]] = round(n.get("value", 0), 1)
    return result


def get_all_ingredients(conn: sqlite3.Connection) -> set[str]:
    """Get all unique ingredient names from catalog."""
    rows = conn.execute("SELECT ingredients FROM catalog").fetchall()
    ingredients = set()
    for row in rows:
        if row[0]:
            items = json.loads(row[0])
            ingredients.update(i.lower() for i in items)
    return ingredients


def main():
    review_only = "--review" in sys.argv
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Ensure ingredients table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            usda_fdc_id INTEGER,
            cal_per_100g REAL,
            protein_per_100g REAL,
            fiber_per_100g REAL,
            source TEXT DEFAULT 'usda',
            default_unit TEXT,
            default_grams REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    if review_only:
        rows = conn.execute(
            "SELECT name, cal_per_100g, protein_per_100g, fiber_per_100g, source, default_unit, default_grams "
            "FROM ingredients ORDER BY name"
        ).fetchall()
        print(f"\n{'Ingredient':<30} {'Cal/100g':>8} {'Pro/100g':>8} {'Fib/100g':>8} {'Source':<8} {'Portion':<25} {'Portion Cal':>10}")
        print("-" * 115)
        for r in rows:
            portion_cal = ""
            if r["default_grams"] and r["cal_per_100g"]:
                pc = r["cal_per_100g"] * r["default_grams"] / 100
                portion_cal = f"{pc:.0f} cal"
            unit = r["default_unit"] or "—"
            print(f"{r['name']:<30} {r['cal_per_100g'] or 0:>8.1f} {r['protein_per_100g'] or 0:>8.1f} {r['fiber_per_100g'] or 0:>8.1f} {r['source'] or 'usda':<8} {unit:<25} {portion_cal:>10}")
        conn.close()
        return

    all_ingredients = get_all_ingredients(conn)
    print(f"Found {len(all_ingredients)} unique ingredients in catalog\n")

    # Check which are already in DB
    existing = {
        r[0] for r in conn.execute("SELECT name FROM ingredients").fetchall()
    }

    new_count = 0
    skip_count = 0
    fail_count = 0

    for ing in sorted(all_ingredients):
        if ing in existing:
            print(f"  SKIP {ing} (already in DB)")
            skip_count += 1
            continue

        # Check manual overrides first
        if ing in MANUAL_ENTRIES:
            entry = MANUAL_ENTRIES[ing]
            if entry is None:
                print(f"  SKIP {ing} (removed)")
                skip_count += 1
                continue
            conn.execute(
                "INSERT OR REPLACE INTO ingredients (name, cal_per_100g, protein_per_100g, fiber_per_100g, source, default_unit, default_grams) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ing, entry["cal_per_100g"], entry["protein_per_100g"], entry["fiber_per_100g"],
                 entry.get("source", "label"), entry.get("default_unit"), entry.get("default_grams")),
            )
            print(f"  MANUAL {ing}: {entry['cal_per_100g']} cal | {entry['protein_per_100g']}g pro | {entry['fiber_per_100g']}g fib")
            new_count += 1
            continue

        # USDA search
        search_term = SEARCH_OVERRIDES.get(ing, ing)
        print(f"  USDA  {ing} (searching '{search_term}')...", end=" ", flush=True)
        food = usda_search(search_term)
        if not food:
            print("NOT FOUND")
            fail_count += 1
            continue

        nutrients = extract_nutrients(food)
        fdc_id = food.get("fdcId")
        desc = food.get("description", "")

        # Get default portion if defined
        portion = DEFAULT_PORTIONS.get(ing, (None, None))
        default_unit, default_grams = portion

        conn.execute(
            "INSERT OR REPLACE INTO ingredients (name, usda_fdc_id, cal_per_100g, protein_per_100g, fiber_per_100g, source, default_unit, default_grams) "
            "VALUES (?, ?, ?, ?, ?, 'usda', ?, ?)",
            (ing, fdc_id, nutrients["cal_per_100g"], nutrients["protein_per_100g"], nutrients["fiber_per_100g"],
             default_unit, default_grams),
        )
        print(f"→ {desc[:40]} | {nutrients['cal_per_100g']} cal | {nutrients['protein_per_100g']}g pro | {nutrients['fiber_per_100g']}g fib")
        new_count += 1

        time.sleep(3)  # DEMO_KEY allows ~30 req/hr; space them out

    conn.commit()
    print(f"\nDone: {new_count} added, {skip_count} skipped, {fail_count} failed")

    # Show full review
    print("\n" + "=" * 60)
    print("Running --review to show all stored data:")
    print("=" * 60)
    conn.close()

    # Re-run in review mode
    sys.argv.append("--review")
    main()


if __name__ == "__main__":
    main()
