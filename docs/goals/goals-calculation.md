# Goal Calculation

This document explains how WorkGraph turns your configured goal targets into the drift and allocation reports you see in the dashboard and CLI.

## 1. Goal source

WorkGraph reads goal targets from either of these files, in order:

1. config/my-goals.yaml
2. config/goals.yaml

The file is expected to contain a top-level mapping named goals, for example:

```yaml
goals:
  Client Delivery: 45
  Learning: 20
  Strategic Planning: 15
  Personal Projects: 20
```

## 2. What gets measured

For the goal analysis window, WorkGraph looks at tracked activity sessions from the database and uses only non-idle time.

It aggregates the total duration by tag, using the session tag values such as:

- Client Delivery
- Learning
- Strategic Planning
- Personal Projects
- and any other tag present in your data

The calculation uses the time range selected by the report, typically the last $N$ days.

## 3. How goal progress is calculated

WorkGraph should evaluate goals using two complementary metrics:

1. Hours as the primary KPI for commitment tracking
2. Percentages as a secondary metric for allocation and balance analysis

### 3.1 Hours-based progress

For each goal, the system sums the tracked seconds that map to that goal in the selected time range and converts them to hours:

$$
\text{actual\_hours} = \frac{\text{goal\_seconds}}{3600}
$$

The target comes from the YAML configuration:

```yaml
goals:
  Learning:
    target:
      hours: 10
      period: week
```

Progress is then computed as:

$$
\text{progress\_pct} = \frac{\text{actual\_hours}}{\text{target\_hours}} \times 100
$$

### 3.2 Percentage-based allocation

Percentages are still useful for understanding balance and drift. They are computed from the same goal totals, but as a derived view:

$$
\text{actual\_pct} = \frac{\text{goal\_seconds}}{\text{total\_tracked\_seconds}} \times 100
$$

This answers the question: "How much of my time went to this goal?"

It does not replace the hours-based target, which answers: "Did I meet my commitment?"

## 4. Goal status and drift

The system should report both:

- whether the target hours were met
- how the current allocation compares with the planned balance

### 4.1 Target completion

A goal is considered:

- Met if actual hours are greater than or equal to the target hours
- In progress if actual hours are below target hours but still positive
- Not started if actual hours are zero

### 4.2 Allocation drift

For balance analysis, the system can still compute:

$$
\text{delta} = \text{actual\_pct} - \text{planned\_pct}
$$

where planned percentage comes from the goal configuration when present.

## 5. Status labeling

The report can label each goal as:

- Met if the hours target is satisfied
- At risk if the hours target is behind schedule
- On track if the hours target is progressing well

Percent-based drift can still be shown as a secondary insight.

## 6. Where this is used

The same calculation feeds:

- the CLI goals report
- weekly/monthly summary reports
- dashboard goal cards
- any future AI coaching views

## 7. Example

If your goal file says:

```yaml
goals:
  Learning:
    target:
      hours: 10
      period: week
```

and your tracked activity shows 7.5 hours of learning time in the selected week, then:

- target hours = 10h
- actual hours = 7.5h
- progress = 75%
- status = In progress / behind target

If the same week also shows that Learning accounts for 13% of your total tracked time, that can be shown as a secondary allocation insight.

## 8. A better approach for your workflow

The earlier model was too brittle because it treated a goal as if it were the same thing as an activity label. That breaks down quickly when the same tool or website can mean different things depending on context.

A more robust model is to separate the problem into four layers:

1. Raw activity
   - the session you actually captured
   - examples: app, browser domain, window title, repo, duration, device, time of day

2. Activity label
   - a short, concrete description of what happened
   - examples: `meeting`, `coding`, `learning_content`, `browsing`

3. Intent
   - the purpose behind the activity
   - examples: `client_meeting`, `delivery_work`, `learning`, `personal_coding`, `interview_prep`

4. Goal
   - the broader commitment bucket the intent contributes to
   - examples: `Client Delivery`, `Learning`, `Personal Projects`

This makes the flow explicit:

raw activity -> activity label -> intent -> goal

### Why this is better

A single activity label can map to different intents depending on context.

For example:

- `meeting` could be:
  - `client_meeting` -> goal `Client Delivery`
  - `interview_prep` -> goal `Learning` or `Career Growth`

- `coding` could be:
  - `delivery_work` -> goal `Client Delivery`
  - `personal_coding` -> goal `Personal Projects`

- `youtube` could be:
  - `learning` -> goal `Learning`
  - `entertainment` -> no goal or a personal bucket

