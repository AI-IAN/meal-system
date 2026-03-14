#!/usr/bin/env python3
"""
Meal System — FastAPI backend.

Serves static files, JSON CRUD for meals/pantry/plan,
SQLite history (meal_log, shop_log), deterministic suggestions,
and intent detection.

Usage:
  python3 server.py          # runs on port 8081
  uvicorn server:app --host 0.0.0.0 --port 8081 --reload

Access from phone via Tailscale: http://<macbook-tailscale-hostname>:8081
"""

import json
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from difflib import get_close_matches
from pathlib import Path
from typing import Optional

import aiosqlite
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "meal_history.db"

ALLOWED_LEGACY_FILES = {"ms2.json", "plan.json", "pantry.json"}

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

async def init_db():
    """Create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS meal_log (
                id INTEGER PRIMARY KEY,
                meal_name TEXT NOT NULL,
                logged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                energy_level TEXT,
                notes TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shop_log (
                id INTEGER PRIMARY KEY,
                items_json TEXT NOT NULL,
                store TEXT,
                logged_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


# ---------------------------------------------------------------------------
# Ollama stub (Phase 4 wiring)
# ---------------------------------------------------------------------------

async def ask_local(prompt: str, system: str = "", model: str = "qwen3:5.9b") -> Optional[str]:
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
        return None  # caller falls back to Haiku or returns error


# ---------------------------------------------------------------------------
# JSON helpers
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
# JSON CRUD — new API
# ---------------------------------------------------------------------------

@app.get("/api/meals")
async def get_meals():
    data = read_json("meals.json")
    return JSONResponse(data if data is not None else [])


@app.get("/api/pantry")
async def get_pantry():
    data = read_json("pantry.json")
    return JSONResponse(data if data is not None else [])


@app.post("/api/pantry")
async def save_pantry(request: Request):
    body = await request.json()
    write_json("pantry.json", body)
    return {"ok": True}


@app.get("/api/plan")
async def get_plan():
    data = read_json("plan.json")
    return JSONResponse(data if data is not None else {})


@app.post("/api/plan")
async def save_plan(request: Request):
    body = await request.json()
    write_json("plan.json", body)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Shopping list CRUD
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
    pantry = read_json("pantry.json") or []
    plan = read_json("plan.json") or {}
    meals_data = read_json("meals.json") or {}
    meals = meals_data.get("meals", []) if isinstance(meals_data, dict) else meals_data

    items = []
    seen = set()

    # Low pantry items
    for p in pantry:
        if isinstance(p, dict) and p.get("qty") in ("low", "0", 0):
            name = p.get("name", "")
            if name.lower() not in seen:
                items.append({"name": name, "reason": "running low", "checked": False})
                seen.add(name.lower())

    # Planned meal ingredients not in pantry
    pantry_names = {(p.get("name", "") if isinstance(p, dict) else p).lower() for p in pantry}
    for planned in plan.get("meals", []):
        meal_name = planned if isinstance(planned, str) else planned.get("name", "")
        meal = next((m for m in meals if m.get("name", "").lower() == meal_name.lower()), None)
        if meal:
            for ing in meal.get("ingredients", []):
                if ing.lower() not in pantry_names and ing.lower() not in seen:
                    items.append({"name": ing, "reason": f"for {meal['name']}", "checked": False})
                    seen.add(ing.lower())

    return items


# ---------------------------------------------------------------------------
# Legacy backward-compat: GET/POST /data/{filename}
# ---------------------------------------------------------------------------

@app.get("/data/{filename}")
async def legacy_get_data(filename: str):
    if filename not in ALLOWED_LEGACY_FILES:
        raise HTTPException(400, "File not allowed")
    data = read_json(filename)
    return JSONResponse(data) if data is not None else JSONResponse(None)


@app.post("/data/{filename}")
async def legacy_post_data(filename: str, request: Request):
    if filename not in ALLOWED_LEGACY_FILES:
        raise HTTPException(400, "File not allowed")
    body = await request.json()
    write_json(filename, body)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Meal logging + pantry depletion
# ---------------------------------------------------------------------------

@app.post("/api/log-meal")
async def log_meal(req: LogMealRequest):
    """Log a meal to history and deplete pantry ingredients."""
    # Insert into meal_log
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO meal_log (meal_name, energy_level, notes) VALUES (?, ?, ?)",
            (req.meal_name, req.energy_level, req.notes),
        )
        await db.commit()

    # Deplete pantry ingredients for this meal
    meals_data = read_json("meals.json") or {}
    meals = meals_data.get("meals", []) if isinstance(meals_data, dict) else meals_data
    pantry = read_json("pantry.json") or []

    # Find the meal in catalog
    meal = None
    for m in meals:
        if m.get("name", "").lower() == req.meal_name.lower():
            meal = m
            break

    if meal and pantry:
        meal_ingredients = {i.lower() for i in meal.get("ingredients", [])}
        # Remove matching items or decrement quantities
        updated_pantry = []
        for item in pantry:
            name = (item.get("name", "") if isinstance(item, dict) else item).lower()
            if name in meal_ingredients:
                # If item has a numeric qty, decrement; otherwise remove
                if isinstance(item, dict) and isinstance(item.get("qty"), (int, float)):
                    item["qty"] = max(0, item["qty"] - 1)
                    if item["qty"] > 0:
                        updated_pantry.append(item)
                # If qty is a string like "plenty" or item is just a string, remove it
                # (conservative: mark as "low" instead of removing)
                elif isinstance(item, dict):
                    item["qty"] = "low"
                    updated_pantry.append(item)
                # Simple string item — remove it
            else:
                updated_pantry.append(item)
        write_json("pantry.json", updated_pantry)

    return {"ok": True, "meal_name": req.meal_name}


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@app.get("/api/history")
async def get_history():
    """Return meal history for the last 30 days."""
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, meal_name, logged_at, energy_level, notes "
            "FROM meal_log WHERE logged_at >= ? ORDER BY logged_at DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


@app.delete("/api/history/{entry_id}")
async def delete_history(entry_id: int):
    """Delete a meal log entry by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM meal_log WHERE id = ?", (entry_id,))
        await db.commit()
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
    meals_data = read_json("meals.json") or {}
    meals = meals_data.get("meals", []) if isinstance(meals_data, dict) else meals_data
    pantry = read_json("pantry.json") or []

    if not meals:
        return []

    # Build pantry set (normalize names)
    pantry_names = set()
    for item in pantry:
        if isinstance(item, dict):
            pantry_names.add(item.get("name", "").lower())
        elif isinstance(item, str):
            pantry_names.add(item.lower())

    # Get recent meal history for rotation scoring
    last_eaten: dict[str, datetime] = {}
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT meal_name, MAX(logged_at) as last_at FROM meal_log "
                "GROUP BY meal_name"
            )
            for row in await cursor.fetchall():
                try:
                    last_eaten[row["meal_name"].lower()] = datetime.fromisoformat(
                        row["last_at"]
                    )
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass  # DB might not exist yet

    now = datetime.now()
    results: list[dict] = []

    for meal in meals:
        name = meal.get("name", "")
        ingredients = [i.lower() for i in meal.get("ingredients", [])]
        meal_energy = meal.get("energy", "").lower()

        # Energy filter
        if energy and meal_energy and meal_energy != energy.lower():
            continue

        if not ingredients:
            continue

        # Pantry match
        available = [i for i in ingredients if i in pantry_names]
        missing = [i for i in ingredients if i not in pantry_names]
        match_pct = len(available) / len(ingredients) if ingredients else 0

        # Days since last eaten (more days = higher bonus, capped at 14)
        last = last_eaten.get(name.lower())
        if last:
            days_since = (now - last).days
        else:
            days_since = 14  # never eaten = treat as 14 days ago
        rotation_bonus = min(days_since, 14) / 14  # 0..1

        # Combined score: 60% pantry match, 40% rotation
        score = round(match_pct * 0.6 + rotation_bonus * 0.4, 3)

        # Build reasoning strings
        reasons = []
        if match_pct == 1.0:
            reasons.append("You have everything you need")
        elif match_pct >= 0.5:
            reasons.append(f"You have {len(available)} of {len(ingredients)} ingredients")
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

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Intent detection (regex/keyword first, Qwen stub for Phase 4)
# ---------------------------------------------------------------------------

