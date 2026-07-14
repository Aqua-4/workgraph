from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
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

_BROWSER_CONTEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("youtube music", "YouTube Music"),
    ("diffchecker", "Diffchecker"),
    ("compare text and find differences", "Diffchecker"),
    ("new tab", "New Tab"),
)


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
) -> str | None:
    if not is_browser_app(app_name, process_name):
        return None

    normalized_title = _normalize_browser_title(window_title)
    if not normalized_title:
        return None

    for pattern, label in _BROWSER_CONTEXT_PATTERNS:
        if pattern in normalized_title:
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
            "keywords": list(existing_rules.get(tag_name, {}).get("keywords", [])),
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
            str(tag_name), {"repos": [], "domains": [], "keywords": []}
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
    if not value:
        return ""

    normalized = re.sub(
        r"\s+-\s+(Brave|Google Chrome|Microsoft Edge|Mozilla Firefox|Firefox|Chrome)$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"^(Brave|Google Chrome|Microsoft Edge|Mozilla Firefox|Firefox|Chrome)\s+-\s+",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    return " ".join(normalized.casefold().split())


def _is_meaningful_browser_domain(value: str) -> bool:
    candidate = value.casefold()
    if candidate in _GENERIC_BROWSER_DOMAINS:
        return False
    if candidate.startswith("chrome:") or candidate.startswith("about:"):
        return False
    return True
