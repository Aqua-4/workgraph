from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

TagRuleMap = dict[str, dict[str, list[str]]]

_BROWSER_APP_NAMES = {
    "arc",
    "brave",
    "brave browser",
    "brave-browser",
    "chrome",
    "firefox",
    "google chrome",
    "microsoft edge",
    "edge",
    "opera",
    "safari",
    "vivaldi",
}

_GENERIC_BROWSER_DOMAINS = {
    "newtab",
    "new-tab",
    "new-tab-page",
    "newtab-page",
}

_DEFAULT_BROWSER_CONTEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("youtube music", "YouTube Music"),
    ("diffchecker", "Diffchecker"),
    ("compare text and find differences", "Diffchecker"),
    ("new tab", "New Tab"),
    ("workgraph", "Workgraph"),
    ("google chat", "Google Chat"),
    ("google meet", "Google Meet"),
    ("meet", "Google Meet"),
    ("straive.com mail", "Work Gmail"),
    ("inbox", "Work Gmail"),
    ("chatgpt", "ChatGPT"),
    ("gemini notebook", "Gemini Notebook"),
    ("darwinbox", "Darwinbox"),
    ("horizon", "Horizon"),
    ("linkedin", "LinkedIn"),
)


@lru_cache(maxsize=1)
def _runtime_browser_context_patterns() -> tuple[tuple[str, str], ...]:
    return load_browser_context_patterns()


def load_browser_context_patterns(
    config_path: Path | str | None = None,
) -> tuple[tuple[str, str], ...]:
    if config_path is None:
        config_dir = Path(__file__).parent.parent / "config"
        config_files = [config_dir / "tags.yaml", config_dir / "my-tags.yaml"]
    else:
        resolved_path = Path(config_path)
        if resolved_path.is_file():
            config_files = [resolved_path]
        else:
            config_files = [resolved_path / "tags.yaml", resolved_path / "my-tags.yaml"]

    merged_patterns: dict[str, str] = {}
    for file_path in config_files:
        if not file_path.exists():
            continue

        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue

        raw_tag_review = data.get("tag_review")
        if not isinstance(raw_tag_review, Mapping):
            continue

        raw_patterns = raw_tag_review.get("browser_context_patterns")
        if not isinstance(raw_patterns, Iterable) or isinstance(raw_patterns, str):
            continue

        for item in raw_patterns:
            if not isinstance(item, Mapping):
                continue
            raw_pattern = _as_optional_string(item.get("pattern"))
            label = _as_optional_string(item.get("label"))
            if not raw_pattern or not label:
                continue
            normalized_pattern = _normalize_match_text(raw_pattern)
            if not normalized_pattern:
                continue
            merged_patterns[normalized_pattern] = label

    if not merged_patterns:
        return _DEFAULT_BROWSER_CONTEXT_PATTERNS

    return tuple((pattern, label) for pattern, label in merged_patterns.items())


def normalize_repo_candidate(git_repo: str | None) -> str | None:
    if not git_repo:
        return None

    text = git_repo.strip().rstrip("/\\")
    if not text:
        return None

    normalized_path = text.replace("\\", "/")
    if normalized_path.endswith("/.git"):
        normalized_path = normalized_path[: -len("/.git")]

    repo_name = normalized_path.split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    repo_name = repo_name.strip()
    return repo_name or None


def normalize_domain_candidate(browser_domain: str | None) -> str | None:
    if not browser_domain:
        return None

    text = browser_domain.strip().lower()
    if not text:
        return None

    if "://" in text:
        parsed = urlparse(text)
        text = parsed.netloc or parsed.path

    text = text.split("/")[0].split(":")[0].strip(".")
    if text.startswith("www."):
        text = text[4:]

    return text or None


def derive_browser_context(
    window_title: str | None,
    app_name: str | None,
    process_name: str | None = None,
    patterns: Iterable[tuple[str, str]] | None = None,
) -> str | None:
    if not is_browser_app(app_name, process_name):
        return None

    normalized_title = _normalize_browser_title(window_title)
    if not normalized_title:
        return None

    candidate_patterns = list(patterns or _runtime_browser_context_patterns())
    candidate_patterns = _sort_browser_context_patterns(candidate_patterns)

    for pattern, label in candidate_patterns:
        if _browser_pattern_matches_title(pattern, normalized_title):
            return label

    return None


