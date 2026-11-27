# Goal Alignment Analysis

## Objective

Create a shared goal-alignment analysis engine for WorkGraph that feeds both the Goals reporting experience and burnout-risk indicators. Goal alignment remains descriptive: it identifies mismatch between intended and observed allocation, but it does not diagnose burnout or imply causality.

## Scope

The implementation covers:

- the dedicated Goals dashboard and CLI reports
- compact goal-drift information on the main dashboard
- context alongside existing burnout and wellbeing trend patterns
- local and sync-client dashboard data
- existing percentage-based and hours-based goal configuration formats

It does not introduce clinical risk scoring, background alerts, team rankings, or session-level manual correction in the first implementation.

## Existing Foundation

### Goal calculation

The canonical implementation is `analyze_goal_allocation()` in [services/reporting.py](../../services/reporting.py). It accepts a database path, goals path, time window, and optional reference time, and returns `GoalDrift` objects.

Each `GoalDrift` can contain:

- `goal`
- `planned_pct`
- `actual_pct`
- `delta_pct_points`
- `relative_gap_pct`
- `target_hours`
- `actual_hours`
- `progress_pct`
- `matched_intents`
- `match_reason`

### Intent mapping

The reporting service supports:

1. an explicit `activity_intent_ids` list on a goal
2. intent metadata in `tags.yaml` or `my-tags.yaml`
3. deterministic fallback text matching across tag, app, window title, and browser domain

This is a useful base for context-sensitive allocation, but it has two gaps: intent metadata can be incomplete, and text fallback can misclassify generic applications such as Teams or Slack.

### Dashboard integration

- `/goals` calls `analyze_goal_allocation()` and renders [api/templates/goals.html](../../api/templates/goals.html).
- `_build_goal_drift_summary()` creates compact goal drift values for the main dashboard.
- `get_burnout_trends()` independently summarizes workload and reflection trends without producing a burnout score.
- Both summaries appear in the local dashboard context and can be made available to sync-client views.

## Current Calculation Behavior

### Percentage-based goals

For legacy goals such as:

```yaml
goals:
  Learning: 20
```

The system compares the percentage of tracked time tagged `Learning` with the planned percentage.

$$
\text{actual\%} = \frac{\text{goal tag seconds}}{\text{total tracked seconds}} \times 100
$$

$$
\text{delta} = \text{actual\%} - \text{planned\%}
$$

The existing status bands are:

- under: delta at or below -5 percentage points
- on track: delta between -5 and +5 percentage points
- over: delta at or above +5 percentage points

### Hours-based goals

For structured goals:

```yaml
goals:
  Learning:
    target:
      hours: 10
      period: week
    planned_pct: 20
    activity_intent_ids:
      - learning
      - study
```

The system matches sessions by intent, sums the matching duration, and calculates:

$$
\text{progress\%} = \frac{\text{actual hours}}{\text{target hours}} \times 100
$$

The existing status bands are:

- under: progress below 90%
- on track: progress from 90% through 110%
- over: progress above 110%

Percentage drift remains available as a secondary allocation view.

## Known Limitations

1. The dashboard summary currently has tag-name-only logic separate from the richer intent-aware report.
2. Goal period normalization is not yet centralized; weekly, monthly, and custom windows need consistent handling.
3. `_load_goals()` and `_load_tag_intent_metadata()` use separate parsing assumptions.
4. `_session_matches_goal_intents()` allows fallback text to match too broadly in some contexts.
5. Sessions can match more than one intent or goal, but the reporting contract does not clearly separate overlap from exclusive allocation.
6. Goal alignment and burnout trends are currently calculated independently, which prevents period-level cross-signal observations.
7. Unmapped time may indicate flexible work, poor tagging, or legitimate activity outside configured goals. It should not automatically be labeled negative.
8. Reflection coverage is sparse for some users, so wellbeing comparisons must remain readiness-gated.
9. Existing queries do not consistently use `deleted_at IS NULL` in every goal path.
10. The result is sensitive to goal configuration changes; past sessions cannot be reinterpreted with a new intent model without explicit replay behavior.

## Proposed Shared Contract

Keep `GoalDrift` as the canonical result for an individual goal. Add a small companion summary for period and dashboard reporting:

```python
{
    "period_start": str,
    "period_end": str,
    "goal_count": int,
    "on_track_count": int,
    "under_count": int,
    "over_count": int,
    "average_absolute_drift_pct_points": float,
    "goal_coverage_pct": float,
    "unmapped_pct": float,
    "largest_under_goal": dict | None,
    "largest_over_goal": dict | None,
}
```

The summary is derived from `GoalDrift` values and never becomes a separate source of truth.

## Implementation Plan

### 1. Normalize reporting behavior

Update [services/reporting.py](../../services/reporting.py) to:

- treat hours and percentage targets as separate primary and secondary measures
- centralize `_load_goals()` parsing for scalar and structured goal formats
- normalize intent names once before session matching
- centralize active-session filtering with `deleted_at IS NULL`
- normalize time windows from an explicit period start and end
- preserve match explanations for auditability
- keep overlap visible when one session matches multiple goals

Hours targets should remain the primary commitment metric. Planned percentage remains the secondary allocation and balance metric.

### 2. Unify dashboard goal drift

Replace the independent tag-name-only logic in `_build_goal_drift_summary()` with `analyze_goal_allocation()` or a shared helper beneath it.

