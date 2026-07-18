# Missed Tag Review Page - Implementation Proposal

## Goal

Add a dedicated page that helps users classify untagged or incorrectly tagged activity sessions, then convert those manual corrections into reusable tagging rules that can be written to config/my-tags.yaml or exported as a downloadable YAML file.

This improves long-term tagging quality and reduces repeated manual cleanup.

## Current State Summary

- Tags are loaded by ActivityTagger from config/tags.yaml plus config/my-tags.yaml, with custom tags overriding defaults by tag name.
- Dashboard and timeline already display tags and show untagged sessions.
- There is no workflow to discover missed entries and promote manual tagging decisions into new rule definitions.

## Proposed User Experience

### New Navigation Item

Add a new nav entry: Tag Review.

- Visible only in standalone and sync-client modes.

### Page: Tag Review

Route: GET /tag-review

Sections:

1. Review Filters

- Date range (default last 7 days).
- Only untagged toggle (default true).
- Include weakly tagged entries toggle (optional phase 2).
- App/domain/repo quick filters.

2. Grouped Review Queue

- Show grouped candidate buckets instead of individual session rows.
- Primary grouping dimensions:
  - app_name
  - browser_domain
  - git_repo
- Browser-aware sub-grouping for browser apps:
  - domain-first when browser_domain is present
  - title-aware fallback when browser_domain is missing or too generic
  - app-only only when neither domain nor useful title signal exists
- Each bucket shows:
  - grouping type, for example domain or repo
  - grouping value, for example github.com or workgraph
  - session count
  - total active time
  - recent sample window titles or apps
  - current tag distribution, if mixed
- Actions per bucket:
  - Assign existing tag to all matching sessions in the bucket.
  - Create new tag and assign to the bucket.
  - Expand bucket to inspect sample sessions.
  - Skip.

3. Rule Suggestions Panel

- Groups manual assignments into candidate rule snippets:
  - domains
  - repos
  - keywords (existing keywords plus explicit app-bucket assignments)
- Shows confidence and estimated impact, for example: affects 23 historical sessions.

4. YAML Update Actions

- Preview merged YAML diff.
- Download generated YAML file instead of direct write.
- Apply to config/my-tags.yaml in a later phase after preview/export behavior is validated.

## High-Level Architecture

### Data Source

Use activity_sessions as the initial source in standalone and sync-client modes.

Candidate query conditions:

- tag IS NULL OR tag = '' for missed entries.
- optional future: suspicious tag quality heuristics.

Grouping strategy:

- Build review buckets from untagged sessions grouped by repo, domain, and app.
- Prefer repo and domain buckets over app-only buckets when stronger signals exist.
- Avoid showing the same session in multiple primary buckets in the same pass.
- Keep app-only buckets as a fallback for sessions with no repo and no domain.

Browser edge case handling:

- Do not treat the browser app name itself, for example Brave or Chrome, as the main grouping key when a stronger browsing signal exists.
- Prefer browser_domain when present because a single browser can span multiple categories.
- When browser_domain is missing, empty, or too generic, derive a browser_context bucket from window_title.
- Example outcomes:
  - "Brave - compare text and find differences online or offline - Diffchecker - Brave" -> Development-oriented browser_context or diffchecker.com domain if available
  - "Brave - New Tab - Brave" -> generic browser bucket, usually skipped or deprioritized
  - "Brave - YouTube Music" -> Entertainment-oriented browser_context or music.youtube.com / youtube.com domain bucket if available

Recommended browser grouping precedence:

1. repo
2. browser_domain
3. browser_context from window_title
4. app_name fallback

### New Persistence for Review Workflow

Add a lightweight review audit table so page actions are recoverable and auditable.

Suggested table: tag_review_actions

Fields:

- id INTEGER PRIMARY KEY
- session_id INTEGER NOT NULL
- session_uuid TEXT NULL
- original_tag TEXT NULL
- selected_tag TEXT NOT NULL
- reason TEXT NULL
- source_signal TEXT NULL
- created_at TEXT NOT NULL
- applied_to_rules INTEGER NOT NULL DEFAULT 0