def resolve_tag_review_group(session: Mapping[str, object]) -> dict[str, str] | None:
    repo_value = normalize_repo_candidate(_as_optional_string(session.get("git_repo")))
    if repo_value:
        return {"group_type": "repo", "group_value": repo_value}

    domain_value = normalize_domain_candidate(
        _as_optional_string(session.get("browser_domain"))
    )
    if domain_value and _is_meaningful_browser_domain(domain_value):
        return {"group_type": "domain", "group_value": domain_value}

    browser_context = derive_browser_context(
        _as_optional_string(session.get("window_title")),
        _as_optional_string(session.get("app_name")),
        _as_optional_string(session.get("process_name")),
    )
    if browser_context:
        return {"group_type": "browser_context", "group_value": browser_context}

    app_name = _as_optional_string(session.get("app_name"))
    if app_name:
        return {"group_type": "app", "group_value": app_name}

    return None


def resolve_browser_tag_review_group(
    session: Mapping[str, object],
) -> dict[str, str] | None:
    app_name = _as_optional_string(session.get("app_name"))
    process_name = _as_optional_string(session.get("process_name"))
    if not is_browser_app(app_name, process_name):
        return None

    domain_value = normalize_domain_candidate(
        _as_optional_string(session.get("browser_domain"))
    )
    if domain_value and _is_meaningful_browser_domain(domain_value):
        return {"group_type": "domain", "group_value": domain_value}

    browser_context = derive_browser_context(
        _as_optional_string(session.get("window_title")),
        app_name,
        process_name,
    )
    if browser_context:
        return {"group_type": "browser_context", "group_value": browser_context}

    title_bucket = derive_browser_title_bucket(
        _as_optional_string(session.get("window_title")),
        app_name,
        process_name,
    )
    if title_bucket:
        return {"group_type": "title_bucket", "group_value": title_bucket}

    if app_name:
        return {"group_type": "app", "group_value": app_name}
    return None


def derive_browser_title_bucket(
    window_title: str | None,
    app_name: str | None,
    process_name: str | None = None,
) -> str | None:
    if not is_browser_app(app_name, process_name):
        return None

    normalized_title = _normalize_browser_title(window_title)
    if not normalized_title:
        return None

    normalized_title = re.sub(r"\s*\([0-9]+\)\s*", " ", normalized_title)
    normalized_title = " ".join(normalized_title.split())
    if not normalized_title:
        return None

    max_len = 96
    if len(normalized_title) <= max_len:
        return normalized_title
    return normalized_title[:max_len].rstrip()


def build_tag_review_groups(
    sessions: Iterable[Mapping[str, object]],
) -> dict[tuple[str, str], list[Mapping[str, object]]]:
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for session in sessions:
        group = resolve_tag_review_group(session)
        if group is None:
            continue
        grouped[(group["group_type"], group["group_value"])].append(session)
    return dict(grouped)


def is_browser_app(app_name: str | None, process_name: str | None = None) -> bool:
    candidates = [app_name, process_name]
    for candidate in candidates:
        if not candidate:
            continue
        normalized = candidate.strip().casefold().replace(".exe", "")
        if normalized in _BROWSER_APP_NAMES:
            return True
    return False


def load_custom_tag_rules(config_path: Path | str | None = None) -> TagRuleMap:
    resolved_path = (
        Path(config_path)
        if config_path
        else Path(__file__).parent.parent / "config" / "my-tags.yaml"
    )
    if not resolved_path.exists():
        return {}

    try:
        data = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

    raw_tags = data.get("tags", {})
    if not isinstance(raw_tags, dict):
        return {}

    normalized: TagRuleMap = {}
    for tag_name, rule in raw_tags.items():
        if not isinstance(rule, Mapping):
            continue
        normalized[str(tag_name)] = {
            "repos": _normalize_string_list(rule.get("repos")),
            "domains": _normalize_string_list(rule.get("domains")),
            "keywords": _normalize_string_list(rule.get("keywords")),
            "intent": _normalize_string_list(rule.get("intent")),
        }
    return normalized