That is why a direct activity-to-goal mapping is not robust by itself.

### Recommended model

Use a simple, deterministic pipeline:

1. Keep the raw session as the source of truth.
2. Infer an activity label from the session signals.
3. Infer an intent from the activity label plus context.
4. Map the intent to one or more goals.
5. Aggregate time by goal for reporting.

### Example mapping

| Raw activity | Activity label | Intent | Goal |
|---|---|---|---|
| VS Code + work repo + client-related window | `coding` | `delivery_work` | `Client Delivery` |
| Google Meet + client context | `meeting` | `client_meeting` | `Client Delivery` |
| Udemy / Coursera / NotebookLM | `learning_content` | `learning` | `Learning` |
| Personal repo + side-project keywords | `coding` | `personal_coding` | `Personal Projects` |

### Suggested configuration shape

Using the current YAML structure, a practical example looks like this. Goals keep the target, planned allocation, and a list of activity-intent IDs, while tags can also carry explicit intent metadata to make the intent layer visible in configuration:

```yaml
goals:
  Client Delivery:
    display_name: Client Delivery
    target:
      hours: 40
      period: week
    planned_pct: 45
    activity_intent_ids:
      - client_delivery
      - meetings
      - planning

  Learning:
    display_name: Learning
    target:
      hours: 10
      period: week
    planned_pct: 20
    activity_intent_ids:
      - learning
      - study
```

```yaml
tags:
  Client Delivery:
    repos:
      - compass
      - tenet
      - atlas
    domains:
      - teams.microsoft.com
      - meet.google.com
    keywords:
      - client
      - delivery
      - meeting
      - standup
    intent:
      - client_delivery
      - meetings
      - planning

  Learning:
    repos: []
    domains:
      - udemy.com
      - coursera.org
    keywords:
      - tutorial
      - course
      - certification
    intent:
      - learning
      - study
```

This keeps the current tag-based matching behavior while making the intent mapping explicit enough to support reporting and explainability.

### Practical implementation guidance

For the first version, keep this simple and explicit:

- use rule-based matching only
- infer one intent per session
- allow manual overrides for ambiguous sessions
- keep the raw session data intact so the classification can be explained later

This gives you a model that is much more understandable than a direct tag-to-goal shortcut.

### Why this helps the reports

Once the system has an intent, the reports become more meaningful because they can answer:

- how much time did I spend on Client Delivery this week?
- how much of my learning time was actual study versus entertainment?
- how much of my personal project time came from side-project coding?

That is much more useful than a simple percentage split by tag.

## 9. Implementation direction

A practical implementation should follow this pipeline:

Raw Activity

↓

Activity Label

↓

Intent Inference

↓

Goal Allocation

↓

Reports / Drift / Coaching

### Step 1: keep raw activity as the source of truth

Each captured session should continue to store the raw signals that describe what happened:

- device
- app name
- browser domain
- window title
- repo / branch
- time of day
- duration

### Step 2: infer an activity label

The first classification layer should be simple and concrete:

- `meeting`
- `coding`
- `learning_content`
- `browsing`
- `admin`

### Step 3: infer an intent

The second layer should explain purpose:

- `client_meeting`
- `delivery_work`
- `learning`
- `personal_coding`
- `interview_prep`

### Step 4: map intent to goal

The final layer turns that intent into a commitment bucket:

- `Client Delivery`
- `Learning`
- `Personal Projects`
- `Strategic Planning`

### Recommended rollout

Start with a simple version:

- use device + domain + repo + keyword rules
- infer one intent per session
- keep the mapping visible in YAML so it is easy to adjust
- allow manual overrides when a session is ambiguous

### Implementation plan adjustments from the feedback

The implementation should also reflect the following decisions for v1:

- Keep the activity-label layer optional. It should only be included if we need reports such as "32h coding" or "6h meetings". If that is not required, the system can infer intent directly from raw context.
- Use stable IDs internally for goals and intents, such as `client_delivery` and `learning`, and keep display names separate for UI use.
- Record the matched rule or reason for each inferred intent so the dashboard can explain why a session was counted toward a goal.
- For the first version, enforce one intent per session and one goal per intent. Many-to-many mappings can be introduced later if they prove necessary.

This should be enough to make goal reporting much more meaningful without overcomplicating the system.

## 10. Notes

- Hours should remain the primary KPI.
- Percentages should stay as a secondary balance view.
- The classification should be explainable, not opaque.
- If no activity exists in the selected range, the goal report should simply show zero or no progress rather than pretending to know more than it does.
