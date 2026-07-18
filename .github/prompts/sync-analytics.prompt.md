---
agent: agent
description: Analyze multi-device sync analytics, data quality, and reliability signals for WorkGraph.
---

You are analyzing WorkGraph in sync-aware mode. Use the local SQLite database and
sync-related tables and APIs to evaluate both productivity metrics and data quality.

Prioritize user-scoped and device-scoped findings. Make it explicit whether each
insight is local-only, sync-aggregated, or uncertain due to sync lag/errors.

## Key API surfaces to align with

- GET /api/stats?source=sync&user_id=<id>&device_id=<optional>&days=<n>
- GET /api/sync/stats?user_id=<id>&device_id=<optional>&days=<n>
- GET /api/sync/users
- GET /api/sync/devices?user_id=<id>
- GET /api/sync/health
- GET /api/sync/errors
- POST /api/sync/rollups/rebuild?user_id=<id>&device_id=<optional>&start_day=<optional>

## Analysis goals

1. Cross-device productivity profile
- Active hours by device
- Focus ratio by device
- Context-switch density by device
- Top tags/apps per device

2. Data reliability and freshness
- Last sync recency and lag indicators
- Recent sync error patterns
- Potential undercount periods due to outages or retries

3. Actionable recommendations
- Device-level focus interventions
- Sync reliability hardening steps
- Rollup rebuild suggestions when historical stats mismatch

## SQL helpers (adapt as needed)

```sql
-- Active time by device (last 7 days)
SELECT device_id,
       ROUND(SUM(CASE WHEN is_idle = 0 THEN duration_sec ELSE 0 END) / 3600.0, 2) AS active_hours,
       ROUND(SUM(duration_sec) / 3600.0, 2) AS total_hours
FROM activity_sessions
WHERE start_time >= datetime('now', '-7 days')
  AND deleted_at IS NULL
GROUP BY device_id
ORDER BY active_hours DESC;

-- Context-switch density by device (switches per active hour)
SELECT device_id,
       ROUND(SUM(context_switches), 2) AS switches,
       ROUND(SUM(CASE WHEN is_idle = 0 THEN duration_sec ELSE 0 END) / 3600.0, 2) AS active_hours,
       ROUND(
         CASE
           WHEN SUM(CASE WHEN is_idle = 0 THEN duration_sec ELSE 0 END) = 0 THEN 0
           ELSE SUM(context_switches) / (SUM(CASE WHEN is_idle = 0 THEN duration_sec ELSE 0 END) / 3600.0)
         END,
         2
       ) AS switches_per_active_hour
FROM activity_sessions
WHERE start_time >= datetime('now', '-14 days')
  AND deleted_at IS NULL
GROUP BY device_id
ORDER BY switches_per_active_hour DESC;
```

## Output requirements

- Provide a concise executive summary first.
- Separate findings into:
  - Productivity insights
  - Reliability/data-quality insights
  - Recommendations
- Include confidence level per major finding (high/medium/low).
- Include SQL used and clearly mark assumptions.

---

## Sync analytics request

${input:What sync analytics question should be answered? e.g. "Which device is driving most context switching?", "Are sync errors skewing my 7-day stats?", "Compare focus quality across my devices this month"}