INTENT_PATTERNS = [
    # Meal logging
    (r"^i\s+(made|ate|had|cooked|finished)\s+(.+)", "log_meal"),
    (r"^just\s+(ate|had|made)\s+(.+)", "log_meal"),
    # Pantry add (comma-separated list)
    (r"^(?:bought|got|picked up|grabbed)\s+(.+)", "pantry_add"),
    (r"^(.+,\s*.+)$", "pantry_add"),  # "eggs, rice, salmon"
    # Shopping list
    (r"^(?:need|buy|get|add to list)\s+(.+)", "shop_add"),
    # Energy/mood filter
    (r"\b(quick|easy|fast|simple|light)\b", "energy_filter"),
    (r"\b(hearty|filling|big|heavy)\b", "energy_filter"),
    # Suggestion request
    (r"^(?:what|what's|whats)\s+(?:for|should|can)\b", "suggest"),
    (r"^(?:surprise me|something|anything)", "suggest"),
]


@app.post("/api/parse-input")
async def parse_input(req: ParseInputRequest):
    """
    Intent detection: regex/keyword first, return 'unknown' if no match.
    Phase 4 will add Qwen for ambiguous inputs.
    """
    text = req.text.strip()
    if not text:
        return ParseInputResponse(intent="empty", confidence=1.0)

    for pattern, intent in INTENT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            entities: dict = {}
            if intent == "log_meal":
                # Extract meal name from last capture group
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

    # No regex match — return unknown (Phase 4: call ask_local here)
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
