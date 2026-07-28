# Unknown Reduction Approach (Review Draft)

## Purpose

Document a safe, reviewable approach to reduce unknown or untagged activity at high impact, without overwriting trusted manual tagging decisions.

## Current Baseline

Observed from recent local checks:

- Total sessions: about 10.6k
- Untagged: about 2.3k to 2.5k
- Unknown app and untagged: near zero
- Untagged Brave sessions with no domain: dominant share (about 75%+ of untagged)
- Untagged sessions with no repo and no domain: very high share

Interpretation:

- The primary problem is not truly Unknown app rows.
- The primary problem is low-signal browser sessions with weak domain or repo evidence.

## Top Untagged Items (GROUP BY + COUNT)

Generated using a grouped SQLite query over untagged sessions with precedence:

1. repo
2. domain
3. browser_context (limited title patterns)
4. app

Note:

- Current live `activity.db` is malformed, so this snapshot uses `backups/activity-20260727-181550.db`.
- The query requested top 50 rows, but only 47 grouped items exist.

| Rank | Item Type       | Item Value                    | Session Count | Total Hours |
| ---- | --------------- | ----------------------------- | ------------: | ----------: |
| 1    | app             | brave                         |          1782 |       27.73 |
| 2    | browser_context | New Tab                       |           115 |         0.1 |
| 3    | app             | konsole                       |            84 |        0.21 |
| 4    | app             | explorer                      |            70 |        0.47 |
| 5    | app             | lockapp                       |            69 |        8.47 |
| 6    | domain          | google.com                    |            53 |        0.42 |
| 7    | app             | searchhost                    |            51 |        0.05 |
| 8    | app             | mintty                        |            39 |        0.06 |
| 9    | app             | plasmashell                   |            38 |        0.13 |
| 10   | domain          | foundit.in                    |            25 |        0.33 |
| 11   | domain          | instahyre.com                 |            22 |        0.11 |
| 12   | app             | xdg-desktop-portal-kde        |            17 |         0.0 |
| 13   | app             | applicationframehost          |            15 |        0.18 |
| 14   | app             | notepad                       |            14 |        0.01 |
| 15   | app             | shellexperiencehost           |            13 |        0.03 |
| 16   | domain          | eportal.incometax.gov.in      |            12 |        0.16 |
| 17   | app             | dolphin                       |            12 |        0.01 |
| 18   | app             | shellhost                     |             8 |         0.1 |
| 19   | app             | snippingtool                  |             8 |        0.02 |
| 20   | app             | winword.exe                   |             6 |         0.0 |
| 21   | app             | plasma-discover               |             5 |        0.07 |
| 22   | domain          | ijecs.in                      |             5 |        0.01 |
| 23   | domain          | sitechecker.pro               |             4 |        0.01 |
| 24   | domain          | simplescraper.io              |             4 |         0.0 |
| 25   | app             | devicepairingwizard           |             3 |        0.01 |
| 26   | app             | gethelp                       |             3 |         0.0 |
| 27   | domain          | data-factory-visual-guide.png |             2 |        0.06 |
| 28   | app             | widgets                       |             2 |        0.02 |
| 29   | domain          | seomator.com                  |             2 |        0.01 |
| 30   | app             | gup                           |             2 |         0.0 |
| 31   | app             | ksmserver-logout-greeter      |             2 |         0.0 |
| 32   | app             | kwalletd5                     |             2 |         0.0 |
| 33   | app             | soffice.bin                   |             2 |         0.0 |
| 34   | app             | spectacle                     |             2 |         0.0 |
| 35   | domain          | firecrawl.dev                 |             2 |         0.0 |
| 36   | domain          | readme.md                     |             2 |         0.0 |
| 37   | domain          | aka.ms                        |             1 |        0.16 |
| 38   | domain          | lmstudio.ai                   |             1 |        0.02 |
| 39   | domain          | azure.microsoft.com           |             1 |        0.01 |
| 40   | domain          | nextcloud.parashar.xyz        |             1 |        0.01 |
| 41   | app             | onedrive                      |             1 |         0.0 |
| 42   | app             | unknown                       |             1 |         0.0 |
| 43   | app             | windowsterminal               |             1 |         0.0 |
| 44   | browser_context | Diffchecker                   |             1 |         0.0 |
| 45   | domain          | diffchecker.com               |             1 |         0.0 |
| 46   | domain          | in.indeed.com                 |             1 |         0.0 |
| 47   | domain          | raspberrypi.local             |             1 |         0.0 |

