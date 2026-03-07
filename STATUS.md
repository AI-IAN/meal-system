---
status: active
priority: medium
phase: "Phase 2"
phase_pct: 25
updated: 2026-03-07
---

# Meal System

## What it is
Three HTML apps: meal tracker (index.html), DP-600 study tracker, and quality reading tracker. Served from localhost:8080.

## Current focus
- Adding "Plan" tab to index.html — weekly meal grid with grocery list generation
- Migrating to dark theme with shared Life OS design tokens

## Next up
- localStorage persistence for plan data (key: ms2-plan)
- Dashboard reads ms2-plan for summary card

## Blocked / Notes
Plan tab depends on Life OS dashboard design tokens being finalized first (Phase B-3).