Why:

- Records explicit user review actions even when bucket-level tagging updates sessions immediately.
- Allows generation of suggestions from reviewed examples.
- Supports rollback, debugging, and later sync propagation for manual overrides.

Recommended extension:

- Add optional group metadata fields later if needed:
  - group_type, for example repo, domain, app
  - group_value
  - group_label for friendlier browser-context display

This is not required for MVP because the affected sessions already capture the underlying grouping signal.

## API Design

### 1) Fetch grouped candidates

GET /api/tag-review/groups

Query params:

- days (default 7)
- only_untagged (default true)
- app_name, domain, repo
- limit, offset

Response includes:

- groups list
- total group count
- current filters

Each group includes:

- group_type
- group_value
- group_label
- session_count
- total_seconds
- sample_sessions
- dominant_apps
- current_tags

### 2) Inspect a group

GET /api/tag-review/groups/{group_type}/{group_value}

Response includes:

- matching sessions
- sample titles
- date coverage
- summary statistics
- any derived browser context clues used for the grouping

### 3) Save group assignments

POST /api/tag-review/assign-group

Payload:

- group_type
- group_value
- selected_tag
- reason (optional)

Behavior:

- Resolve all matching sessions in the group.
- Insert one audit row per affected session into tag_review_actions.
- Update activity_sessions.tag immediately for all matching sessions so analytics improve now.
- Return affected session count and updated summary.

### 4) Generate rule suggestions

POST /api/tag-review/suggestions

Input:

- optional range and minimum frequency thresholds.

Output:

- grouped candidate rules per tag:
  - repos list
  - domains list
  - keywords list
  - sample_count per token
  - estimated historical matches

### 5) YAML preview

GET /api/tag-review/yaml-preview

Output:

- merged YAML text (existing my-tags + proposed additions)
- warnings (conflicts, duplicates, low-confidence keywords)

### 6) Apply YAML update

Phase 2 only.

POST /api/tag-review/yaml-apply

Payload:

- strategy: merge or replace_tag_block
- selected suggestions by tag
- backup boolean

Behavior:

- writes backup file first, for example backups/my-tags.YYYYMMDD-HHMMSS.yaml
- updates config/my-tags.yaml
- returns updated file hash and summary

### 7) Download generated YAML

POST /api/tag-review/yaml-download

Output:

- downloadable YAML attachment containing only new suggestions or full merged rules.

## Rule Generation Heuristics

Build suggestions from reviewed group assignments with conservative defaults.

1. Domain suggestions

- Extract normalized host from browser_domain.
- Keep tokens with frequency >= min_domain_hits, default 2.
- Prefer exact domain roots, for example github.com over subdomain noise.

2. Repo suggestions

- Extract stable repo identifier from git_repo path.
- Keep tokens with frequency >= min_repo_hits, default 2.

Browser context fallback:

- If browser_domain is unavailable but the bucket came from a browser_context derived from window_title, use that only for review UX, not as a reusable YAML rule source.
- Do not generate new YAML rules directly from raw browser window titles in MVP.
- Instead, use browser_context buckets to help the user bulk-tag sessions, then rely on future domain captures or explicit YAML edits for reusable rules.

App-only fallback:

- Keep browser_context grouping as review-only in MVP and do not generate rules from raw browser titles.
- For app-only buckets, include explicit app assignments as keyword candidates in YAML preview/download.
- Continue to prefer repo/domain signals for reusable structural rules.

3. Keyword suggestions

- Tokenize window_title and app_name.
- Remove stop words and very short tokens.
- Keep phrases seen repeatedly with the same selected_tag.
- Exclude overly generic terms like code, browser, tab unless user explicitly opts in.

MVP constraint:

- Keep keyword generation conservative.
- Surface existing keywords from config/my-tags.yaml and merge explicit app-bucket assignments as keyword candidates.
- Do not generate keywords from raw browser_context titles in MVP.

4. Collision checks

