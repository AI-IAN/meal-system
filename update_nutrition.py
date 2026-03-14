#!/usr/bin/env python3
"""
One-time script: update catalog nutrition (cal, protein, fiber) using Haiku.

Reads each meal from meals.db, sends name + ingredients to Haiku,
writes back accurate integer values. Prints before/after for review.

Usage:
  python3 update_nutrition.py              # dry-run (print only)
  python3 update_nutrition.py --apply      # write to DB

Cost: ~22 meals × Haiku ≈ $0.01
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

# Load API key from .env
for env_path in [Path(__file__).parent / ".env", Path.home() / "ij/career/job-tailor/.env"]:
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        break

import anthropic

DB_PATH = Path(__file__).parent / "data" / "meals.db"

# New meals to add before running nutrition
NEW_MEALS = [
    {
        "id": "chia-pudding",
        "name": "Chia Pudding",
        "energy": "low",
        "time": "5 min + overnight",
        "ingredients": json.dumps(["chia seeds", "oat milk", "honey", "vanilla extract"]),
        "notes": "Mix chia + milk + sweetener night before. Top with fruit/nuts in morning.",
    },
    {
        "id": "yogurt-parfait",
        "name": "Yogurt Parfait",
        "energy": "low",
        "time": "3 min",
        "ingredients": json.dumps(["full-fat yogurt", "granola", "mixed berries", "honey"]),
        "notes": "Layer yogurt, granola, berries. Drizzle honey.",
    },
    {
        "id": "tofu-rice-bowl",
        "name": "Crispy Tofu Rice Bowl",
        "energy": "medium",
        "time": "20 min",
        "ingredients": json.dumps(["firm tofu", "frozen rice", "soy sauce", "sesame oil", "frozen stir-fry veg"]),
        "notes": "Press tofu, cube, pan-fry until crispy. Serve over rice with veg and sauce.",
    },
    {
        "id": "silken-tofu-miso-soup",
        "name": "Silken Tofu Miso Soup",
        "energy": "low",
        "time": "10 min",
        "ingredients": json.dumps(["silken tofu", "miso paste", "dashi stock", "wakame", "green onions"]),
        "notes": "Bring dashi to simmer, add wakame + cubed silken tofu, kill heat, stir in miso. Top with scallions.",
    },
    {
        "id": "apple-snack",
        "name": "Apple (nightly)",
        "energy": "low",
        "time": "0 min",
        "ingredients": json.dumps(["apples"]),
        "notes": "Nightly snack. Sometimes with a bit of PB.",
    },
]


SYSTEM_PROMPT = """\
You are a sports nutritionist. Given a meal name, its ingredients, and any notes, \
estimate realistic nutrition for ONE serving eaten by a 6'2" 185 lb adult male at home.

Rules:
- Portions should be realistic and generous (home cooking, not restaurant, not diet)
- For snacks, estimate a typical snack portion (e.g., a big handful of trail mix, 2-3 squares of dark chocolate)
- For "batch" items like jammy eggs, estimate per-egg values
- Return ONLY a JSON object: {"calories": int, "protein_g": int, "fiber_g": int}
- Use integers only, no ranges, no tildes
- Be accurate — don't round to nearest 50 or 100. Real estimates.
- Calories 50-1200, protein 0-60g, fiber 0-25g per serving
- No explanation, no markdown, just the JSON object"""


def get_nutrition(client, meal_name: str, ingredients: list[str], notes: str) -> dict:
    """Call Haiku to estimate nutrition for a meal."""
    user_msg = f"Meal: {meal_name}\nIngredients: {', '.join(ingredients)}"
    if notes:
        user_msg += f"\nNotes: {notes}"

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = resp.content[0].text.strip()
    # Parse JSON from response (handle possible markdown wrapping)
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)

    # Handle inconsistent key names from LLM
    def find_key(d, candidates):
        for k in candidates:
            if k in d:
                return d[k]
        raise KeyError(f"None of {candidates} found in {d}")

    cal = int(find_key(data, ["calories", "cal", "kcal"]))
    protein = int(find_key(data, ["protein_g", "protein", "protein_grams"]))
    fiber = int(find_key(data, ["fiber_g", "fiber", "fiber_grams"]))
    assert 50 <= cal <= 1200, f"calories {cal} out of range"
    assert 0 <= protein <= 60, f"protein {protein} out of range"
    assert 0 <= fiber <= 25, f"fiber {fiber} out of range"

    return {"calories": cal, "protein_g": protein, "fiber_g": fiber}


def main():
    apply = "--apply" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Step 2: Insert new meals if they don't exist
    for meal in NEW_MEALS:
        existing = conn.execute("SELECT id FROM catalog WHERE id = ?", (meal["id"],)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO catalog (id, name, energy, time, ingredients, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (meal["id"], meal["name"], meal["energy"], meal["time"], meal["ingredients"], meal["notes"]),
            )
            print(f"  + Added new meal: {meal['name']}")
    conn.commit()

    # Fetch all meals
    rows = conn.execute("SELECT id, name, calories, protein, fiber, ingredients, notes FROM catalog ORDER BY name").fetchall()

    client = anthropic.Anthropic()

    print(f"\n{'Meal':<35} {'Old Cal':<10} {'Old Pro':<10} {'New Cal':<10} {'New Pro':<10} {'New Fib':<10}")
    print("-" * 95)

    updates = []
    for row in rows:
        ingredients = json.loads(row["ingredients"]) if row["ingredients"] else []
        notes = row["notes"] or ""

        try:
            result = get_nutrition(client, row["name"], ingredients, notes)
        except Exception as e:
            print(f"  ERROR for {row['name']}: {e}")
            continue

        old_cal = row["calories"] or "—"
        old_pro = row["protein"] or "—"
        new_cal = str(result["calories"])
        new_pro = f"{result['protein_g']}g"
        new_fib = f"{result['fiber_g']}g"

        print(f"{row['name']:<35} {old_cal:<10} {old_pro:<10} {new_cal:<10} {new_pro:<10} {new_fib:<10}")

        updates.append((str(result["calories"]), f"{result['protein_g']}g", f"{result['fiber_g']}g", row["id"]))

    if apply:
        print(f"\nWriting {len(updates)} updates to DB...")
        for cal, pro, fib, meal_id in updates:
            conn.execute(
                "UPDATE catalog SET calories = ?, protein = ?, fiber = ?, updated_at = datetime('now') WHERE id = ?",
                (cal, pro, fib, meal_id),
            )
        conn.commit()
        print("Done.")
    else:
        print(f"\nDry run — {len(updates)} meals would be updated. Run with --apply to write.")

    conn.close()


if __name__ == "__main__":
    main()
