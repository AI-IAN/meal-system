#!/usr/bin/env python3
"""
Migrate meal system JSON files + meal_history.db → unified meals.db

Sources:
  data/meals.json   → catalog table (meals list only, other keys stay in JSON)
  data/pantry.json  → pantry table
  data/plan.json    → plan table
  data/meal_history.db meal_log → meals.db meal_log (copy rows)
  data/meal_history.db shop_log → meals.db shop_log (copy rows)

Also creates: pantry_snapshots, llm_calls tables (empty)
Renames originals to .bak after successful migration.
"""

import json
import shutil
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
NEW_DB = DATA_DIR / "meals.db"
OLD_DB = DATA_DIR / "meal_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_name TEXT NOT NULL,
    logged_at TEXT DEFAULT (datetime('now')),
    energy_level TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS shop_log (
    id INTEGER PRIMARY KEY,
    items_json TEXT NOT NULL,
    store TEXT,
    logged_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS catalog (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    energy TEXT,
    calories TEXT,
    protein TEXT,
    time TEXT,
    ingredients TEXT,  -- JSON array
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pantry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    qty TEXT NOT NULL,
    category TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_name TEXT NOT NULL,
    day TEXT,
    slot TEXT,
    week_of TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pantry_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_count INTEGER,
    low_count INTEGER,
    snapshot_date TEXT DEFAULT (date('now')),
    UNIQUE(snapshot_date)
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_cents REAL,
    duration_ms INTEGER,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def migrate():
    if NEW_DB.exists():
        print(f"ERROR: {NEW_DB} already exists. Remove it first to re-migrate.")
        return False

    conn = sqlite3.connect(NEW_DB)
    conn.executescript(SCHEMA)
    print("Created meals.db with schema")

    # --- Catalog (from meals.json) ---
    meals_path = DATA_DIR / "meals.json"
    if meals_path.exists():
        data = json.loads(meals_path.read_text())
        meals = data.get("meals", []) if isinstance(data, dict) else data
        for m in meals:
            conn.execute(
                "INSERT OR IGNORE INTO catalog (id, name, energy, calories, protein, time, ingredients, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    m.get("id", m.get("name", "").lower().replace(" ", "-")),
                    m.get("name", ""),
                    m.get("energy"),
                    m.get("cal"),
                    m.get("protein"),
                    m.get("time"),
                    json.dumps(m.get("ingredients", [])),
                    m.get("note"),
                ),
            )
        conn.commit()
        print(f"  catalog: {len(meals)} meals migrated")

        # Save non-meal keys back to a reference JSON (shopping_suggestions, etc.)
        if isinstance(data, dict):
            extra_keys = {k: v for k, v in data.items() if k != "meals"}
            if extra_keys:
                ref_path = DATA_DIR / "meals_reference.json"
                ref_path.write_text(json.dumps(extra_keys, indent=2, ensure_ascii=False))
                print(f"  saved non-meal data to {ref_path.name}: {list(extra_keys.keys())}")
    else:
        print("  meals.json not found, skipping catalog")

    # --- Pantry (from pantry.json) ---
    pantry_path = DATA_DIR / "pantry.json"
    if pantry_path.exists():
        items = json.loads(pantry_path.read_text())
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    conn.execute(
                        "INSERT INTO pantry (name, qty, category) VALUES (?, ?, ?)",
                        (item.get("name", ""), item.get("qty", ""), item.get("category")),
                    )
                elif isinstance(item, str):
                    conn.execute(
                        "INSERT INTO pantry (name, qty) VALUES (?, ?)",
                        (item, "1"),
                    )
            conn.commit()
            print(f"  pantry: {len(items)} items migrated")
    else:
        print("  pantry.json not found, skipping pantry")

    # --- Plan (from plan.json) ---
    plan_path = DATA_DIR / "plan.json"
    if plan_path.exists():
        plan_data = json.loads(plan_path.read_text())
        planned_meals = plan_data.get("meals", []) if isinstance(plan_data, dict) else []
        count = 0
        for entry in planned_meals:
            if isinstance(entry, str):
                conn.execute("INSERT INTO plan (meal_name) VALUES (?)", (entry,))
                count += 1
            elif isinstance(entry, dict):
                conn.execute(
                    "INSERT INTO plan (meal_name, day, slot) VALUES (?, ?, ?)",
                    (entry.get("name", ""), entry.get("day"), entry.get("slot")),
                )
                count += 1
        conn.commit()
        print(f"  plan: {count} entries migrated")
    else:
        print("  plan.json not found, skipping plan")

    # --- Copy meal_log + shop_log from meal_history.db ---
    if OLD_DB.exists():
        old = sqlite3.connect(OLD_DB)
        meal_rows = old.execute("SELECT meal_name, logged_at, energy_level, notes FROM meal_log").fetchall()
        for row in meal_rows:
            conn.execute(
                "INSERT INTO meal_log (meal_name, logged_at, energy_level, notes) VALUES (?, ?, ?, ?)",
                row,
            )

        shop_rows = old.execute("SELECT items_json, store, logged_at FROM shop_log").fetchall()
        for row in shop_rows:
            conn.execute(
                "INSERT INTO shop_log (items_json, store, logged_at) VALUES (?, ?, ?)",
                row,
            )
        conn.commit()
        old.close()
        print(f"  meal_log: {len(meal_rows)} rows copied from meal_history.db")
        print(f"  shop_log: {len(shop_rows)} rows copied from meal_history.db")
    else:
        print("  meal_history.db not found, skipping history copy")

    # --- Initial pantry snapshot ---
    pantry_count = conn.execute("SELECT COUNT(*) FROM pantry").fetchone()[0]
    low_count = conn.execute("SELECT COUNT(*) FROM pantry WHERE qty = 'low'").fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO pantry_snapshots (item_count, low_count) VALUES (?, ?)",
        (pantry_count, low_count),
    )
    conn.commit()
    print(f"  pantry snapshot: {pantry_count} items, {low_count} low")

    conn.close()

    # --- Verify ---
    print("\n--- Verification ---")
    conn = sqlite3.connect(NEW_DB)
    for table in ["catalog", "pantry", "plan", "meal_log", "shop_log", "pantry_snapshots"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} rows")
    conn.close()

    # --- Rename originals to .bak ---
    for f in ["meals.json", "pantry.json", "plan.json"]:
        src = DATA_DIR / f
        if src.exists():
            dst = DATA_DIR / (f + ".bak")
            shutil.move(str(src), str(dst))
            print(f"  {f} → {f}.bak")

    if OLD_DB.exists():
        dst = DATA_DIR / "meal_history.db.bak"
        shutil.move(str(OLD_DB), str(dst))
        print(f"  meal_history.db → meal_history.db.bak")

    print("\nMigration complete!")
    return True


if __name__ == "__main__":
    migrate()
