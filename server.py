#!/usr/bin/env python3
"""
Meal System — FastAPI backend.

Serves static files, SQLite CRUD for meals/pantry/plan/history,
deterministic suggestions, and intent detection.

All data in data/meals.db (catalog, pantry, plan, meal_log, shop_log,
pantry_snapshots, llm_calls).

Usage:
  python3 server.py          # runs on port 8081
  uvicorn server:app --host 0.0.0.0 --port 8081 --reload

Access from phone via Tailscale: http://<macbook-tailscale-hostname>:8081
"""

import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
for env_path in [BASE_DIR / ".env", Path.home() / "ij/career/job-tailor/.env"]:
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        break
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "meals.db"

# Reference data (non-catalog extras from meals.json)
REFERENCE_PATH = DATA_DIR / "meals_reference.json"

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LogMealRequest(BaseModel):
    meal_name: str
    energy_level: Optional[str] = None
    notes: Optional[str] = None


class ParseInputRequest(BaseModel):
    text: str


class ParseInputResponse(BaseModel):
    intent: str
    confidence: float
    entities: dict = {}


class Suggestion(BaseModel):
    meal_name: str
    score: float
    reasons: list[str]
    ingredients_available: list[str]
    ingredients_missing: list[str]


# ---------------------------------------------------------------------------
# SQLite setup
# ---------------------------------------------------------------------------

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
    fiber TEXT,
    time TEXT,
    ingredients TEXT,
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


async def init_db():
    """Create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def db_fetch_all(query: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT and return list of dicts."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def db_fetch_one(query: str, params: tuple = ()) -> Optional[dict]:
    """Execute a SELECT and return one dict or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
        return dict(row) if row else None


async def db_execute(query: str, params: tuple = ()):
    """Execute a write query (INSERT/UPDATE/DELETE)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()


async def db_executemany(query: str, params_list: list[tuple]):
    """Execute a write query for many rows."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(query, params_list)
        await db.commit()


# ---------------------------------------------------------------------------
# JSON helpers (only for shop.json and reference data now)
# ---------------------------------------------------------------------------

def read_json(filename: str) -> any:
    """Read a JSON file from data/, return None if missing."""
    path = DATA_DIR / filename
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_json(filename: str, data: any) -> None:
    """Write data as JSON to data/."""
    path = DATA_DIR / filename
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Ollama stub (Phase 4 wiring)
# ---------------------------------------------------------------------------

