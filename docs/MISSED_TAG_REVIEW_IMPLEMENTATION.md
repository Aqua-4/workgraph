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

2. Missed Entries Queue

- Paginated table/card list of candidate sessions.
- Each row shows start/end, app, window title, browser domain, repo, current tag.
- Actions per row:
  - Assign existing tag.
  - Create new tag and assign.
  - Skip.

3. Rule Suggestions Panel

- Groups manual assignments into candidate rule snippets:
  - domains
  - repos
  - keywords
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

- Records explicit user review actions even when session tags are updated immediately.
- Allows generation of suggestions from reviewed examples.
- Supports rollback, debugging, and later sync propagation for manual overrides.

## API Design

### 1) Fetch candidates

GET /api/tag-review/candidates

Query params:

- days (default 7)
- only_untagged (default true)
- app_name, domain, repo
- limit, offset

Response includes:

- sessions list
- total count
- current filters

### 2) Save manual assignments

POST /api/tag-review/assign

Payload:

- session_id or session_uuid
- selected_tag
- reason (optional)

Behavior:

- Insert row into tag_review_actions.
- Update activity_sessions.tag immediately for that session so analytics improve now.

### 3) Generate rule suggestions

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

### 4) YAML preview

GET /api/tag-review/yaml-preview

Output:

- merged YAML text (existing my-tags + proposed additions)
- warnings (conflicts, duplicates, low-confidence keywords)

### 5) Apply YAML update

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

### 6) Download generated YAML

POST /api/tag-review/yaml-download

Output:

- downloadable YAML attachment containing only new suggestions or full merged rules.

## Rule Generation Heuristics

Build suggestions from reviewed actions with conservative defaults.

1. Domain suggestions

- Extract normalized host from browser_domain.
- Keep tokens with frequency >= min_domain_hits, default 2.
- Prefer exact domain roots, for example github.com over subdomain noise.

2. Repo suggestions

- Extract stable repo identifier from git_repo path.
- Keep tokens with frequency >= min_repo_hits, default 2.

3. Keyword suggestions

- Tokenize window_title and app_name.
- Remove stop words and very short tokens.
- Keep phrases seen repeatedly with the same selected_tag.
- Exclude overly generic terms like code, browser, tab unless user explicitly opts in.

MVP constraint:

- Do not generate new keyword suggestions yet.
- Only surface existing keywords already present in config/my-tags.yaml for the selected tag, so the review flow stays low-noise.

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

- Tag Review page with untagged queue.
- Manual assign action updates session tag + review table.
- Suggestion generator for domains/repos.
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

- suggestion extraction from reviewed samples
- collision detection
- YAML merge idempotency and dedupe

2. API tests

- candidate filtering and pagination
- assign endpoint updates review table and session tag
- yaml preview/apply/download responses

3. Integration tests

- full workflow: review -> suggest -> preview -> apply -> tagger reload check

4. Regression tests

- ensure existing journal and dashboard routes are unchanged
- ensure sync-server mode still hides unsupported pages

## Resolved Decisions

1. Manual assignment should immediately update activity_sessions.tag after explicit user action.
2. Keywords stay conservative in MVP: only surface keywords already present in config/my-tags.yaml for the selected tag.
3. Strict comment preservation is not required when rewriting config/my-tags.yaml.
4. In sync-client mode, manual reviewed session assignments should sync as session data, but YAML rule changes remain local-only.

## Implementation Plan

### Phase 1 Delivery Goal

Deliver a review page that lets a user find untagged sessions, assign a tag immediately, accumulate reviewed examples, and export a YAML preview for future rule updates.

### Step 1: Data model and repository helpers

Files:

- db/schema.sql
- db/repository.py
- tests/test_repository.py

Work:

- Add a new tag_review_actions table to the schema.
- Add repository helpers to:
  - list candidate sessions for review
  - insert a review action
  - update a session tag by session id
  - query reviewed actions for suggestion generation
- Keep the new helpers local-only in Phase 1.

Notes:

- The table should behave as an audit log, not as a second source of truth for current tag state.
- activity_sessions.tag remains the canonical local tag for dashboard and reporting.

### Step 2: Tag review service layer

Files:

- services/activity_tagger.py
- services/tag_review.py (new)
- tests/test_reporting_features.py or a new focused test file

Work:

- Add a small service module for:
  - normalizing repo and domain candidates
  - grouping reviewed actions by selected_tag
  - generating repo/domain suggestions with counts
  - loading current config/my-tags.yaml and preparing merged preview output
  - marking conflicts where a repo or domain is already assigned to another tag
- Reuse the current tag loading shape so suggestions match the existing YAML format.

Notes:

- Keep keyword handling read-only in MVP.
- If a selected tag already exists in my-tags.yaml, merge into that block.
- If a selected tag is new, create an empty tag block and populate only reviewed repo/domain suggestions.

### Step 3: API endpoints and route wiring

Files:

- api/app.py
- tests/test_api_journal.py or a new test file such as tests/test_tag_review_api.py

Work:

- Add GET /tag-review for the HTML page.
- Add GET /api/tag-review/candidates.
- Add POST /api/tag-review/assign.
- Add POST /api/tag-review/suggestions.
- Add GET /api/tag-review/yaml-preview.
- Add POST /api/tag-review/yaml-download.
- Gate the page and APIs to standalone and sync-client modes only.

Behavior:

- POST /api/tag-review/assign should:
  - validate selected_tag
  - write a row to tag_review_actions
  - immediately update activity_sessions.tag
  - return updated session summary and a success flag
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
  - candidate table
  - tag assignment controls
  - suggestion summary panel
  - YAML preview/download controls
- Keep the UI close to current dashboard and timeline patterns.

Interaction flow:

1. User opens Tag Review.
2. Client loads untagged candidates from the API.
3. User assigns a tag to one or more sessions.
4. UI refreshes the reviewed queue and suggestion panel.
5. User previews generated YAML.
6. User downloads YAML for manual merge or replacement later.

Notes:

- Prefer simple server-rendered HTML plus small fetch calls instead of a large client-side framework.
- Show the current tag and a clear "updated locally" status after assignment.

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

- candidate query returns only untagged sessions by default
- assignment writes audit row and updates session tag immediately
- suggestion generation groups repos/domains correctly
- preview output dedupes entries case-insensitively
- sync-server mode rejects or hides the route
- yaml download returns attachment content in expected structure

### Suggested execution order

1. Add schema and repository helpers.
2. Add suggestion and YAML preview service code.
3. Add API endpoints and tests.
4. Add the Tag Review template and navigation.
5. Add download workflow.
6. Add sync-client propagation hook if the tag update path is already stable.

## Concrete Task Checklist

### Database and repository

- Add tag_review_actions DDL to db/schema.sql.
- Add a helper in db/repository.py to ensure the new table exists during migrations or startup.
- Add a repository function such as list_tag_review_candidates(db_path, days, only_untagged, app_name, domain, repo, limit, offset).
- Add a repository function such as count_tag_review_candidates(db_path, days, only_untagged, app_name, domain, repo).
- Add a repository function such as create_tag_review_action(db_path, session_id, original_tag, selected_tag, reason, source_signal).
- Add a repository function such as update_activity_session_tag(db_path, session_id, selected_tag).
- Add a repository function such as list_review_actions_for_suggestions(db_path, days=None, selected_tag=None).
- Add tests in tests/test_repository.py for candidate filtering, audit row insertion, and immediate session tag update.

### Tag review service

- Create services/tag_review.py.
- Add a helper such as normalize_repo_candidate(git_repo) that extracts a stable repo token.
- Add a helper such as normalize_domain_candidate(browser_domain) that strips noise and lowercases domains.
- Add a helper such as load_custom_tag_rules(config_path=None) for config/my-tags.yaml access.
- Add a helper such as build_tag_review_suggestions(review_actions, existing_rules) returning grouped repo/domain suggestions.
- Add a helper such as build_yaml_preview(existing_rules, selected_suggestions) returning preview text and warning metadata.
- Add a helper such as find_rule_conflicts(existing_rules, candidate_rules) to flag collisions across tags.
- Keep keyword output read-only by exposing only existing keywords for the selected tag.
- Add focused tests in a new test file such as tests/test_tag_review_service.py.

### API routes

- In api/app.py, add a guard helper such as _ensure_tag_review_features_enabled() that rejects sync-server mode.
- Add GET /tag-review to render the new page.
- Add GET /api/tag-review/candidates.
- Add POST /api/tag-review/assign.
- Add POST /api/tag-review/suggestions.
- Add GET /api/tag-review/yaml-preview.
- Add POST /api/tag-review/yaml-download.
- Add request models for assignment and suggestion inputs if payload validation is needed.
- Make POST /api/tag-review/assign call repository write functions in this order:
  1. read current session
  2. insert audit row
  3. update activity_sessions.tag
  4. return updated session payload
- Add route tests in a new file such as tests/test_tag_review_api.py.

### Template and frontend behavior

- Add a nav link in api/templates/base.html for Tag Review.
- Create api/templates/tag_review.html.
- Reuse the existing dashboard/timeline card and table styling where practical.
- Add a filter form with days, only_untagged, app_name, domain, and repo inputs.
- Add a candidate list region that renders session metadata and tag controls.
- Add a suggestion panel that renders grouped repo/domain suggestions by selected tag.
- Add a YAML preview region and a download button.
- Use small fetch-based interactions rather than a heavy client-side app.
- Show optimistic success state only after the assign API confirms the write.

### Sync-client seam

- Identify where manual session tag updates can be included in sync payload updates.
- Add a small seam in services/sync_worker.py or adjacent sync code so a later change can propagate reviewed tag overrides without changing the tag review UI contract.
- Keep the first implementation safe even if cross-device propagation is deferred.

### Validation checklist before coding complete

- Tag Review link appears only in standalone and sync-client modes.
- Untagged candidates load correctly from local activity_sessions.
- Assigning a tag updates dashboard-visible data immediately.
- Suggestions are generated only from reviewed actions.
- Existing keywords from config/my-tags.yaml remain visible, but new keyword suggestions are not generated.
- YAML preview is deterministic and case-insensitive dedupe works.
- YAML download returns valid YAML without mutating config/my-tags.yaml.
- Sync-server mode returns a clear unsupported response for page and API routes.

## Recommended First Slice

Start with a low-risk first slice:

- Add Tag Review page in standalone and sync-client.
- Support manual tagging and suggestions from domain/repo only.
- Provide YAML preview and download.
- Defer direct YAML file mutation until after user validates quality.

This delivers immediate value while minimizing accidental rule pollution.
