---
status: active
priority: high
updated: 2026-03-09
---

# Meal System

## What it is
Mobile-first meal picker app — weekly planning, pantry tracking, and "what can I make right now" filtered by what's in stock.

## How to run
```bash
cd ~/ij/projects/meal-system
python3 server.py
# MacBook: http://localhost:8081
# Phone (Tailscale): http://<macbook-tailscale-hostname>:8081
```

## Current focus
- App fully built and running — all tabs (Eat, Plan, Shop, Pantry) functional
- Groceries ordered and arrived — ready to enter into system

## Next up
- Enter grocery haul into pantry.json (mark what's in stock)
- Plan meals for the week using the Plan tab
- Meal rotation history (avoid repeating last 3 picks)
