# Meal System Redesign — Option C (Hybrid)

## Problem

The app works but isn't used. Friction exceeds value.
- Pantry entry = tap 40+ chips individually
- Meal data hardcoded in HTML CONFIG — can't edit without touching code
- 21-slot weekly grid is heavyweight for "what's next?"
- No depletion — pantry state rots immediately after first meal
- No history — random picker repeats, no learning

## Design Principles

1. **Code-first, LLM only for ambiguity** — don't use a model where `split(",")` works
2. **JSON for catalog data, SQLite only for history** — git-visible, hand-editable
3. **Entry = typing, not tapping** — natural language where it earns its keep
4. **The app should answer one question: "What should I eat?"**
5. **Fun, not clinical** — eating should feel good. Positive tone, vibrant colors, playful energy.

## Architecture

```
Frontend (vanilla HTML/CSS/JS)
  ↕ fetch()
Thin FastAPI backend (server.py)
  ├── Static file serving
  ├── JSON CRUD (meals.json, pantry.json, plan.json)
  ├── SQLite (meal_history.db) — append-only log
  ├── Smart endpoints (deterministic Python logic)
  └── /ai/* endpoints (Qwen via Ollama, Haiku fallback)
```

### What lives where

| Data | Format | Why |
|------|--------|-----|
| Meals catalog | `meals.json` | Hand-editable, git-visible, changes rarely |
| Pantry state | `pantry.json` | Small, flat, full overwrite on change. **Includes quantities** (×6, plenty, ½ tub) |
| Meal plan | `plan.json` | Simple key-value, easy to inspect |
| Meal history | SQLite `meal_log` | Append-only, needs queries ("last 7 days", "when did I last have X") |
| Shopping history | SQLite `shop_log` | Pattern detection ("usual Whole Foods run") |

### LLM Routing

**Principle: Code first. Qwen for ambiguity. Haiku only if Qwen fails.**

| Task | Handler | Why |
|------|---------|-----|
| Parse "eggs, rice, salmon" | Python `split(",") + strip()` | Deterministic |
| Fuzzy match "salm" → "Salmon" | Python `difflib.get_close_matches` | Deterministic |
| "What can I make?" | Python set intersection (pantry ∩ meal ingredients) | Deterministic |
| Meal rotation/avoid repeats | Python query on meal_log | Deterministic |
| Shopping list from plan | Python aggregate ingredients | Deterministic |
| "I made the rice bowl last night" | **Qwen** — intent + entity extraction | Ambiguous verb + meal reference |
| "Something quick, not too heavy" | **Qwen** — energy inference + reasoning | Subjective input |
| "Use up the salmon, it's been in the fridge" | **Qwen** — prioritization with context | Needs pantry + meal + freshness reasoning |
| "I bought the usual Whole Foods stuff" | **Qwen** — recall shopping patterns from history | Needs history query + inference |
| Qwen returns garbage / times out | **Haiku** fallback | Safety net |

**Model:** `qwen3.5:latest` via Ollama (`http://localhost:11434/api/generate`)

**Ollama call pattern:**
```python
async def ask_local(prompt, system="", model="qwen3.5:latest"):
    """Call Qwen via Ollama. Returns parsed response or None on failure."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("http://localhost:11434/api/generate", json={
                "model": model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 256}
            }, timeout=15)
            return resp.json()["response"]
    except:
        return None  # caller falls back to Haiku or returns error
```

**Key: structured prompts with constrained output.** Don't ask Qwen to be creative — give it the meal list, pantry state, and ask it to pick/parse into a specific JSON shape. Small models excel when the answer space is narrow.

## Visual Design (FINAL)

### Layout: "The Prompt"
Conversational, input-first. The text field IS the interface. Meals appear as ranked suggestions with reasoning ("Salmon's been in the fridge 2 days — tonight's the night"). Not a dashboard — a conversation with your kitchen.

### Fonts
- **Space Grotesk** — body text, UI elements, nav
- **Instrument Serif** (italic) — greeting headline, suggestion reasoning
- **IBM Plex Mono** — data (calories, protein, timestamps, quantities)

### Icons (SVG, purpose-built)
- **Eat** — bowl with steam rising
- **Plan** — calendar grid with dots
- **Shop** — shopping bag
- **Pantry** — jar with lines
- **Log** — clock face

### Colors: Three switchable themes
Theme persists to localStorage. Switcher is a small unobtrusive control (corner icon or settings gear), NOT a prominent bar.

