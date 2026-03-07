---
status: active
priority: high
updated: 2026-03-07
---

# Meal System

## What it is
Mobile-first meal picker app — weekly planning, pantry tracking, and "what can I make right now" filtered by what's in stock.

## How to run
```bash
cd ~/ij/projects/meal-system
python3 server.py
# MacBook: http://localhost:8080
# Phone (Tailscale): http://<macbook-tailscale-hostname>:8080
```

## Current focus
- server.py live — data persists to data/*.json, accessible via Tailscale on phone
- Pantry tab: chip tiles by category, tap to mark in stock
- Eat tab: sorted by availability based on pantry state

## Next up
- Populate pantry.json with actual current stock
- Plan this week, build grocery list, shop
- Meal rotation history (avoid repeating last 3 picks)