## Untagged Brave Sessions (GROUP BY + COUNT)

Generated from live `activity.db` (integrity check: `ok`) for rows where:

- `COALESCE(tag, '') = ''`
- `LOWER(app_name) = 'brave'`

Grouping logic:

1. domain (when browser_domain exists)
2. browser_context (limited known title patterns)
3. title_bucket (fallback, normalized title prefix)

Summary:

- Untagged Brave total sessions: 1910
- Untagged Brave total hours: 26.71

| Rank | Brave Group Type | Brave Group Value                                                                 | Session Count | Total Hours |
| ---- | ---------------- | --------------------------------------------------------------------------------- | ------------: | ----------: |
| 1    | browser_context  | New Tab                                                                           |           115 |         0.1 |
| 2    | title_bucket     | dashboard - workgraph - brave                                                     |            96 |        0.33 |
| 3    | title_bucket     | journal - workgraph - brave                                                       |            92 |        2.07 |
| 4    | title_bucket     | omnissa horizon - brave                                                           |            90 |        1.02 |
| 5    | title_bucket     | straive darwinbox - brave                                                         |            88 |        1.15 |
| 6    | title_bucket     | gemini notebook - system design - gemini notebook                                 |            60 |        4.44 |
| 7    | domain           | google.com                                                                        |            53 |        0.42 |
| 8    | title_bucket     | timeline - workgraph - brave                                                      |            42 |        0.09 |
| 9    | title_bucket     | gemini notebook - python - gemini notebook                                        |            40 |         5.7 |
| 10   | title_bucket     | google chat - bhavvishyya mandalapu - chat                                        |            38 |        0.75 |
| 11   | title_bucket     | gemini notebook                                                                   |            25 |        0.28 |
| 12   | title_bucket     | 💻 core concepts & implementation - genai for developers: building intelligent ap |            20 |        0.07 |
| 13   | title_bucket     | google chat - mcp - core devs - chat                                              |            19 |        0.05 |
| 14   | title_bucket     | 8079.00 ▼ (-6.06%) crudeoilm26augfut - brave                                     |            18 |        0.01 |
| 15   | title_bucket     | straive.darwinbox.com/ms/db/home - brave                                          |            18 |        0.01 |
| 16   | title_bucket     | google chat - pavan kaki - chat                                                   |            16 |        0.04 |
| 17   | title_bucket     | time management - brave                                                           |            16 |         0.0 |
| 18   | title_bucket     | service logs\| elastic container service \| us-east-1 - brave                     |            15 |        0.04 |
| 19   | title_bucket     | warner bros. discovery - prod - sign in - brave                                   |            15 |        0.02 |
| 20   | title_bucket     | 8080.00 ▼ (-6.05%) crudeoilm26augfut - brave                                     |            14 |        0.01 |
| 21   | title_bucket     | untitled - brave                                                                  |            14 |         0.0 |
| 22   | title_bucket     | 8078.00 ▼ (-6.07%) crudeoilm26augfut - brave                                     |            13 |        0.01 |
| 23   | domain           | eportal.incometax.gov.in                                                          |            12 |        0.16 |
| 24   | title_bucket     | youtube                                                                           |            12 |        0.07 |
| 25   | title_bucket     | pipelines\| codepipeline \| us-east-1 - brave                                     |            12 |        0.02 |
| 26   | title_bucket     | 8083.00 ▼ (-6.01%) crudeoilm26augfut - brave                                     |            12 |         0.0 |
| 27   | title_bucket     | investment suggestions for growth - brave                                         |            11 |        0.15 |
| 28   | title_bucket     | meet – zbk-hykr-hrq - brave                                                      |            11 |        0.12 |
| 29   | title_bucket     | abu dhabi job prep - brave                                                        |            11 |        0.06 |
| 30   | title_bucket     | genai4devs · gitlab - brave                                                      |            11 |        0.04 |
| 31   | title_bucket     | inbox (297) - parashar.sangle@straive.com - straive.com mail - brave              |            11 |        0.03 |
| 32   | title_bucket     | genai for developers: building intelligent applications - brave                   |            11 |        0.02 |
| 33   | title_bucket     | minor changes create server by pkaki12 · pull request#200 · wcet-enterprise-tec |            11 |        0.01 |
| 34   | title_bucket     | meet - brave                                                                      |            11 |         0.0 |
| 35   | title_bucket     | 8082.00 ▼ (-6.02%) crudeoilm26augfut - brave                                     |            10 |        0.02 |
| 36   | title_bucket     | 8071.00 ▼ (-6.15%) crudeoilm26augfut - brave                                     |            10 |        0.01 |
| 37   | title_bucket     | appcentral - brave                                                                |            10 |        0.01 |
| 38   | title_bucket     | google chat - bhavvishyya mandalapu messaged you - chat                           |             9 |        0.01 |
| 39   | title_bucket     | 8081.00 ▼ (-6.03%) crudeoilm26augfut - brave                                     |             9 |         0.0 |
| 40   | title_bucket     | naukri toptier - brave                                                            |             8 |        0.69 |
| 41   | title_bucket     | promotions (34) - parashar.sangle@straive.com - straive.com mail - brave          |             8 |        0.17 |
| 42   | title_bucket     | aqua-4/workgraph: privacy-first work telemetry for engineers. track apps, browse  |             8 |        0.03 |
| 43   | title_bucket     | ide · genai4devs / vashist-implementation / module1 · gitlab - brave            |             8 |        0.02 |
| 44   | title_bucket     | 8074.00 ▼ (-6.12%) crudeoilm26augfut - brave                                     |             8 |         0.0 |
| 45   | title_bucket     | chatgpt - brave                                                                   |             8 |         0.0 |
| 46   | title_bucket     | straive darwinbox : login - brave                                                 |             8 |         0.0 |
| 47   | title_bucket     | group members · genai4devs · gitlab - brave                                     |             7 |        0.17 |
| 48   | title_bucket     | /mcp-gw-use1-vpc1/qa/mcp-gw-secrets\| secrets manager \| us-east-1 - brave        |             7 |        0.07 |
| 49   | title_bucket     | mcp platform - comp offs - parashar.sangle@straive.com - straive.com mail - brav  |             7 |        0.05 |
| 50   | title_bucket     | vashist-implementation · gitlab - brave                                          |             7 |        0.01 |

