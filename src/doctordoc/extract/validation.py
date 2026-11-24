"""Validation rules for extracted data.

Types and ranges that fit in the Pydantic schema (ge=, le=, Literal ...) are checked by
Pydantic itself. This module adds the rest: required fields, ranges, cross-field checks
(line items add up to the total, dates in order) and your own rules.

Adding your own rule is one decorated function:

    @rule("weight_is_positive")
    def positive(data):
        if data["weight"] is not None and data["weight"] <= 0:
            return ("weight", "weight must be positive")
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from doctordoc.extract.models import Issue

Severity = Literal["error", "warning"]
# A rule function gets the extracted data (a dict) and returns its problems as
# (path, message) pairs, or Issue objects, or a list of them, or nothing.
RuleResult = Any


@dataclass
class Rule:
    name: str
    check: Callable[[dict[str, Any]], RuleResult]
    severity: Severity = "error"

    def run(self, data: dict[str, Any]) -> list[Issue]:
        result = self.check(data)
        if result is None:
            return []
        items = [result] if _is_single(result) else result
        issues: list[Issue] = []
        for item in items:
            if isinstance(item, Issue):
                issues.append(item)
            else:
                path, message = item
                issues.append(
                    Issue(path=path, rule=self.name, message=message, severity=self.severity)
                )
        return issues


def _is_single(result: Any) -> bool:
    """A lone (path, message) tuple of two strings, as opposed to a list of them."""
    return isinstance(result, Issue) or (
        isinstance(result, tuple) and len(result) == 2 and all(isinstance(x, str) for x in result)
    )


def rule(name: str, severity: Severity = "error") -> Callable[[Callable[..., RuleResult]], Rule]:
    """Decorator that turns a function into a Rule."""

    def wrap(function: Callable[..., RuleResult]) -> Rule:
        return Rule(name=name, check=function, severity=severity)

    return wrap


def run_rules(rules: list[Rule], data: dict[str, Any]) -> list[Issue]:
    """Run every rule. A rule that crashes becomes a warning, it never stops the others."""
    issues: list[Issue] = []
    for item in rules:
        try:
            issues.extend(item.run(data))
        except Exception as err:  # a buggy custom rule must not break extraction
            issues.append(
                Issue(
                    path="",
                    rule=item.name,
                    severity="warning",
                    message=f"rule crashed: {type(err).__name__}: {err}",
                )
            )
    return issues


# ---------------------------------------------------------------------------- paths

_STEP = re.compile(r"([A-Za-z_]\w*)|\[(\*|\d+)\]")


def resolve(data: Any, path: str) -> list[tuple[str, Any]]:
    """Look up a path like 'line_items[*].amount'. Returns (concrete_path, value) pairs.

    '[*]' means every item of a list, so one path can give many values.
    """
    results: list[tuple[str, Any]] = [("", data)]
    for name, index in _STEP.findall(path):
        step: list[tuple[str, Any]] = []
        for where, value in results:
            if name:
                if isinstance(value, dict):
                    step.append((f"{where}.{name}" if where else name, value.get(name)))
            elif isinstance(value, list):
                picks = range(len(value)) if index == "*" else [int(index)]
                step.extend((f"{where}[{i}]", value[i]) for i in picks if i < len(value))
        results = step
    return results


def _numbers(pairs: list[tuple[str, Any]]) -> list[tuple[str, float]]:
    return [
        (p, float(v)) for p, v in pairs if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


# ---------------------------------------------------------------------------- ready-made rules


def required(*paths: str, severity: Severity = "error") -> Rule:
    """The fields must be present (not null, not empty)."""

    def check(data: dict[str, Any]) -> list[tuple[str, str]]:
        problems = []
        for path in paths:
            found = resolve(data, path)
            if not found:
                problems.append((path, f"'{path}' is required but missing"))
            problems += [
                (p, f"'{p}' is required but empty") for p, v in found if v in (None, "", [])
            ]
        return problems

    return Rule("required", check, severity)


def in_range(
    path: str,
    minimum: float | None = None,
    maximum: float | None = None,
    severity: Severity = "error",
) -> Rule:
    """Numbers at `path` must be between minimum and maximum."""

    def check(data: dict[str, Any]) -> list[tuple[str, str]]:
        problems = []
        for where, number in _numbers(resolve(data, path)):
            if minimum is not None and number < minimum:
                problems.append((where, f"{number:g} is below the minimum {minimum:g}"))
            if maximum is not None and number > maximum:
                problems.append((where, f"{number:g} is above the maximum {maximum:g}"))
        return problems

    return Rule("in_range", check, severity)


def date_order(earlier: str, later: str, severity: Severity = "error") -> Rule:
    """The date at `earlier` must not be after the date at `later`."""

    def check(data: dict[str, Any]) -> list[Issue]:
        first, second = resolve(data, earlier), resolve(data, later)
        if not first or not second:
            return []
        a, b = _as_date(first[0][1]), _as_date(second[0][1])
        if a and b and a > b:
            message = f"{earlier} ({a}) is after {later} ({b})"
            return [
                Issue(
                    path=later,
                    rule="date_order",
                    message=message,
                    severity=severity,
                    related=[earlier],
                )
            ]
        return []

    return Rule("date_order", check, severity)


def sum_equals(
    items: str, total: str, tolerance: float = 0.02, severity: Severity = "error"
) -> Rule:
    """The numbers at `items` (e.g. 'line_items[*].amount') must add up to `total`."""

    def check(data: dict[str, Any]) -> list[Issue]:
        values = _numbers(resolve(data, items))
        totals = _numbers(resolve(data, total))
        if not values or not totals:
            return []
        expected = sum(v for _, v in values)
        where, stated = totals[0]
        if abs(expected - stated) > tolerance:
            message = f"{items} adds up to {expected:g}, but {total} says {stated:g}"
            return [
                Issue(
                    path=where,
                    rule="sum_equals",
                    message=message,
                    severity=severity,
                    related=[p for p, _ in values],
                )
            ]
        return []

    return Rule("sum_equals", check, severity)


def sum_of_fields(
    parts: list[str], total: str, tolerance: float = 0.02, severity: Severity = "error"
) -> Rule:
    """Several single fields (e.g. subtotal + tax) must add up to `total`."""

    def check(data: dict[str, Any]) -> list[Issue]:
        found = [_numbers(resolve(data, p)) for p in parts]
        totals = _numbers(resolve(data, total))
        if not totals or any(not f for f in found):
            return []  # a missing part is the job of required(), not of this rule
        expected = sum(f[0][1] for f in found)
        where, stated = totals[0]
        if abs(expected - stated) > tolerance:
            message = f"{' + '.join(parts)} = {expected:g}, but {total} says {stated:g}"
            return [
                Issue(
                    path=where,
                    rule="sum_of_fields",
                    message=message,
                    severity=severity,
                    related=parts,
                )
            ]
        return []

    return Rule("sum_of_fields", check, severity)


def row_product(
    list_path: str,
    a: str,
    b: str,
    result: str,
    tolerance: float = 0.02,
    severity: Severity = "error",
) -> Rule:
    """In every row of a list: a * b must equal result (e.g. quantity * unit_price = amount)."""

    def check(data: dict[str, Any]) -> list[Issue]:
        issues = []
        for where, row in resolve(data, f"{list_path}[*]"):
            if not isinstance(row, dict):
                continue
            x, y, z = row.get(a), row.get(b), row.get(result)
            numbers = (int, float)
            if not (isinstance(x, numbers) and isinstance(y, numbers) and isinstance(z, numbers)):
                continue
            if abs(x * y - z) > tolerance:
                message = f"{a} {x:g} x {b} {y:g} = {x * y:g}, but {result} says {z:g}"
                issues.append(
                    Issue(
                        path=f"{where}.{result}",
                        rule="row_product",
                        message=message,
                        severity=severity,
                        related=[f"{where}.{a}", f"{where}.{b}"],
                    )
                )
        return issues

    return Rule("row_product", check, severity)


def matches(path: str, pattern: str, severity: Severity = "warning") -> Rule:
    """Text at `path` must match the regular expression."""
    compiled = re.compile(pattern)

    def check(data: dict[str, Any]) -> list[tuple[str, str]]:
        return [
            (where, f"'{value}' does not match {pattern}")
            for where, value in resolve(data, path)
            if isinstance(value, str) and not compiled.fullmatch(value)
        ]

    return Rule("matches", check, severity)