def build_tag_review_suggestions(
    review_actions: Iterable[Mapping[str, object]],
    existing_rules: Mapping[str, Mapping[str, list[str]]],
    *,
    min_domain_hits: int = 2,
    min_repo_hits: int = 2,
) -> dict[str, dict[str, object]]:
    grouped_domains: dict[str, Counter[str]] = defaultdict(Counter)
    grouped_repos: dict[str, Counter[str]] = defaultdict(Counter)
    grouped_keywords: dict[str, Counter[str]] = defaultdict(Counter)
    reviewed_counts: Counter[str] = Counter()

    for action in review_actions:
        selected_tag = str(action.get("selected_tag") or "").strip()
        if not selected_tag:
            continue

        reviewed_counts[selected_tag] += 1

        domain = normalize_domain_candidate(
            _as_optional_string(action.get("browser_domain"))
        )
        if domain:
            grouped_domains[selected_tag][domain] += 1

        repo = normalize_repo_candidate(_as_optional_string(action.get("git_repo")))
        if repo:
            grouped_repos[selected_tag][repo] += 1

        source_signal = str(action.get("source_signal") or "").strip().casefold()
        app_name = _as_optional_string(action.get("app_name"))
        if source_signal == "app" and app_name:
            grouped_keywords[selected_tag][app_name] += 1

        if source_signal in {"browser_context", "title_bucket"}:
            browser_keyword = derive_browser_context(
                _as_optional_string(action.get("window_title")),
                app_name,
                _as_optional_string(action.get("process_name")),
            )
            if browser_keyword:
                grouped_keywords[selected_tag][browser_keyword] += 1

    conflicts = find_rule_conflicts(
        existing_rules,
        {
            tag_name: {
                "domains": list(domain_counts.keys()),
                "repos": list(repo_counts.keys()),
            }
            for tag_name, domain_counts in grouped_domains.items()
            for repo_counts in [grouped_repos.get(tag_name, Counter())]
        },
    )

    suggestions: dict[str, dict[str, object]] = {}
    tag_names = set(reviewed_counts.keys()) | set(existing_rules.keys())
    for tag_name in sorted(tag_names):
        domain_items = [
            _build_suggestion_item(
                token,
                count,
                conflicts.get("domains", {}).get(token.casefold()),
            )
            for token, count in grouped_domains.get(tag_name, Counter()).items()
            if count >= min_domain_hits
        ]
        repo_items = [
            _build_suggestion_item(
                token,
                count,
                conflicts.get("repos", {}).get(token.casefold()),
            )
            for token, count in grouped_repos.get(tag_name, Counter()).items()
            if count >= min_repo_hits
        ]

        suggestions[tag_name] = {
            "tag_name": tag_name,
            "domains": sorted(
                domain_items, key=lambda item: (-item["sample_count"], item["value"])
            ),
            "repos": sorted(
                repo_items, key=lambda item: (-item["sample_count"], item["value"])
            ),
            "keywords": _merge_unique(
                existing_rules.get(tag_name, {}).get("keywords", []),
                list(grouped_keywords.get(tag_name, Counter()).keys()),
            ),
            "total_reviewed": reviewed_counts.get(tag_name, 0),
        }

    return suggestions


def find_rule_conflicts(
    existing_rules: Mapping[str, Mapping[str, list[str]]],
    candidate_rules: Mapping[str, Mapping[str, list[str]]],
) -> dict[str, dict[str, str]]:
    existing_lookup: dict[str, dict[str, str]] = {"domains": {}, "repos": {}}

    for tag_name, rule in existing_rules.items():
        for domain in rule.get("domains", []):
            existing_lookup["domains"].setdefault(domain.casefold(), str(tag_name))
        for repo in rule.get("repos", []):
            existing_lookup["repos"].setdefault(repo.casefold(), str(tag_name))

    conflicts: dict[str, dict[str, str]] = {"domains": {}, "repos": {}}
    for tag_name, rule in candidate_rules.items():
        for domain in rule.get("domains", []):
            owner = existing_lookup["domains"].get(domain.casefold())
            if owner and owner != tag_name:
                conflicts["domains"][domain.casefold()] = owner
        for repo in rule.get("repos", []):
            owner = existing_lookup["repos"].get(repo.casefold())
            if owner and owner != tag_name:
                conflicts["repos"][repo.casefold()] = owner

    return conflicts


