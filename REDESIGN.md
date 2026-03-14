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
| Pantry state | `pantry.json` | Small, flat, full overwrite on change |
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
| "I made the curry last night" | **Qwen** — intent + entity extraction | Ambiguous verb + meal reference |
| "I'm tired, something quick" | **Qwen** — energy inference + reasoning | Subjective input |
| "Use up the salmon, it's been in the fridge" | **Qwen** — prioritization with context | Needs pantry + meal + freshness reasoning |
| "I bought the usual Whole Foods stuff" | **Qwen** — recall shopping patterns from history | Needs history query + inference |
| Qwen returns garbage / times out | **Haiku** fallback | Safety net |

**Ollama call pattern:**
```python
def ask_local(prompt, system="", model="qwen3:0.5b"):
    """Call Qwen via Ollama. Returns parsed response or None on failure."""
    try:
        resp = httpx.post("http://localhost:11434/api/generate", json={
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 256}
        }, timeout=10)
        return resp.json()["response"]
    except:
        return None  # caller falls back to Haiku or returns error
```

**Key: structured prompts with constrained output.** Don't ask Qwen to be creative — give it the meal list, pantry state, and ask it to pick/parse into a specific JSON shape. Small models excel when the answer space is narrow.

## UX Redesign

### Core Flow Change

**Current:** Tabs (Eat | Plan | Shop | Pantry) — 4 equal surfaces, no clear entry point

**New:** Single primary surface with contextual panels

```
┌─────────────────────────┐
│  What should I eat?     │  ← Always visible, always the question
│                         │
│  [text input field]     │  ← Type anything: "I'm tired", "use the salmon",
│                         │     "I just ate eggs", "add rice to pantry"
│                         │
│  ┌─ Suggestion ───────┐ │
│  │ 🍳 Rice Bowl       │ │  ← Top pick based on: pantry, history, energy
│  │ You have everything │ │
│  │ Last had: 4 days ago│ │
│  │ [Ate this] [Not that]│ │
│  └────────────────────┘ │
│                         │
│  ┌─ Also works ───────┐ │
│  │ Curry (have all)    │ │  ← 2-3 alternatives
│  │ Eggs + Toast (quick)│ │
│  └────────────────────┘ │
│                         │
│  [Pantry] [Plan] [Shop] │  ← Secondary actions, bottom nav
│  [History]              │
└─────────────────────────┘
```

### Input Field — The Key Innovation

One text field that handles everything via intent detection:

| Input | Detected intent | Action |
|-------|----------------|--------|
| "eggs, rice, miso" | Pantry add (comma list) | Add to pantry, confirm |
| "I made curry" | Meal log | Log to history, deplete ingredients |
| "I'm tired" | Energy state | Filter suggestions to low-effort |
| "what's for dinner" | Suggestion request | Show top pick for dinner-appropriate meals |
| "need salmon" | Shopping list add | Add to shopping list |
| "bought everything on list" | Shopping → pantry | Move checked items to pantry |

**Intent detection priority:**
1. **Regex/keyword match** (Python) — handles 80% of inputs
   - Starts with "I made/ate/had" → meal log
   - Comma-separated words → pantry add
   - "need/buy/get" prefix → shopping list
   - "tired/lazy/quick/easy" → energy=low filter
2. **Qwen** (local) — only called for inputs that don't match patterns
   - Returns structured JSON: `{"intent": "log_meal", "meal": "curry", "confidence": 0.9}`
3. **Ambiguous → ask** — if confidence < 0.7, ask the user: "Did you mean you ate curry, or you want to add curry ingredients?"

### Panels (replace tabs)

Panels slide up from bottom or expand inline. Not full tab switches.

**Pantry Panel:**
- Quick-stock: "What did you buy?" → text entry, parsed into items
- Category chips still exist but as a *verification* view, not primary entry
- Items show "last used" date (from history) — stale items highlighted
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

## Visual Direction

The current dark indigo theme was a default, not a choice. The meal system deserves its own identity. Options to consider:

### A. Neo-Brutalist Zine
- Warm white base, chunky 3px borders, offset shadows
- Bright fills per energy level: yellow (low), pink (medium), teal (high)
- Feels playful — makes meal picking feel fun, not like chore management
- **Good fit because:** eating should feel joyful, not clinical

### B. Field Notebook
- Cream ruled-paper background, margin lines, handwritten feel
- Sticky notes for meal suggestions, check marks for pantry
- Personal and warm — like a kitchen notepad
- **Good fit because:** it's literally a food journal / kitchen reference

### C. Swiss Precision
- White, clean, single accent color, dramatic type scale
- Grid-based meal cards, tiny uppercase labels
- Feels efficient and smart — like a well-organized kitchen
- **Good fit because:** the app is about information density (pantry state, nutrition, availability)

### D. Something New
- Use the style guide prompt template to generate fresh options
- Could try: "Diner Menu", "Recipe Card Box", "Farmer's Market Chalkboard"

## Data Migration

From current system to new:

1. Extract CONFIG meals from index.html → `meals.json`
2. Keep existing `pantry.json`, `plan.json`, `ms2.json` formats (backward compatible)
3. Create `meal_history.db` with tables:
   - `meal_log (id, meal_name, logged_at, energy_level, notes)`
   - `shop_log (id, items_json, store, logged_at)`
4. Server.py grows from 80 lines to ~200 — still small

## Implementation Phases

### Phase 1: Data & Backend (~1 session)
- [ ] Extract meals from HTML CONFIG → `meals.json`
- [ ] FastAPI backend with JSON CRUD + SQLite history
- [ ] Meal log endpoint (POST /log-meal)
- [ ] "What can I make?" endpoint (deterministic Python)
- [ ] Pantry depletion on meal log

### Phase 2: New Frontend (~1-2 sessions)
- [ ] Choose visual direction (see options above)
- [ ] Build new single-surface UI with text input
- [ ] Intent detection (regex first, Qwen integration later)
- [ ] Suggestion engine (pantry + history + rotation)
- [ ] Slide-up panels for Pantry/Plan/Shop/History

### Phase 3: Intelligence (~1 session)
- [ ] Qwen integration for ambiguous inputs
- [ ] Haiku fallback
- [ ] Shopping pattern learning (from shop_log)
- [ ] "Usual run" feature
- [ ] Meal variety scoring

### Phase 4: Polish (~0.5 session)
- [ ] Mobile optimization (safe areas, tap targets)
- [ ] Tailscale cross-device testing
- [ ] Meal editor (add/edit meals via UI → writes meals.json)
- [ ] Onboarding flow ("stock your pantry in 30 seconds")

## Open Questions

1. **Visual direction** — which aesthetic? Or generate new options?
2. **Qwen model** — `qwen3:0.5b` or larger? What's currently running on the MacBook?
3. **Meal plan integration with dashboard** — keep shared localStorage (`ms2-plan` key)?
4. **Quantities** — worth tracking (2 eggs vs "have eggs") or keep binary for now?
5. **httpx vs requests** — for Ollama calls. httpx is async-native (better with FastAPI)