- If a candidate token is already used by another tag, mark as conflict and require explicit confirmation.

## YAML Merge Strategy

Target format remains:

- top-level tags mapping
- each tag has repos, domains, keywords lists

Merge behavior:

1. Load existing config/my-tags.yaml.
2. Ensure target tag blocks exist.
3. Append only unique values, case-insensitive dedupe.
4. Preserve stable sorting for deterministic diffs.
5. Keep comments best-effort, but accept that YAML rewrite may drop comments unless using a round-trip parser.

Implementation note:

- If comment preservation is important, use ruamel.yaml for round-trip editing.
- If not, PyYAML is simpler and already present.

## Mode-Specific Behavior

### standalone

- Fully enabled.
- Local updates to activity_sessions and config/my-tags.yaml.

### sync-client

- Enabled for local data.
- Same local YAML update/download behavior.
- Manual session tag overrides should sync as session-level data for multi-device consistency.
- YAML rule changes remain local-only in MVP and should not sync to server.

### sync-server

- Default: disable page with explanatory message.
- Reason: rules are personal/local policy and may differ per device/user.


## Security and Safety

1. Path safety

- Only allow writing under config/my-tags.yaml for apply endpoint.
- Reject arbitrary file paths from client payload.

2. Input validation

- Validate tag names and token lengths.
- Reject malformed YAML structures in replace flows.

3. Backup before write

- Always create backup snapshot before apply.

4. Dry-run support

- yaml-preview endpoint should be usable without write permissions.

## Suggested Implementation Phases

### Phase 1: MVP

- Tag Review page with grouped untagged buckets.
- Group assign action updates matching session tags + review table.
- Suggestion generator for domains/repos from reviewed groups, plus keyword enrichment from explicit app-bucket assignments.
- Browser-aware grouping that can split browser activity by domain or title-derived browser context.
- YAML preview + download only.

### Phase 2: Apply and refine

- YAML apply endpoint with backup.
- Keyword suggestion heuristics.
- Conflict resolution UI.

### Phase 3: Advanced quality

- Confidence scoring and impact simulation.
- Bulk actions and keyboard shortcuts.
- Optional background retag job to reprocess historical sessions after rule updates.

## Testing Strategy

1. Unit tests

- suggestion extraction from reviewed group assignments
- collision detection
- YAML merge idempotency and dedupe
- browser grouping precedence, including browser_domain versus browser_context fallback

2. API tests

- grouped candidate aggregation and pagination
- group assign endpoint updates review table and matching session tags
- yaml preview/apply/download responses
- browser sessions with different titles/domains land in different review buckets when appropriate

3. Integration tests

- full workflow: review -> suggest -> preview -> apply -> tagger reload check

4. Regression tests

- ensure existing journal and dashboard routes are unchanged
- ensure sync-server mode still hides unsupported pages

## Resolved Decisions

1. Manual assignment should immediately update activity_sessions.tag after explicit user action.
2. Keywords stay conservative in MVP: surface existing keywords and merge explicit app-bucket assignments; do not generate title-derived browser_context keywords.
3. Strict comment preservation is not required when rewriting config/my-tags.yaml.
4. In sync-client mode, manual reviewed session assignments should sync as session data, but YAML rule changes remain local-only.

## Implementation Plan

### Phase 1 Delivery Goal

Deliver a review page that lets a user find grouped untagged activity buckets, assign a tag to all matching sessions immediately, accumulate reviewed examples, and export a YAML preview for future rule updates.

### Step 1: Data model and repository helpers

Files:

- db/schema.sql
- db/repository.py
- tests/test_repository.py

Work:

- Add a new tag_review_actions table to the schema.
- Add repository helpers to:
  - list grouped review buckets
  - list sessions within a selected bucket
  - insert a review action
  - update session tags for a group of session ids
  - query reviewed actions for suggestion generation
- Keep the new helpers local-only in Phase 1.

Notes:

- The table should behave as an audit log, not as a second source of truth for current tag state.
- activity_sessions.tag remains the canonical local tag for dashboard and reporting.
- Group assignment is only a review workflow convenience, not a new persisted entity.