def build_yaml_preview(
    existing_rules: Mapping[str, Mapping[str, list[str]]],
    selected_suggestions: Mapping[str, Mapping[str, object]],
) -> tuple[str, list[dict[str, str]]]:
    merged_rules: TagRuleMap = deepcopy(_coerce_rule_map(existing_rules))
    warnings: list[dict[str, str]] = []
    conflicts = find_rule_conflicts(
        existing_rules, _candidate_values_only(selected_suggestions)
    )

    for token_type, owners in conflicts.items():
        for token_key, owner in owners.items():
            warnings.append(
                {
                    "type": "conflict",
                    "field": token_type,
                    "value": token_key,
                    "owner": owner,
                }
            )

    for tag_name, suggestion in selected_suggestions.items():
        target_rule = merged_rules.setdefault(
            str(tag_name), {"repos": [], "domains": [], "keywords": [], "intent": []}
        )
        target_rule["repos"] = _merge_unique(
            target_rule.get("repos", []),
            _extract_values(suggestion.get("repos", [])),
        )
        target_rule["domains"] = _merge_unique(
            target_rule.get("domains", []),
            _extract_values(suggestion.get("domains", [])),
        )
        target_rule["keywords"] = _merge_unique(
            target_rule.get("keywords", []),
            _normalize_string_list(suggestion.get("keywords")),
        )
        existing_intent_values = target_rule.get("intent", [])
        if (
            existing_intent_values
            or isinstance(suggestion.get("intent"), Iterable)
            and not isinstance(suggestion.get("intent"), str)
        ):
            target_rule["intent"] = _merge_unique(
                existing_intent_values,
                _normalize_string_list(suggestion.get("intent")),
            )

    preview_text = yaml.safe_dump(
        {"tags": merged_rules},
        sort_keys=False,
        allow_unicode=False,
    )
    return preview_text, warnings


def _build_suggestion_item(
    value: str, sample_count: int, conflict_owner: str | None
) -> dict[str, object]:
    item: dict[str, object] = {
        "value": value,
        "sample_count": sample_count,
    }
    if conflict_owner:
        item["conflict_with"] = conflict_owner
    return item


def _extract_values(items: object) -> list[str]:
    values: list[str] = []
    if not isinstance(items, Iterable) or isinstance(items, str):
        return values

    for item in items:
        if isinstance(item, Mapping):
            value = _as_optional_string(item.get("value"))
        else:
            value = _as_optional_string(item)
        if value:
            values.append(value)
    return values


def _normalize_string_list(values: object) -> list[str]:
    if not isinstance(values, Iterable) or isinstance(values, str):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _as_optional_string(value)
        if not item:
            continue
        folded = item.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(item)
    return normalized


def _merge_unique(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in list(existing) + list(incoming):
        item = _as_optional_string(value)
        if not item:
            continue
        folded = item.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        merged.append(item)
    return sorted(merged, key=str.casefold)


def _coerce_rule_map(
    existing_rules: Mapping[str, Mapping[str, list[str]]],
) -> TagRuleMap:
    normalized: TagRuleMap = {}
    for tag_name, rule in existing_rules.items():
        normalized[str(tag_name)] = {
            "repos": _normalize_string_list(rule.get("repos", [])),
            "domains": _normalize_string_list(rule.get("domains", [])),
            "keywords": _normalize_string_list(rule.get("keywords", [])),
            "intent": _normalize_string_list(rule.get("intent", [])),
        }
    return normalized


def _candidate_values_only(
    selected_suggestions: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, list[str]]]:
    candidate_rules: dict[str, dict[str, list[str]]] = {}
    for tag_name, suggestion in selected_suggestions.items():
        candidate_rules[str(tag_name)] = {
            "repos": _extract_values(suggestion.get("repos", [])),
            "domains": _extract_values(suggestion.get("domains", [])),
        }
    return candidate_rules


def _as_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_browser_title(value: str | None) -> str:
    return _normalize_match_text(value)


def _normalize_match_text(value: str | None) -> str:
    if not value:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    text = re.sub(r"[|–—]+", " - ", text)
    text = re.sub(
        r"\s+-\s+(Brave|Google Chrome|Microsoft Edge|Mozilla Firefox|Firefox|Chrome)$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(Brave|Google Chrome|Microsoft Edge|Mozilla Firefox|Firefox|Chrome)\s+-\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return " ".join(text.split())


def _sort_browser_context_patterns(
    patterns: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    ranked = []
    for index, (pattern, label) in enumerate(patterns):
        normalized_pattern = _normalize_match_text(pattern)
        if normalized_pattern:
            ranked.append((index, len(normalized_pattern), pattern, label))
    ranked.sort(key=lambda item: (item[1], item[0]), reverse=True)
    return [(pattern, label) for _, _, pattern, label in ranked]


def _browser_pattern_matches_title(pattern: str, normalized_title: str) -> bool:
    normalized_pattern = _normalize_match_text(pattern)
    if not normalized_pattern:
        return False

    for token in normalized_pattern.split("|"):
        token = _normalize_match_text(token)
        if token and token in normalized_title:
            return True
    return False


def _is_meaningful_browser_domain(value: str) -> bool:
    candidate = value.casefold()
    if candidate in _GENERIC_BROWSER_DOMAINS:
        return False
    if candidate.startswith("chrome:") or candidate.startswith("about:"):
        return False
    return True