The dashboard-facing `goal_drift` result should continue to expose:

- `goals_path`
- `goal_targets`
- `goal_drift_items`
- `goal_drift_score_pct_points`
- `goal_coverage_pct`
- `unmapped_pct`
- `largest_gap`

Add the shared summary fields without breaking existing template consumers.

Apply the same implementation in local and sync-client summary paths. Preserve user and optional device scoping for sync analytics.

### 3. Integrate with burnout trends

Extend the period comparison used by `get_burnout_trends()` with a goal-alignment block assembled from the shared summary.

Examples of permitted observations:

- goal progress declined while meeting time increased
- goal coverage decreased while energy ratings changed
- active time and goal drift moved together across ready periods

Do not present these observations as causation or prediction. User-facing text should use association language and retain the existing descriptive disclaimer.

A cross-signal observation should require:

- enough activity coverage for the relevant period
- enough goal-matching coverage to avoid false interpretation
- sufficient reflection days before using wellbeing correlations
- at least two comparable periods

### 4. Update the Goals UI

Update [api/templates/goals.html](../../api/templates/goals.html) to show:

- time range
- goal coverage
- unmapped time
- average absolute drift
- counts of under/on-track/over goals
- the largest under and over goals
- existing progress or allocation detail
- the intent match reason for hours-based goals

Keep the hours table as the primary format when any goal has a target. Fall back to the percentage table for legacy configurations.

Add a compact goal-alignment section to [api/templates/dashboard.html](../../api/templates/dashboard.html) only if it does not duplicate the detailed Goals page. It should surface one sentence and the largest mismatch rather than a dense table.

### 5. Update CLI and report output

Add the shared alignment summary before goal tables in markdown and CLI output.

The summary should include:

- period and goal configuration path
- goal coverage and unmapped time
- drift score
- under/on-track/over counts
- largest under-target goal
- largest over-allocation goal

Keep existing CLI arguments compatible.

### 6. Add configuration documentation

Document the normalized goal contract in [docs/goals/goals-calculation.md](../goals/goals-calculation.md).

Clarify:

- target hours are primary commitment measures
- planned percentage is an allocation measure
- activity intent IDs map to goal reporting
- tag intent metadata is optional
- fallback matching is heuristic
- unmapped time is not inherently unhealthy
- custom threshold support, if added later, must retain a default for existing configurations

## Tests

### Reporting tests

In [tests/test_reporting_features.py](../../tests/test_reporting_features.py), cover:

- legacy scalar percentage goals
- structured hours goals
- planned percentage and actual percentage drift
- explicit intent matching
- tag intent metadata
- fallback intent matching
- multiple matching intents
- sessions matched by more than one goal
- zero tracked time
- 100% unmapped time
- deleted sessions
- period boundaries and timezone normalization
- missing goals file

### Goals UI tests

In [tests/test_goals_ui.py](../../tests/test_goals_ui.py), verify:

- hours-based rendering
- percentage-only rendering
- empty state
- coverage and unmapped values
- under/on-track/over status
- match reasons
- no clinical-risk language

### API tests

In [tests/test_api_journal.py](../../tests/test_api_journal.py), verify:

- dashboard goal summary with sufficient and insufficient data
- sync-client goal summary behavior
- deleted and idle sessions excluded
- goal and burnout sections rendered together
- cross-signal observations only when sufficient periods exist
- no goal alignment or burnout diagnosis score

### Additional validation

- run the Goals CLI for normal, legacy, empty, and sparse configurations
- open `/goals` and the local dashboard
- verify sync-client dashboard behavior
- verify percentage totals do not silently claim exclusive allocation
- verify user-facing copy avoids causality and clinical conclusions

## Verification

Run:

```bash
uv run python -m pytest tests/test_reporting_features.py tests/test_goals_ui.py tests/test_api_journal.py
```

Then run:

```bash
uv run python -m pytest tests/test_reporting_features.py tests/test_goals_ui.py tests/test_api_journal.py tests/test_sync_api.py tests/test_sync_worker.py
```

For manual verification:

1. Use a valid structured goals file and confirm progress and allocation agree.
2. Use a legacy scalar goals file and confirm the percentage fallback works.
3. Remove the goals file and confirm the empty state is clear.
4. Use sparse reflection data and confirm correlations are withheld.
5. Inspect sync-client output and confirm user scoping remains consistent.

## Decisions

- Goal alignment and burnout trend data share a contract, but remain separate measures.
- Goal alignment is descriptive and never becomes a clinical or predictive score.
- Existing threshold bands remain unchanged for the first implementation.
- Unmapped time is informational.
- Session-level manual overrides are out of scope initially.
- No new background alerting or recommendation service is required.

## Future Considerations

### Threshold configuration

Users may eventually need configurable under/on-track/over bands. If added, threshold settings should have defaults and should affect only reporting, not the raw drift calculation.

### Intent correction

A later session-review or tag-review workflow could improve intent classification. It should retain an audit trail and allow explicit session-level overrides rather than silently rewriting historical tags.

### Correlation analysis

A later implementation could compare alignment trends with meeting load, focus blocks, active time, stress, and energy. It should remain clearly local, user-scoped, descriptive, and readiness-gated.

### Period views

Once the base period contract is stable, add week-over-week and month-over-month summaries with enough coverage checks and explicit timezone handling.
