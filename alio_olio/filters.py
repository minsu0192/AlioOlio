from __future__ import annotations

from collections import defaultdict

from .domain import FilterRule, OFFICIAL_FIELD_MAP, Posting


def matches(posting: Posting, rules: list[FilterRule]) -> bool:
    active = [rule for rule in rules if rule.enabled]
    grouped: dict[str, list[FilterRule]] = defaultdict(list)
    includes: list[str] = []
    excludes: list[str] = []
    for rule in active:
        if rule.rule == "include":
            includes.append((rule.value or rule.name).casefold())
        elif rule.rule == "exclude":
            excludes.append((rule.value or rule.name).casefold())
        elif rule.category in OFFICIAL_FIELD_MAP:
            grouped[rule.category].append(rule)

    for category, category_rules in grouped.items():
        field = getattr(posting, OFFICIAL_FIELD_MAP[category])
        values = [field] if isinstance(field, str) else field
        normalized = {str(item).casefold() for item in values}
        if not any((rule.value or rule.name).casefold() in normalized for rule in category_rules):
            return False

    haystack = posting.searchable_text()
    if includes and not any(word in haystack for word in includes):
        return False
    if any(word in haystack for word in excludes):
        return False
    return True
