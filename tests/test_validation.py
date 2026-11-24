from datetime import date

from doctordoc.extract.validation import (
    date_order,
    in_range,
    matches,
    required,
    resolve,
    row_product,
    rule,
    run_rules,
    sum_equals,
    sum_of_fields,
)

DATA = {
    "invoice_date": date(2024, 3, 15),
    "due_date": date(2024, 3, 1),
    "subtotal": 4200.0,
    "tax": 420.0,
    "total": 4620.0,
    "number": "INV-1",
    "items": [
        {"qty": 2, "price": 1500.0, "amount": 3000.0},
        {"qty": 10, "price": 120.0, "amount": 1300.0},
    ],
}


def test_resolve_paths_with_wildcards():
    assert resolve(DATA, "items[*].amount") == [
        ("items[0].amount", 3000.0),
        ("items[1].amount", 1300.0),
    ]
    assert resolve(DATA, "items[1].qty") == [("items[1].qty", 10)]
    assert resolve(DATA, "nope.deeper") == []


def test_required():
    issues = run_rules([required("number", "missing", "empty")], {"number": "x", "empty": ""})
    assert sorted(i.path for i in issues) == ["empty", "missing"]


def test_in_range_checks_every_item():
    issues = run_rules([in_range("items[*].price", maximum=1000)], DATA)
    assert [i.path for i in issues] == ["items[0].price"]


def test_date_order():
    [issue] = run_rules([date_order("invoice_date", "due_date")], DATA)
    assert issue.path == "due_date" and issue.related == ["invoice_date"]


def test_sum_equals_flags_total_and_items():
    [issue] = run_rules([sum_equals("items[*].amount", "subtotal")], DATA)
    assert issue.path == "subtotal" and "4300" in issue.message
    assert issue.related == ["items[0].amount", "items[1].amount"]


def test_sum_of_fields_ok_and_row_product_mismatch():
    assert run_rules([sum_of_fields(["subtotal", "tax"], "total")], DATA) == []
    [issue] = run_rules([row_product("items", "qty", "price", "amount")], DATA)
    assert issue.path == "items[1].amount"


def test_rules_stay_quiet_when_data_is_missing():
    assert run_rules([sum_equals("items[*].amount", "subtotal"), date_order("a", "b")], {}) == []


def test_matches_is_a_warning_by_default():
    [issue] = run_rules([matches("number", r"\d+")], DATA)
    assert issue.severity == "warning"


def test_custom_rule_with_decorator():
    @rule("weight_is_positive")
    def positive(data):
        if data["weight"] <= 0:
            return ("weight", "weight must be positive")

    [issue] = run_rules([positive], {"weight": -1})
    assert issue.rule == "weight_is_positive" and issue.path == "weight"


def test_custom_rule_can_return_several_problems_or_issue_objects():
    @rule("many")
    def many(data):
        return [("a", "bad a"), ("b", "bad b")]

    assert [i.path for i in run_rules([many], {})] == ["a", "b"]


def test_a_crashing_rule_becomes_a_warning_and_does_not_stop_others():
    @rule("crashes")
    def crashes(data):
        return data["missing_key"]

    issues = run_rules([crashes, required("x")], {})
    assert [i.rule for i in issues] == ["crashes", "required"]
    assert issues[0].severity == "warning"