async def ask_local(prompt: str, system: str = "", model: str = "qwen3.5:latest") -> Optional[str]:
    """Call Qwen via Ollama. Returns parsed response or None on failure."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 256},
                },
                timeout=15,
            )
            return resp.json()["response"]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lifespan (DB init)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    print(f"Meal System → http://localhost:8081")
    print(f"Tailscale   → http://<your-macbook-hostname>:8081")
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Meal System", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Catalog (meals) API — from SQLite catalog table
# ---------------------------------------------------------------------------

def _catalog_row_to_meal(row: dict) -> dict:
    """Convert a catalog DB row to the frontend meal dict format."""
    return {
        "id": row["id"],
        "name": row["name"],
        "energy": row.get("energy"),
        "cal": row.get("calories"),
        "protein": row.get("protein"),
        "fiber": row.get("fiber"),
        "time": row.get("time"),
        "ingredients": json.loads(row["ingredients"]) if row.get("ingredients") else [],
        "note": row.get("notes"),
    }


@app.get("/api/meals")
async def get_meals():
    """Return full meals data: catalog from DB + reference data from JSON."""
    rows = await db_fetch_all("SELECT * FROM catalog ORDER BY name")
    meals = [_catalog_row_to_meal(r) for r in rows]

    # Merge in reference data (shopping_suggestions, meal_unlocks, etc.)
    result = {"meals": meals}
    if REFERENCE_PATH.exists():
        try:
            ref = json.loads(REFERENCE_PATH.read_text())
            result.update(ref)
        except Exception:
            pass
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Pantry API — from SQLite pantry table
# ---------------------------------------------------------------------------

@app.get("/api/pantry")
async def get_pantry():
    rows = await db_fetch_all("SELECT id, name, qty, category FROM pantry ORDER BY category, name")
    return JSONResponse(rows)


@app.post("/api/pantry")
async def save_pantry(request: Request):
    """Replace entire pantry with provided list."""
    body = await request.json()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pantry")
        for item in body:
            if isinstance(item, dict):
                await db.execute(
                    "INSERT INTO pantry (name, qty, category) VALUES (?, ?, ?)",
                    (item.get("name", ""), item.get("qty", ""), item.get("category")),
                )
            elif isinstance(item, str):
                await db.execute(
                    "INSERT INTO pantry (name, qty) VALUES (?, ?)",
                    (item, "1"),
                )
        # Take a pantry snapshot
        count = (await (await db.execute("SELECT COUNT(*) FROM pantry")).fetchone())[0]
        low = (await (await db.execute("SELECT COUNT(*) FROM pantry WHERE qty = 'low'")).fetchone())[0]
        await db.execute(
            "INSERT OR REPLACE INTO pantry_snapshots (item_count, low_count, snapshot_date) "
            "VALUES (?, ?, date('now'))",
            (count, low),
        )
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Plan API — from SQLite plan table
# ---------------------------------------------------------------------------

@app.get("/api/plan")
async def get_plan():
    rows = await db_fetch_all("SELECT meal_name, day, slot, week_of FROM plan ORDER BY id")
    # Return in the same shape the frontend expects: {"meals": [...]}
    meals = [r["meal_name"] for r in rows]
    return JSONResponse({"meals": meals})


@app.post("/api/plan")
async def save_plan(request: Request):
    """Replace plan with provided data."""
    body = await request.json()
    planned_meals = body.get("meals", []) if isinstance(body, dict) else []
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM plan")
        for entry in planned_meals:
            if isinstance(entry, str):
                await db.execute("INSERT INTO plan (meal_name) VALUES (?)", (entry,))
            elif isinstance(entry, dict):
                await db.execute(
                    "INSERT INTO plan (meal_name, day, slot) VALUES (?, ?, ?)",
                    (entry.get("name", ""), entry.get("day"), entry.get("slot")),
                )
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Shopping list CRUD (still JSON — no migration needed)
# ---------------------------------------------------------------------------

@app.get("/api/shop")
async def get_shop():
    data = read_json("shop.json")
    return JSONResponse(data if data is not None else [])


@app.post("/api/shop")
async def save_shop(request: Request):
    body = await request.json()
    write_json("shop.json", body)
    return {"ok": True}


@app.get("/api/shop/auto")
async def auto_shop():
    """Generate shopping list from low pantry items + planned meal ingredients."""
    pantry = await db_fetch_all("SELECT name, qty FROM pantry")
    plan_rows = await db_fetch_all("SELECT meal_name FROM plan")
    catalog = await db_fetch_all("SELECT name, ingredients FROM catalog")

    items = []
    seen = set()

    # Low pantry items
    for p in pantry:
        if p["qty"] in ("low", "0"):
            name = p["name"]
            if name.lower() not in seen:
                items.append({"name": name, "reason": "running low", "checked": False})
                seen.add(name.lower())

    # Planned meal ingredients not in pantry
    pantry_names = {p["name"].lower() for p in pantry}
    catalog_by_name = {m["name"].lower(): m for m in catalog}
    for row in plan_rows:
        meal = catalog_by_name.get(row["meal_name"].lower())
        if meal:
            ingredients = json.loads(meal["ingredients"]) if meal["ingredients"] else []
            for ing in ingredients:
                if ing.lower() not in pantry_names and ing.lower() not in seen:
                    items.append({"name": ing, "reason": f"for {row['meal_name']}", "checked": False})
                    seen.add(ing.lower())

    return items


# ---------------------------------------------------------------------------
# Legacy backward-compat: GET/POST /data/{filename}
# These now proxy to the SQLite-backed endpoints where applicable.
# ---------------------------------------------------------------------------

ALLOWED_LEGACY_FILES = {"ms2.json", "plan.json", "pantry.json"}


@app.get("/data/{filename}")
async def legacy_get_data(filename: str):
    if filename not in ALLOWED_LEGACY_FILES:
        raise HTTPException(400, "File not allowed")
    if filename == "pantry.json":
        return await get_pantry()
    elif filename == "plan.json":
        return await get_plan()
    # ms2.json — try reading from disk (might not exist anymore)
    data = read_json(filename)
    return JSONResponse(data) if data is not None else JSONResponse(None)


@app.post("/data/{filename}")
async def legacy_post_data(filename: str, request: Request):
    if filename not in ALLOWED_LEGACY_FILES:
        raise HTTPException(400, "File not allowed")
    if filename == "pantry.json":
        return await save_pantry(request)
    elif filename == "plan.json":
        return await save_plan(request)
    body = await request.json()
    write_json(filename, body)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Meal logging + pantry depletion
# ---------------------------------------------------------------------------

@app.post("/api/log-meal")
async def log_meal(req: LogMealRequest):
    """Log a meal to history and deplete pantry ingredients."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Insert into meal_log
        await db.execute(
            "INSERT INTO meal_log (meal_name, energy_level, notes) VALUES (?, ?, ?)",
            (req.meal_name, req.energy_level, req.notes),
        )

        # Look up meal ingredients from catalog
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT ingredients FROM catalog WHERE LOWER(name) = LOWER(?)",
            (req.meal_name,),
        )
        row = await cursor.fetchone()

        if row and row["ingredients"]:
            meal_ingredients = {i.lower() for i in json.loads(row["ingredients"])}

            # Get pantry items that match
            cursor = await db.execute("SELECT id, name, qty FROM pantry")
            pantry_rows = await cursor.fetchall()
            for p in pantry_rows:
                if p["name"].lower() in meal_ingredients:
                    # Mark as low (conservative depletion)
                    qty = p["qty"]
                    if qty not in ("low", "0"):
                        await db.execute(
                            "UPDATE pantry SET qty = 'low', updated_at = datetime('now') WHERE id = ?",
                            (p["id"],),
                        )

        await db.commit()

    return {"ok": True, "meal_name": req.meal_name}


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@app.get("/api/history")
async def get_history():
    """Return meal history for the last 30 days."""
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    return await db_fetch_all(
        "SELECT id, meal_name, logged_at, energy_level, notes "
        "FROM meal_log WHERE logged_at >= ? ORDER BY logged_at DESC",
        (cutoff,),
    )