**1. Fresh Lime (default)**
- Dark neutral base (#111210)
- Green→lime gradient accent (#30b860 → #90d840)
- Bright, energetic, fresh — like a farmers market
- Lime green for ready states, warm amber for partial

**2. Electric Berry**
- Dark cool base (#100e14)
- Purple→pink gradient (#d040e0 → #f06090)
- Bold, playful, a little punk
- Teal-mint (#50d8a0) for ready states

**3. Sunset Coral**
- **Neutral** dark base (#111214) — NOT brown, no warm undertones
- Coral→peach gradient (#f06050 → #f8a870)
- Warm but vibrant — golden hour energy
- Teal (#40c8a0) for ready states

### Tone & Copy
The app should feel encouraging, not like a chore tracker.
- **Input placeholder:** "What sounds good?" or "Grabbed groceries? Tell me what you got"
- **Hint pills:** "something quick", "use what's fresh", "high protein", "surprise me" — positive framing, not "I'm tired"
- **Suggestion reasoning:** personal, warm, useful — "You haven't had this in a while" not "Last consumed 5 days ago"
- **Empty states:** friendly, not clinical — "Your pantry's empty — let's stock up!" not "No data found"

### Responsive
- Mobile-first (480px max), but adapts to desktop — wider layout with more breathing room on larger screens
- Not a phone-only skinny column on a 27" monitor

## UX Redesign

### Core Flow Change

**Current:** Tabs (Eat | Plan | Shop | Pantry) — 4 equal surfaces, no clear entry point

**New:** Single primary surface with contextual panels

```
┌─────────────────────────┐
│  What are we eating?    │  ← Always visible, serif italic greeting
│                         │
│  [text input field]     │  ← Type anything: "use the salmon",
│  [hint pills]           │     "just ate eggs", "add rice to pantry"
│                         │
│  ┌─ Suggestion ───────┐ │
│  │ Rice Bowl + Salmon  │ │  ← Top pick based on: pantry, history, energy
│  │ You have everything.│ │     Reasoning in italic serif
│  │ Haven't had in 5d.  │ │
│  │ [Ate this] [Not that]│ │
│  └────────────────────┘ │
│                         │
│  Also works:            │
│  2. Eggs + Toast  320   │  ← Compact alternatives
│  3. Stir Fry      380   │
│                         │
│  [Eat] [Plan] [Shop]   │  ← Bottom nav, SVG icons
│  [Pantry] [Log]         │
└─────────────────────────┘
```

### Input Field — The Key Innovation

One text field that handles everything via intent detection:

| Input | Detected intent | Action |
|-------|----------------|--------|
| "eggs, rice, miso" | Pantry add (comma list) | Add to pantry, confirm |
| "I made rice bowl" | Meal log | Log to history, deplete ingredients |
| "something quick" | Energy state | Filter suggestions to low-effort |
| "what's for dinner" | Suggestion request | Show top pick for dinner-appropriate meals |
| "need salmon" | Shopping list add | Add to shopping list |
| "bought everything on list" | Shopping → pantry | Move checked items to pantry |

**Intent detection priority:**
1. **Regex/keyword match** (Python) — handles 80% of inputs
   - Starts with "I made/ate/had" → meal log
   - Comma-separated words → pantry add
   - "need/buy/get" prefix → shopping list
   - "quick/easy/fast/light" → energy=low filter
2. **Qwen** (local) — only called for inputs that don't match patterns
   - Returns structured JSON: `{"intent": "log_meal", "meal": "rice bowl", "confidence": 0.9}`
3. **Ambiguous → ask** — if confidence < 0.7, ask the user: "Did you mean you ate the rice bowl, or you want to add its ingredients?"

### Panels (replace tabs)

All 5 views work as full sections with smooth transitions. Bottom nav switches between them.

**Eat (home):**
- Greeting + input + suggestions (the main flow)
- Context chips showing meals today, pantry count, items to use soon

**Pantry Panel:**
- Quick-stock: "What did you buy?" → text entry, parsed into items
- Category chips still exist but as a *verification* view, not primary entry
- Items show quantity (×6, plenty, low) and "last used" date from history
- "Running low" indicators based on depletion tracking

**Plan Panel:**
- NOT a 21-slot grid
- "Next 3-5 meals" rolling view
- "Suggest" fills them based on: pantry, history, variety
- Tap to swap, drag to reorder

**Shop Panel:**
- Auto-generated from: low pantry items + planned meals needing ingredients
- Manual add still works
- "Usual run" button — recalls last shopping pattern (from shop_log)

**History Panel:**
- Simple reverse-chronological list
- "This week" / "Last week" grouping
- Highlights: variety score, repeat alerts, streaks

## Data Migration

From current system to new:

1. Extract CONFIG meals from index.html → `meals.json`
2. Pantry format changes: `["eggs"]` → `[{"name": "eggs", "qty": 6, "unit": "count"}]`
3. Keep `plan.json` format (backward compatible)
4. Create `meal_history.db` with tables:
   - `meal_log (id, meal_name, logged_at, energy_level, notes)`
   - `shop_log (id, items_json, store, logged_at)`
5. Server.py: replace stdlib HTTP server with FastAPI (~200 lines)

## Implementation Plan

**IMPORTANT: No worktree isolation.** All agents work directly in the repo. Commit frequently between phases to prevent work loss.

### Phase 1: Data & Backend (~1 session)

**Can be parallelized with subagents:**

**Agent 1 — Data extraction:**
- [ ] Extract CONFIG meals from current index.html → `data/meals.json`
- [ ] Convert pantry format to include quantities
- [ ] Validate all meal ingredient lists are consistent

**Agent 2 — FastAPI backend:**
- [ ] New `server.py` with FastAPI + uvicorn
- [ ] JSON CRUD endpoints: GET/POST for meals.json, pantry.json, plan.json
- [ ] SQLite setup: meal_log and shop_log tables
- [ ] POST /log-meal — logs meal, depletes pantry ingredients
- [ ] GET /suggestions — deterministic "what can I make?" (pantry intersection + history rotation)
- [ ] POST /parse-input — intent detection (regex first, Qwen endpoint for ambiguous)
- [ ] Ollama integration with httpx (async, qwen3.5:latest, Haiku fallback)
- [ ] CORS for Tailscale cross-device

**Commit checkpoint after Phase 1.**

### Phase 2: Frontend — Eat view (~1 session)

**Agent 3 — Core UI:**
- [ ] New index.html with theme system (CSS custom properties, 3 palettes)
- [ ] Responsive layout (mobile-first, adapts to desktop)
- [ ] Greeting + input field + hint pills
- [ ] Fetch suggestions from backend, render ranked cards with reasoning
- [ ] "Ate this" / "Not that" actions (POST to /log-meal)
- [ ] Bottom nav with SVG icons (all 5 views)
- [ ] Theme switcher (unobtrusive — gear icon or small dots)
- [ ] Positive tone throughout (copy per Visual Design section)

**Commit checkpoint after Phase 2.**

### Phase 3: Remaining Panels (~1 session)

**Can be parallelized:**

**Agent 4 — Pantry + Shop panels:**
- [ ] Pantry: text entry for bulk add, category chips as verification, quantity display
- [ ] Pantry: "running low" and "last used" indicators
- [ ] Shop: auto-generated list from low pantry + plan needs
- [ ] Shop: manual add, check off items, "add bought to pantry" flow

**Agent 5 — Plan + History panels:**
- [ ] Plan: rolling "next 3-5 meals" view (not 21-slot grid)
- [ ] Plan: "Suggest" button fills from backend logic
- [ ] History: reverse-chronological meal log
- [ ] History: this week / last week grouping, variety indicators

**Commit checkpoint after Phase 3.**

### Phase 4: Intelligence + Polish (~1 session)
- [ ] Qwen integration for ambiguous inputs (test with real prompts)
- [ ] Haiku fallback when Qwen fails/times out
- [ ] Shopping pattern learning ("usual run" feature)
- [ ] Mobile safe areas, tap targets
- [ ] Tailscale cross-device testing
- [ ] Meal editor (add/edit meals via UI → writes meals.json)
- [ ] Onboarding: "Stock your pantry in 30 seconds" quick-entry flow

## Resolved Questions

- ~~Visual direction~~ → The Prompt layout, 3 switchable themes (lime, berry, coral)
- ~~Qwen model~~ → qwen3.5:latest via Ollama
- ~~Quantities~~ → Yes, fuzzy (×6, plenty, ½ tub, low)
- ~~httpx vs requests~~ → httpx (async-native for FastAPI)
- ~~Meal plan dashboard integration~~ → Keep shared localStorage for now
- ~~Worktree isolation~~ → NO. Work directly in repo. Commit between phases.
