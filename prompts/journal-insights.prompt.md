---
mode: ask
description: Summarise, search, or draw insights from workgraph journal entries and daily reflections.
---

You have access to the user's journal from the workgraph activity database
(`activity.db`). Use the schema below to retrieve, summarise, or find themes
across entries. Always present data in a clear, empathetic tone.

## Relevant tables

### journal_entries
Free-form notes linked to an optional time window.

| Column | Notes |
|--------|-------|
| `created_at` | ISO-8601 UTC |
| `start_time` / `end_time` | optional session range this note covers |
| `title` | short heading (may be NULL) |
| `notes` | free-text body |
| `metadata` | JSON blob |
| `deleted_at` | NULL = active |

### daily_reflections
Structured end-of-day entries.

| Column | Notes |
|--------|-------|
| `date` | `YYYY-MM-DD` (UNIQUE) |
| `wins` | what went well |
| `problems` | blockers / frustrations |
| `tomorrow` | plans for next day |
| `energy` | 1–5 self-rating |
| `stress` | 1–5 self-rating |

## Example SQL

```sql
-- Most recent journal entries
SELECT created_at, title, notes
FROM journal_entries
WHERE deleted_at IS NULL
ORDER BY created_at DESC
LIMIT 10;

-- Daily reflections with low energy this month
SELECT date, wins, problems, energy, stress
FROM daily_reflections
WHERE date >= date('now', 'start of month')
  AND energy <= 2
ORDER BY date;

-- Average energy and stress by day-of-week
SELECT strftime('%w', date) AS dow,
       ROUND(AVG(energy),2)  AS avg_energy,
       ROUND(AVG(stress),2)  AS avg_stress
FROM daily_reflections
GROUP BY dow
ORDER BY dow;
```

## Notes for Copilot

- The `notes` and `wins`/`problems`/`tomorrow` columns contain raw user text —
  treat them with care and do not store or repeat sensitive content beyond what
  the user requests.
- When searching by keyword, use `LIKE '%keyword%'` or suggest FTS if the user
  has SQLite FTS5 available.

---

## Journal request

${input:What would you like to know from your journal? e.g. "Summarise my wins from this week", "Find entries where I mentioned feeling blocked", "What did I plan for tomorrow most often?"}