@app.delete("/api/history/{entry_id}")
async def delete_history(entry_id: int):
    """Delete a meal log entry by ID."""
    await db_execute("DELETE FROM meal_log WHERE id = ?", (entry_id,))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Suggestions engine (deterministic)
# ---------------------------------------------------------------------------

@app.get("/api/suggestions")
async def get_suggestions(energy: Optional[str] = None):
    """
    Deterministic "what can I make?" logic.
    Score = pantry match % + days-since-last-eaten bonus + energy filter.
    """
    catalog = await db_fetch_all("SELECT * FROM catalog")
    pantry = await db_fetch_all("SELECT name FROM pantry")

    if not catalog:
        return []

    pantry_names = {p["name"].lower() for p in pantry}

    # Get recent meal history for rotation scoring
    last_eaten: dict[str, datetime] = {}
    try:
        rows = await db_fetch_all(
            "SELECT meal_name, MAX(logged_at) as last_at FROM meal_log GROUP BY meal_name"
        )
        for row in rows:
            try:
                last_eaten[row["meal_name"].lower()] = datetime.fromisoformat(row["last_at"])
            except (ValueError, TypeError):
                pass
    except Exception:
        pass

    now = datetime.now()
    results: list[dict] = []

    for meal in catalog:
        name = meal["name"]
        ingredients = json.loads(meal["ingredients"]) if meal.get("ingredients") else []
        ingredients_lower = [i.lower() for i in ingredients]
        meal_energy = (meal.get("energy") or "").lower()

        if energy and meal_energy and meal_energy != energy.lower():
            continue
        if not ingredients_lower:
            continue

        available = [i for i in ingredients_lower if i in pantry_names]
        missing = [i for i in ingredients_lower if i not in pantry_names]
        match_pct = len(available) / len(ingredients_lower)

        last = last_eaten.get(name.lower())
        days_since = (now - last).days if last else 14
        rotation_bonus = min(days_since, 14) / 14

        score = round(match_pct * 0.6 + rotation_bonus * 0.4, 3)

        reasons = []
        if match_pct == 1.0:
            reasons.append("You have everything you need")
        elif match_pct >= 0.5:
            reasons.append(f"You have {len(available)} of {len(ingredients_lower)} ingredients")
        else:
            reasons.append(f"Missing {len(missing)} ingredients")

        if last:
            if days_since == 0:
                reasons.append("You had this today")
            elif days_since == 1:
                reasons.append("You had this yesterday")
            elif days_since <= 7:
                reasons.append(f"Last had {days_since} days ago")
            else:
                reasons.append("You haven't had this in a while")
        else:
            reasons.append("Haven't tried this yet — give it a go!")

        results.append(
            Suggestion(
                meal_name=name,
                score=score,
                reasons=reasons,
                ingredients_available=available,
                ingredients_missing=missing,
            ).model_dump()
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Intent detection (regex/keyword first, Qwen stub for Phase 4)
# ---------------------------------------------------------------------------

INTENT_PATTERNS = [
    (r"^i\s+(made|ate|had|cooked|finished)\s+(.+)", "log_meal"),
    (r"^just\s+(ate|had|made)\s+(.+)", "log_meal"),
    (r"^(?:bought|got|picked up|grabbed)\s+(.+)", "pantry_add"),
    (r"^(.+,\s*.+)$", "pantry_add"),
    (r"^(?:need|buy|get|add to list)\s+(.+)", "shop_add"),
    (r"\b(quick|easy|fast|simple|light)\b", "energy_filter"),
    (r"\b(hearty|filling|big|heavy)\b", "energy_filter"),
    (r"^(?:what|what's|whats)\s+(?:for|should|can)\b", "suggest"),
    (r"^(?:surprise me|something|anything)", "suggest"),
]


@app.post("/api/parse-input")
async def parse_input(req: ParseInputRequest):
    text = req.text.strip()
    if not text:
        return ParseInputResponse(intent="empty", confidence=1.0)

    for pattern, intent in INTENT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            entities: dict = {}
            if intent == "log_meal":
                entities["meal"] = match.group(match.lastindex).strip()
            elif intent == "pantry_add":
                raw = match.group(match.lastindex).strip()
                entities["items"] = [i.strip() for i in raw.split(",") if i.strip()]
            elif intent == "shop_add":
                raw = match.group(match.lastindex).strip()
                entities["items"] = [i.strip() for i in raw.split(",") if i.strip()]
            elif intent == "energy_filter":
                word = match.group(1).lower()
                if word in ("quick", "easy", "fast", "simple", "light"):
                    entities["energy"] = "low"
                else:
                    entities["energy"] = "high"
            return ParseInputResponse(
                intent=intent, confidence=0.9, entities=entities
            )

    return ParseInputResponse(intent="unknown", confidence=0.0, entities={})


# ---------------------------------------------------------------------------
# Static files — mount LAST so API routes take priority
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="static")

# ---------------------------------------------------------------------------
# Run with: python3 server.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