### Step 2: Tag review service layer

Files:

- services/activity_tagger.py
- services/tag_review.py (new)
- tests/test_reporting_features.py or a new focused test file

Work:

- Add a small service module for:
  - normalizing repo and domain candidates
  - building grouped review buckets by repo, domain, and app
  - deriving browser_context buckets from browser window titles when domain data is missing or too generic
  - grouping reviewed actions by selected_tag
  - generating repo/domain suggestions with counts
  - loading current config/my-tags.yaml and preparing merged preview output
  - marking conflicts where a repo or domain is already assigned to another tag
- Reuse the current tag loading shape so suggestions match the existing YAML format.

Notes:

- Keep keyword handling conservative in MVP.
- If a selected tag already exists in my-tags.yaml, merge into that block.
- If a selected tag is new, create an empty tag block and populate reviewed repo/domain suggestions plus explicit app-bucket keyword assignments.
- If a reviewed group is app-only and has no repo/domain signal, keep the manual tag update and include the app name as a keyword candidate.
- If a reviewed group is browser_context-only and has no repo/domain signal, use it for bulk tagging only and do not generate a reusable YAML rule in MVP.

### Step 3: API endpoints and route wiring

Files:

- api/app.py
- tests/test_api_journal.py or a new test file such as tests/test_tag_review_api.py

Work:

- Add GET /tag-review for the HTML page.
- Add GET /api/tag-review/groups.
- Add GET /api/tag-review/groups/{group_type}/{group_value}.
- Add POST /api/tag-review/assign-group.
- Add POST /api/tag-review/suggestions.
- Add GET /api/tag-review/yaml-preview.
- Add POST /api/tag-review/yaml-download.
- Gate the page and APIs to standalone and sync-client modes only.

Behavior:

- POST /api/tag-review/assign-group should:
  - validate selected_tag
  - resolve the affected sessions for the selected bucket
  - write one row to tag_review_actions per affected session
  - immediately update activity_sessions.tag for all affected sessions
  - return affected count, sample rows, and a success flag
- In sync-client mode, structure the code so the manual override can later flow into sync session updates without rewriting the API contract.

Notes:

- Do not add yaml-apply in Phase 1.
- For download, return generated YAML as an attachment rather than writing to disk.

### Step 4: UI page and interaction flow

Files:

