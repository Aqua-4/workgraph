I think this is where WorkGraph becomes genuinely useful beyond activity tracking.

Most tools answer:

What did I do?

This feature adds:

Why did the day go that way?

That is much more valuable for review and LLM analysis.

---

## Updated V1.2 Approach (Revised)

### 1) Keep journal data in a separate table with no direct relations

We should not modify `activity_sessions` and we should not add relation tables in V1.2.

Instead, keep journal entries independent and correlate by time window at query time.

Proposed table:

```sql
CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    title TEXT,
    notes TEXT,
    metadata TEXT
);
```

Notes:

- `metadata` is JSON text and can store optional context without rigid schema.
- No foreign keys needed in V1.2.
- Correlation function can fetch sessions in `start_time -> end_time` window.

---

### 2) Remove CLI journaling and add GUI journaling tab

Do not implement `workgraph note`.

Add a new tab in dashboard:

- Calendar date selector
- Start timestamp (optional)
- End timestamp (optional)
- Title
- Notes
- Save button

This allows users to log historical moments, not only real-time entries.

---

### 3) Keep journal fields open text

Do not force category/impact enums in V1.2.

Users may log anything relevant to their day, including non-work context, for example:

- Office training
- Traveling to client location
- Emotional state and recovery time
- Personal interruption that impacted work

The journal should remain expressive first, structured second.

---

## Correlation Strategy (Without DB Relations)

Journal and activity sessions can be correlated using timestamp overlap.

Example logic:

1. User opens a journal entry (with time range).
2. Query `activity_sessions` where session overlaps that range.
3. Return grouped summary:
   - apps used
   - repos involved
   - total active time
   - context switching count

This preserves clean separation while still enabling rich analysis.

---

## Daily Reflection + Energy/Stress

Keep this as part of journaling UX (same tab, separate section):

- Date
- Wins (text)
- Problems (text)
- Tomorrow plan (text)
- Energy score (1-10)
- Stress score (1-10)

This can be stored in a second lightweight table:

```sql
CREATE TABLE IF NOT EXISTS daily_reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    wins TEXT,
    problems TEXT,
    tomorrow TEXT,
    energy INTEGER,
    stress INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Even if wins/problems are free text, energy/stress gives strong long-term signals.

---

## Implementation Optimizations

### Schema + performance

- Store all times in UTC ISO format (`YYYY-MM-DDTHH:MM:SS+00:00`).
- Add indexes for fast filtering:
  - `journal_entries(start_time)`
  - `journal_entries(end_time)`
  - `daily_reflections(date)`
- Keep text payloads in `TEXT`; avoid premature normalization.

### API design

- Add journal endpoints:
  - `POST /api/journal`
  - `GET /api/journal?from=...&to=...`
  - `GET /api/journal/{id}/correlated-sessions`
- Add reflection endpoints:
  - `PUT /api/reflections/{date}`
  - `GET /api/reflections?from=...&to=...`

### UI design

- Add a new dashboard tab: `Journal`.
- Pre-fill date/time fields with current local time for speed.
- Support optional empty start/end time for broad notes.
- Add quick templates in UI only (not DB enums):
  - Incident
  - Win
  - Learning
  - Risk

### Data quality without rigid schema

- Keep inputs open, but provide optional helper labels.
- Save labels under `metadata` JSON so users are not blocked.
- Use soft guidance (placeholder examples), not hard validation.

### Correlation efficiency

- Implement overlap query once in repository/service layer.
- Cache correlation results for a short window in API if needed.
- Only compute expensive summaries on-demand (entry details page), not on every list row.

### Rollout safety

- Ship schema migration first.
- Keep all existing activity collection untouched.
- Add journal/reflection read APIs before UI write path.
- Add basic tests for create/read + overlap query.

---

## Why this revised approach is strong

- Minimal risk: no changes to current session collector pipeline.
- High flexibility: users can record real-world context, not just predefined categories.
- Better usability: historical journaling from GUI is easier than CLI prompts.
- Better analysis: timestamp correlation provides context without hard coupling.

This keeps V1.2 simple and practical while unlocking much stronger LLM summaries later.