## Goals

1. Reduce untagged sessions significantly without data loss.
2. Preserve user-reviewed tags and avoid destructive retagging.
3. Make tag review queues more actionable by splitting broad browser buckets.
4. Keep behavior predictable, testable, and reversible.

## Non-Goals (for this slice)

1. Perfect 100% auto-tagging.
2. Auto-generating fragile YAML rules directly from free-form browser titles.
3. Replacing existing tagging architecture.

## High-Impact Strategy

### Stream A0: Dedicated Browser Handling (New)

Add a browser-specific normalization and tagging path so repeated title variants collapse into stable groups before review or tagging.

Why this is needed:

- Untagged browser rows are heavily concentrated in repeated title patterns.
- Current grouping treats many equivalent titles as separate long-tail buckets.
- Domain is often missing for browser sessions, so title handling must be stronger.

Design summary:

1. Browser Title Normalizer
2. Browser Tag Handler (tagging pipeline hook)
3. Browser Review Tab (separate workflow)

#### A0.1 Browser Title Normalizer

Goal:

- Convert noisy browser titles into canonical labels that are stable for grouping and bulk actions.

Normalization pipeline:

1. Lowercase and trim.
2. Strip browser suffixes (for example: "- brave", "- chrome", "- edge").
3. Normalize separators and punctuation (hyphen, en dash, em dash, pipe, bullets, colons) into spaces.
4. Collapse repeated whitespace to a single space.
5. Remove volatile tokens where safe:
   - meeting room codes
   - inbox counters like "(297)"
   - trailing browser product names
6. Apply ordered substring and regex rules to emit a canonical browser label.

Enhanced matching rules:

1. Matching must be case-insensitive.
2. Matching must be normalization-based, not fuzzy: apply the same normalization pipeline to both title and pattern.
3. Support explicit pattern alternatives using a pipe separator, for example "pipelines | codepipeline".
4. Evaluate longest normalized patterns first so specific rules win before broad ones.
5. Keep deterministic tie-breaking for equal-length patterns by preserving file order.