- api/templates/base.html
- api/templates/tag_review.html (new)
- api/static/* if shared JavaScript or styles are needed

Work:

- Add a Tag Review nav link for standalone and sync-client.
- Create a page with:
  - filter form
  - grouped review table
  - bucket assignment controls
  - expandable sample-session inspector
  - clear display of bucket source, for example repo, domain, or browser context
  - suggestion summary panel
  - YAML preview/download controls
- Keep the UI close to current dashboard and timeline patterns.

Interaction flow:

1. User opens Tag Review.
2. Client loads grouped untagged buckets from the API.
3. User reviews a bucket, optionally expands it to inspect sample sessions.
4. User assigns a tag to the entire bucket.
5. UI refreshes the grouped queue and suggestion panel.
6. User previews generated YAML.
7. User downloads YAML for manual merge or replacement later.

Notes:

- Prefer simple server-rendered HTML plus small fetch calls instead of a large client-side framework.
- Show affected session count and a clear "updated locally" status after group assignment.

### Step 5: Sync-client follow-up hook

Files:

- services/sync_worker.py
- services/sync_daemon.py
- tests/test_sync_worker.py

Work:

- Do not fully implement cross-device propagation in the first UI slice unless it is already straightforward.
- Add a clear integration seam so a reviewed manual override can be pushed as a session update.
- Document that the canonical sync behavior is:
  - sync manual session tag overrides
  - do not sync YAML policy files

Notes:

- This may be a Phase 1.5 task if current sync payloads already support tag updates cleanly.
- If not, keep the UI local first and add explicit follow-up work rather than blocking the page.

### Step 6: Test plan for the first implementation pass

Add focused coverage for:

- grouped candidate query returns expected repo/domain/app buckets by default
- browser sessions split correctly across domain and browser-context buckets
- group assignment writes audit rows and updates matching session tags immediately
- suggestion generation groups repos/domains correctly
- preview output dedupes entries case-insensitively
- sync-server mode rejects or hides the route
- yaml download returns attachment content in expected structure

### Suggested execution order

1. Add grouped repository helpers.
2. Add grouping and YAML preview service code.
3. Add grouped API endpoints and tests.
4. Add the Tag Review template and navigation.
5. Add group-inspector and download workflow.
6. Add sync-client propagation hook if the tag update path is already stable.

## Concrete Task Checklist

Status legend:

- [x] Implemented
- [~] Partially implemented or documented but not fully wired
- [ ] Not implemented yet

### Implemented So Far Snapshot

- [x] Audit table, grouped repository helpers, and grouped bulk tag updates are implemented.
- [x] Tag Review page, grouped APIs, YAML preview/download, and focused tests are implemented.
- [x] Sync manual-override propagation is covered by a focused sync test.
- [x] Browser edge-case handling is now implemented with browser_context-aware grouping and focused tests.
- [x] Legacy per-session tag-review endpoints have been removed so the grouped workflow is the only supported path.

### Database and repository

- [x] Add tag_review_actions DDL to db/schema.sql.
- [x] Add a helper in db/repository.py to ensure the new table exists during migrations or startup.
- [x] Add a repository function such as list_tag_review_groups(db_path, days, only_untagged, app_name, domain, repo, limit, offset).
- [x] Add a repository function such as count_tag_review_groups(db_path, days, only_untagged, app_name, domain, repo).
- [x] Add a repository function such as list_tag_review_group_sessions(db_path, group_type, group_value, limit).
- [x] Make the grouping resolver browser-aware so browser sessions can fall back to browser_context derived from window_title when domain is missing.
- [x] Add a repository function such as create_tag_review_action(db_path, session_id, original_tag, selected_tag, reason, source_signal).
- [x] Add a repository function such as update_activity_session_tags(db_path, session_ids, selected_tag).
- [x] Add a repository function such as list_review_actions_for_suggestions(db_path, days=None, selected_tag=None).
- [x] Add tests in tests/test_repository.py for group aggregation, audit row insertion, and immediate grouped tag updates.

### Tag review service

- [x] Create services/tag_review.py.
- [x] Add a helper such as normalize_repo_candidate(git_repo) that extracts a stable repo token.
- [x] Add a helper such as normalize_domain_candidate(browser_domain) that strips noise and lowercases domains.
- [x] Add a helper such as build_tag_review_groups(sessions) that prioritizes repo/domain/app buckets.
- [x] Add a helper such as derive_browser_context(window_title, app_name) that extracts meaningful browser review buckets from titles like Diffchecker or YouTube Music.
- [x] Add a helper such as load_custom_tag_rules(config_path=None) for config/my-tags.yaml access.
- [x] Add a helper such as build_tag_review_suggestions(review_actions, existing_rules) returning grouped repo/domain suggestions.
- [x] Include explicit app-bucket assignments as keyword candidates in build_tag_review_suggestions output.
- [x] Add a helper such as build_yaml_preview(existing_rules, selected_suggestions) returning preview text and warning metadata.
- [x] Add a helper such as find_rule_conflicts(existing_rules, candidate_rules) to flag collisions across tags.
- [x] Keep keyword output read-only by exposing only existing keywords for the selected tag.
- [x] Add focused tests in a new test file such as tests/test_tag_review_service.py.

### API routes

- [x] In api/app.py, add a guard helper such as _ensure_tag_review_features_enabled() that rejects sync-server mode.
- [x] Add GET /tag-review to render the new page.
- [x] Add GET /api/tag-review/groups.
- [x] Add GET /api/tag-review/groups/{group_type}/{group_value}.
- [x] Add POST /api/tag-review/assign-group.
- [x] Add POST /api/tag-review/suggestions.
- [x] Add GET /api/tag-review/yaml-preview.
- [x] Add POST /api/tag-review/yaml-download.
- [x] Add request models for assignment and suggestion inputs if payload validation is needed.
- [x] Make POST /api/tag-review/assign-group call repository write functions in this order:
  1. resolve group sessions
  2. insert audit row for each affected session
  3. update activity_sessions.tag for affected session ids
  4. return affected count and updated summary
- [x] Add route tests in a new file such as tests/test_tag_review_api.py.

### Template and frontend behavior

- [x] Add a nav link in api/templates/base.html for Tag Review.
- [x] Create api/templates/tag_review.html.
- [x] Reuse the existing dashboard/timeline card and table styling where practical.
- [x] Add a filter form with days, only_untagged, app_name, domain, and repo inputs.
- [x] Add a grouped candidate list region that renders bucket metadata and tag controls.
- [x] Add an expandable sample-session region per bucket.
- [x] Distinguish visually between repo buckets, domain buckets, browser-context buckets, and app fallback buckets.
- [x] Add a suggestion panel that renders grouped repo/domain suggestions by selected tag.
- [x] Add a YAML preview region and a download button.
- [x] Use small fetch-based interactions rather than a heavy client-side app.
- [x] Show optimistic success state only after the group-assign API confirms the write.

### Sync-client seam

- [x] Identify where manual session tag updates can be included in sync payload updates.
- [x] Add a small seam in services/sync_worker.py or adjacent sync code so a later change can propagate reviewed tag overrides without changing the tag review UI contract.
- [x] Keep the first implementation safe even if cross-device propagation is deferred.

### Validation checklist before coding complete

- [x] Tag Review link appears only in standalone and sync-client modes.
- [x] Grouped candidates load correctly from local activity_sessions.
- [x] Browser rows such as Diffchecker, New Tab, and YouTube Music fall into sensible separate review buckets.
- [x] Assigning a tag to a bucket updates dashboard-visible data immediately.
- [x] Suggestions are generated only from reviewed actions.
- [x] Existing keywords from config/my-tags.yaml remain visible, and explicit app-bucket assignments appear as keyword candidates in YAML preview/download.
- [x] YAML preview is deterministic and case-insensitive dedupe works.
- [x] YAML download returns valid YAML without mutating config/my-tags.yaml.
- [x] Sync-server mode returns a clear unsupported response for page and API routes.

## Recommended First Slice

Start with a low-risk first slice:

- Add Tag Review page in standalone and sync-client.
- Support grouped tagging by repo, domain, and app, with reusable suggestions generated from repo/domain and explicit app assignments merged as keywords.
- Include browser-aware grouping so mixed browser usage is split by domain or title-derived browser context rather than collapsing into one browser-app bucket.
- Provide YAML preview and download.
- Defer direct YAML file mutation until after user validates quality.

## Browser Edge Case Recommendation

Problem:

- A browser app such as Brave or Chrome can contain work, entertainment, research, and generic browsing in the same app.
- Grouping only by app_name would mix unrelated activities into one review bucket.
- Domain grouping helps when browser_domain is present, but some sessions may only have window titles.

Recommended solution:

1. Add a browser-specific review grouping layer.
2. Prefer browser_domain whenever it exists and is meaningful.
3. If browser_domain is missing, derive a browser_context from the window title.
4. Treat generic browser titles as low-value buckets and deprioritize them.

Suggested browser_context examples:

- Diffchecker from titles containing Diffchecker or compare text and find differences.
- YouTube Music from titles containing YouTube Music.
- New Tab from titles containing New Tab.

Practical rule:

- browser_context is a review aid, not a new YAML rule source in MVP.
- Repo/domain-derived signals should remain the primary reusable rule source, while explicit app-bucket assignments can be merged into keywords.

This keeps the review page useful for bulk cleanup without polluting my-tags.yaml with fragile title-based rules.

This delivers immediate value while minimizing accidental rule pollution.