Recommended matching passes:

1. Pass A: strict normalized substring check.
2. Pass B: relaxed punctuation-tolerant check only if Pass A has no match.

Guardrails:

1. Keep the pattern list curated; do not auto-learn from free-form titles.
2. Avoid very short generic patterns unless bounded, to reduce false positives.
3. Re-run focused tests whenever new patterns are introduced.

Initial canonical mappings (seed set):

- "journal - workgraph - brave" -> "Workgraph"
- "dashboard - workgraph - brave" -> "Workgraph"
- "timeline - workgraph - brave" -> "Workgraph"
- "google chat - <person></person> - chat" -> "Google Chat"
- "meet – <room-code></room> - brave" or "meet - brave" -> "Google Meet"
- "inbox (...) - ... - straive.com mail - brave" -> "Work Gmail"
- "chatgpt - brave" -> "ChatGPT"

Guardrail:

- Keep this mapping curated and explicit. Do not auto-learn from free-form titles in this phase.

#### A0.2 Browser Tag Handler

Goal:

- Add browser-aware tagging before generic keyword fallback, especially when domain is absent.

Recommended precedence for browser sessions:

1. repo match
2. domain match
3. normalized browser title match
4. generic app and keyword fallback

Expected outcome:

- Fewer browser sessions remain untagged solely due to missing domain.

#### A0.3 Browser Review Tab

Goal:

- Provide a dedicated browser queue separate from generic app review.

Core UX:

1. Filter to browser apps only.
2. Group by:
   - domain (when available)
   - canonical browser label (from normalizer)
3. Bulk assign tags per browser group.
4. Show representative sample titles for confidence checks.

Benefits:

- Reduces noise from app-wide browser buckets.
- Makes repeated browser patterns quickly actionable.

Decision:

- Add a separate browser-only missing-tags tab in Phase 1.

Tab scope:

1. Show only sessions where app is a browser and tag is empty.
2. Keep this tab focused on missing tags only (no weak-tag mode in first pass).
3. Exclude non-browser apps from this workflow entirely.

Proposed navigation and route:

1. New nav item: Browser Tag Review
2. Route: /browser-tag-review
3. API base: /api/browser-tag-review/*

Proposed API endpoints:

1. GET /api/browser-tag-review/groups
2. GET /api/browser-tag-review/groups/{group_type}/{group_value}
3. POST /api/browser-tag-review/assign-group

Grouping behavior:

1. Prefer domain when present.
2. Else use canonical browser label from the normalizer.
3. Else fallback to coarse title bucket for manual triage.

Assignment behavior:

1. Bulk assign selected tag to all matched sessions in the browser group.
2. Record one review audit row per affected session.
3. Refresh browser groups immediately after write.

YAML behavior:

1. Include the same YAML Preview panel directly in Browser Tag Review.
2. Allow YAML download from the browser-only workflow without switching back to the main Tag Review tab.
3. Reuse the same suggestion-generation and YAML download flow as Tag Review so the output stays consistent.

Out of scope for first browser tab slice:

1. YAML auto-apply.
2. Cross-app grouping.
3. Auto-learning new title rules from free-form titles.

#### A0.4 Implementation Targets

Primary files:

- services/tag_review.py
- services/activity_tagger.py
- api/app.py
- api/templates/tag_review.html

Potential new files:

- services/browser_title_normalizer.py
- api/templates/browser_review.html

Tests to extend:

- tests/test_tag_review_service.py
- tests/test_tag_review_api.py
- tests/test_v11_features.py

#### A0.5 Acceptance Metrics

Track before and after for browser-only untagged rows:

1. Count of untagged browser sessions.
2. Share of app-only browser buckets vs canonical browser groups.
3. Count reduction in top long-tail title variants.
4. Net drop in total untagged sessions after safe retag.

Success definition for first pass:

- Top repeated browser title variants collapse into canonical labels.
- Browser review queue becomes dominated by meaningful groups, not near-duplicate titles.
- Safe retag updates increase while overwrite risk remains zero.

### Stream A: Browser Context Enrichment for Review Buckets

Improve browser title grouping in tag review so large app buckets (for example Brave) split into meaningful browser_context buckets.

Target files:

- services/tag_review.py
- tests/test_tag_review_service.py
- tests/test_repository.py
- tests/test_tag_review_api.py

Proposed changes:

1. Expand known browser context patterns beyond current limited set.
2. Normalize common browser title suffixes and separators more aggressively.
3. Add conservative context labels for repeated patterns, for example:
   - Google Meet
   - Google Chat
   - LinkedIn
   - Gemini Notebook
   - Darwinbox
   - Horizon
4. Keep precedence unchanged:
   - repo
   - domain
   - browser_context
   - app

Important guardrail:

- browser_context remains a review aid first; avoid directly converting raw title strings into permanent YAML rules in MVP.

### Stream B: Better Domain Recovery (Collector Side)

Improve domain extraction quality for browser sessions where possible.

Target files:

- collector/browser_tracker.py
- tests/test_browser_tracker.py

Proposed changes:

1. Improve title-based host extraction for common title variants.
2. Make history title matching more resilient to punctuation and separators.
3. Add tests for high-frequency real-world title forms.

Expected effect:

- Fewer sessions fall back to app-only buckets.
- More sessions become domain-grouped and easier to tag consistently.

### Stream C: Safe Retag as Default Operational Path

Safe retag has already been introduced and should be the standard remediation command.

Command:

uv run python main.py --retag-existing --safe

Behavior:

- Updates only empty tags.
- Does not overwrite existing tagged sessions.

Operational recommendation:

1. Use safe retag after rule improvements.
2. Reserve full retag for explicit migration windows only.

## Proposed Rollout Plan

### Phase 1: Browser Context Expansion (Review UX impact first)

1. Add curated browser_context patterns based on observed frequent titles.
2. Add tests validating grouping outcomes for these patterns.
3. Verify tag-review groups split large Brave bucket into smaller actionable buckets.

Success criteria:

- Reduced proportion of app-only Brave groups.
- Increased browser_context groups with meaningful labels.

### Phase 2: Domain Recovery Improvements

1. Improve extraction/matching in browser tracker.
2. Add deterministic tests for domain detection scenarios.
3. Validate new sessions include domain more often where expected.

Success criteria:

- Reduced untagged sessions with empty domain and empty repo.
- Improved domain-based grouping share.

### Phase 3: Controlled Safe Retag + Review

1. Run safe retag.
2. Review untagged delta and top remaining buckets.
3. Use tag-review bucket assignment for residual cases.

Success criteria:

- Meaningful drop in total untagged counts.
- No regressions from overwriting manually tagged sessions.

## Validation and Test Plan

1. Unit tests
   - browser_context derivation patterns and precedence
   - domain normalization and extraction edge cases
2. Repository/API tests
   - grouped bucket composition after pattern expansion
   - detail endpoint and group assignment behavior remains stable
3. Operational checks
   - pre and post counts for:
     - total untagged
     - untagged with app=brave and no domain
     - untagged with no repo and no domain

## Risk Assessment

1. Over-grouping risk
   - Too-broad patterns could merge unrelated browsing activity.
   - Mitigation: conservative pattern set and focused tests.
2. False-positive domain extraction
   - Aggressive parsing may infer incorrect hosts.
   - Mitigation: strict host validation and test coverage.
3. Rule pollution risk
   - Turning free-form titles into permanent rules can create noise.
   - Mitigation: keep browser_context as review-only for rule generation in MVP.

## Rollback and Safety

1. Keep DB backups before non-safe retag operations.
2. Use safe retag for routine cleanup.
3. Revert pattern additions if grouping quality declines.

## Open Review Questions

1. Which browser_context labels should be included in the first curated set?
2. Should generic contexts (for example New Tab) be deprioritized or hidden by default in the UI?
3. Should we add an optional fallback tag for long-tail untagged browser sessions, or keep them explicitly untagged for manual review?

## Approval Gate

No code changes for Stream A or Stream B should be applied until this approach is reviewed and approved.



## Pattern Matching Logic (Finalized)

1. Compare using case-insensitive normalized text.
2. Normalize both title and pattern with identical rules before matching.
3. Support explicit alternatives in a pattern using the pipe separator.
4. Match longest normalized patterns first.
5. Use stable file order to break ties when pattern lengths are equal.
6. If strict normalized matching fails, use a limited punctuation-tolerant fallback.
