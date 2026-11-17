"""Independent mathematical and privacy acceptance for algebra Homework Help."""
from copy import deepcopy
from fractions import Fraction
from math import isqrt, sqrt
import json
import socket

import pytest

from academy.homework_algebra import algebra_guidance, validate_algebra_task
from academy import provider
from academy.homework_deidentification import (
    build_public_homework_content,
    build_public_homework_prompt,
    compile_homework_task,
    validate_public_homework_task,
)
from algebra_uat_cases import (
    EQUATION_CASES, EXPRESSION_CASES, INEQUALITY_CASES,
    MALFORMED_OR_AMBIGUOUS, SYSTEM_CASES,
)
from test_homework_deidentified_egress import wire


def _value(node, point):
    """Small independent interpreter for the public AST, never source text."""
    kind = node["type"]
    if kind == "number":
        return Fraction(*node["value"])
    if kind == "variable":
        return Fraction(point[node["name"]])
    if kind == "neg":
        return -_value(node["value"], point)
    if kind == "abs":
        return abs(_value(node["value"], point))
    if kind == "sqrt":
        value = _value(node["value"], point)
        if value < 0:
            raise ValueError("Outside the real square-root domain")
        if isinstance(value, Fraction):
            numerator, denominator = isqrt(value.numerator), isqrt(value.denominator)
            if numerator * numerator == value.numerator and denominator * denominator == value.denominator:
                return Fraction(numerator, denominator)
        return sqrt(value)
    left, right = _value(node["left"], point), _value(node["right"], point)
    if kind == "add":
        return left + right
    if kind == "sub":
        return left - right
    if kind == "mul":
        return left * right
    if kind == "div":
        return left / right
    if kind == "pow":
        if left == 0 and right <= 0:
            raise ValueError("Zero to a nonpositive power is undefined")
        return left ** (int(right) if right.denominator == 1 else float(right))
    raise AssertionError(f"Unknown AST operation: {kind}")


def _compiled(question):
    result = compile_homework_task(question)
    assert result.status == "ready", (question, result.status, result.reason)
    assert result.task is not None and result.task["kind"] == "algebra", (question, result.task)
    assert validate_public_homework_task(result.task) == result.task
    return result.task


def _assert_value(actual, expected):
    if isinstance(actual, (int, Fraction)):
        assert actual == expected
    else:
        assert actual == pytest.approx(float(expected))


@pytest.mark.parametrize("case_id,question,points,expected", EXPRESSION_CASES, ids=[row[0] for row in EXPRESSION_CASES])
def test_expression_tree_preserves_math_at_independent_witnesses(case_id, question, points, expected):
    task = _compiled(question)
    assert task["action"] in {"factor", "expand", "simplify"}
    for point, value in zip(points, expected):
        _assert_value(_value(task["expression"], point), value)


@pytest.mark.parametrize("case_id,question,points,expected", EQUATION_CASES, ids=[row[0] for row in EQUATION_CASES])
def test_equations_preserve_both_sides_and_degenerate_cases(case_id, question, points, expected):
    task = _compiled(question)
    assert task["action"] == "solve"
    relation, = task["relations"]
    assert relation["operator"] == "eq"
    for point, residual in zip(points, expected):
        _assert_value(_value(relation["left"], point) - _value(relation["right"], point), residual)


@pytest.mark.parametrize("case_id,question,points,expected", INEQUALITY_CASES, ids=[row[0] for row in INEQUALITY_CASES])
def test_inequalities_keep_direction_and_inclusive_endpoints(case_id, question, points, expected):
    task = _compiled(question)
    relation, = task["relations"]
    compare = {
        "lt": lambda a, b: a < b, "le": lambda a, b: a <= b,
        "gt": lambda a, b: a > b, "ge": lambda a, b: a >= b,
    }[relation["operator"]]
    assert [compare(_value(relation["left"], point), _value(relation["right"], point)) for point in points] == list(expected)


def test_compound_inequality_preserves_both_sides_and_negative_division_rule():
    task = _compiled("Solve 1 < -2x <= 7")
    assert len(task["relations"]) == 2
    a, b = task["relations"]
    assert a["operator"] == "lt" and b["operator"] == "le"
    assert a["right"] == b["left"]

    def admits(x):
        point = {"x": x}
        return _value(a["left"], point) < _value(a["right"], point) and _value(b["left"], point) <= _value(b["right"], point)

    assert admits(Fraction(-7, 2))
    assert admits(-1)
    assert not admits(Fraction(-1, 2))
    assert not admits(-4)
    assert algebra_guidance(task)["reverse_inequality_when_dividing_by_negative"] is True


@pytest.mark.parametrize("case_id,question,point,expected", SYSTEM_CASES, ids=[row[0] for row in SYSTEM_CASES])
def test_systems_keep_both_equations_including_inconsistent_and_dependent_cases(case_id, question, point, expected):
    task = _compiled(question)
    assert task["action"] == "solve_system" and len(task["relations"]) == 2
    assert all(relation["operator"] == "eq" for relation in task["relations"])
    residuals = [_value(r["left"], point) - _value(r["right"], point) for r in task["relations"]]
    assert sorted(residuals) == sorted(expected)


@pytest.mark.parametrize("question,argument,expected", [
    ("Given f(x) = 2x^2 - 3x + 1, help me find f(-2)", -2, 15),
    ("Evaluate f(3) for f(x) = (x - 1)/(x + 1)", 3, Fraction(1, 2)),
    ("Evaluate f(-2) where f(x) = sqrt(x + 6)", -2, 2),
    ("Evaluate g(2) for g(x) = x^2 + 1", 2, 5),
    ("Evaluate x^2 - 3x + 1 at x = -2", -2, 11),
])
def test_function_evaluation_keeps_the_definition_and_exact_input(question, argument, expected):
    task = _compiled(question)
    assert task["action"] == "evaluate_function"
    assert Fraction(*task["input"]) == argument
    _assert_value(_value(task["expression"], {"x": argument}), expected)


@pytest.mark.parametrize("question,feature,points,expected", [
    ("Graph y = -(x - 2)^2 + 3", "graph", ({"x": 2}, {"x": 0}), (3, -1)),
    ("Find the zeros of f(x) = x^2 - 9", "zeros", ({"x": 3}, {"x": 0}), (0, -9)),
    ("Find the vertex of y = x^2 - 4x + 3", "vertex", ({"x": 2}, {"x": 0}), (-1, 3)),
    ("Find the slope of y = 3x + 2", "slope", ({"x": 0}, {"x": 1}), (2, 5)),
    ("Find the domain of f(x) = 1/(x - 2)", "domain", ({"x": 1}, {"x": 3}), (-1, 1)),
    ("Find the range of y = x^2 + 1", "range", ({"x": 0}, {"x": 2}), (1, 5)),
    ("Graph y = 2^(x + 1)", "graph", ({"x": 0}, {"x": 2}), (2, 8)),
])
def test_function_features_retain_the_complete_expression(question, feature, points, expected):
    task = _compiled(question)
    assert task["action"] == "study_function" and task["feature"] == feature
    for point, value in zip(points, expected):
        _assert_value(_value(task["expression"], point), value)


@pytest.mark.parametrize("question,feature,points,expected", [
    ("Graph 2x + 3y = 6", "graph", ({"x": 0, "y": 2}, {"x": 3, "y": 0}, {"x": 0, "y": 0}), (0, 0, -6)),
    ("Find the slope of 2x + 3y = 6", "slope", ({"x": 0, "y": 2}, {"x": 3, "y": 0}), (0, 0)),
    ("Graph x = 4", "graph", ({"x": 4, "y": -2}, {"x": 0, "y": 8}), (0, -4)),
    ("Find the slope of x = 4", "slope", ({"x": 4, "y": 0}, {"x": 0, "y": 0}), (0, -4)),
])
def test_standard_form_and_vertical_line_tasks_keep_the_relation(question, feature, points, expected):
    task = _compiled(question)
    assert task["action"] == "study_relation" and task["feature"] == feature
    relation, = task["relations"]
    assert relation["operator"] == "eq"
    for point, residual in zip(points, expected):
        _assert_value(_value(relation["left"], point) - _value(relation["right"], point), residual)


def test_rational_and_radical_domains_are_not_erased_by_simplification():
    task = _compiled("Simplify (x - 1)/(x - 1)")
    quotient = task["expression"]
    assert _value(quotient, {"x": 2}) == 1
    with pytest.raises(ZeroDivisionError):
        _value(quotient, {"x": 1})
    assert any(condition["operator"] == "ne" and _value(condition["expression"], {"x": 1}) == 0
               for condition in algebra_guidance(task)["domain_conditions"])
    radical = _compiled("Simplify sqrt(x - 2)")["expression"]
    assert _value(radical, {"x": 6}) == 2
    with pytest.raises(ValueError):
        _value(radical, {"x": 1})


@pytest.mark.parametrize("question,excluded,allowed", [
    ("Simplify x^0", {"x": 0}, {"x": 1}),
    ("Simplify (x - 1)^0", {"x": 1}, {"x": 2}),
    ("Simplify x^(-2)", {"x": 0}, {"x": 2}),
])
def test_zero_and_negative_powers_keep_their_nonzero_base_domain(question, excluded, allowed):
    task = _compiled(question)
    with pytest.raises(ValueError):
        _value(task["expression"], excluded)
    guidance = algebra_guidance(task)
    conditions = guidance["domain_conditions"]
    assert conditions

    def admits(point):
        result = []
        for condition in conditions:
            left, right = _value(condition["expression"], point), Fraction(*condition["value"])
            result.append(left != right if condition["operator"] == "ne" else left >= right)
        return all(result)

    assert not admits(excluded)
    assert admits(allowed)


@pytest.mark.parametrize("question", MALFORMED_OR_AMBIGUOUS)
def test_incomplete_or_extra_material_is_never_truncated_or_replaced_with_old_algebra(question):
    previous = _compiled("Solve 3x - 5 = 10")
    for context in (None, previous):
        result = compile_homework_task(question, previous_task=context)
        assert result.status in {"clarify", "blocked"}, (question, result.task)
        assert result.task is None


@pytest.mark.parametrize("question", [
    "Simplify x^11", "Simplify x^(-11)", "Simplify 10001x + 2",
    "Simplify x/0", "Simplify sqrt(-1)", "Simplify (-2)^x",
    "Simplify " + "-(" * 15 + "x + 1" + ")" * 15,
    "Simplify 2^10000", "Simplify 10000^10000", "Simplify 1e999x + 2",
    "Evaluate f(10000) for f(x) = 2^(10000x + 10000)",
    "Graph x^2 + y^2 = 9",
    "Solve x = 2 using the quadratic formula",
    "Solve 3x + 1 = 7 by completing the square",
])
def test_invalid_or_out_of_bound_expression_stays_local(question):
    result = compile_homework_task(question)
    assert result.status == "clarify", (question, result.task)
    assert result.task is None


@pytest.mark.parametrize("question", [
    "Help me factor x^2 - 5x + 6", "Solve -2x + 5 <= 11",
    "Solve the system: 2x + y = 7; x - y = 2", "Graph y = 2^(x + 1)",
    "Graph 2x + 3y = 6",
])
def test_personal_headers_do_not_change_algebra_or_either_provider_phase(question):
    clean = compile_homework_task(question)
    wrapped = compile_homework_task(
        "Name: PRIVATE_ALGEBRA_STUDENT\nUsername: PRIVATE_LOGIN\nSchool: PRIVATE_SCHOOL\n"
        "Grade: 10\nAge: 15\nEmail: private@example.invalid\n" + question)
    assert clean.status == wrapped.status == "ready"
    assert clean.task == wrapped.task
    assert clean.action == wrapped.action and clean.stage == wrapped.stage
    candidate = {"mode": "explanation", "text": "Start by identifying the mathematical operation.", "reference_ids": []}
    for phase in ("triage", "safety"):
        kwargs = {"phase": phase, "include_image": False}
        if phase == "safety":
            kwargs["candidate"] = candidate
        a = build_public_homework_prompt(clean.task, clean.action, clean.stage, **kwargs)
        b = build_public_homework_prompt(wrapped.task, wrapped.action, wrapped.stage, **kwargs)
        assert a == b
        encoded = json.dumps(a)
        for sentinel in ("PRIVATE_ALGEBRA_STUDENT", "PRIVATE_LOGIN", "PRIVATE_SCHOOL", "private@example.invalid", question):
            assert sentinel not in encoded


@pytest.mark.parametrize("private_context", [
    "with my birthday 12/25/2011", ", and my answer is 7",
])
def test_private_context_is_rejected_or_removed_without_becoming_an_operand(private_context):
    base = _compiled("Explain x + 1 = 2")
    result = compile_homework_task("Explain x + 1 = 2 " + private_context)
    if result.status == "ready":
        assert result.task == base
        content = build_public_homework_content(result.task, result.action, result.stage)
        assert content["task"] == base
    else:
        assert result.status in {"clarify", "blocked"} and result.task is None


@pytest.mark.parametrize("question", [
    "Solve 3x - 5 = 10", "Expand (x + 2)(x - 3)", "Find the zeros of f(x) = x^2 - 9",
])
def test_canonical_algebra_contains_math_not_a_computed_answer_or_private_fields(question):
    task = _compiled(question)
    payload = build_public_homework_content(task)
    assert payload["task"] == task

    def inspect(value):
        if isinstance(value, dict):
            assert not set(value) & {"answer", "solution", "solutions", "roots", "result", "student_id", "course_id", "history", "message"}
            if "name" in value:
                assert value == {"type": "variable", "name": value["name"]} and value["name"] in {"x", "y"}
            for item in value.values():
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    inspect(task)


@pytest.mark.parametrize("mutate", [
    lambda task: task.update(student_id="PRIVATE_STUDENT"),
    lambda task: task.update(action="execute_private_code"),
    lambda task: task.update(method="PRIVATE_TEACHER_INSTRUCTIONS"),
    lambda task: task["expression"].update(private_history="PRIVATE_HISTORY"),
    lambda task: task.update(expression={"type": "variable", "name": "PRIVATE_NAME"}),
    lambda task: task.update(expression={"type": "number", "value": [True, 1]}),
    lambda task: task.update(expression={"type": "number", "value": [0.5, 1]}),
    lambda task: task.update(expression={"type": "number", "value": [float("inf"), 1]}),
    lambda task: task.update(expression={"type": "number", "value": [float("nan"), 1]}),
    lambda task: task.update(expression={"type": "number", "value": [1, 0]}),
    lambda task: task.update(expression={"type": "number", "value": [10001, 1]}),
    lambda task: task.update(expression={"type": "number", "value": [1, 10001]}),
])
def test_malformed_or_private_task_fields_fail_both_validators_and_prompt_construction(mutate):
    task = deepcopy(_compiled("Simplify x + 2"))
    mutate(task)
    for validate in (validate_algebra_task, validate_public_homework_task, build_public_homework_content):
        with pytest.raises((ValueError, TypeError)):
            validate(task)
    with pytest.raises((ValueError, TypeError)):
        build_public_homework_prompt(task, include_image=False)


def test_large_branching_and_deep_asts_fail_before_any_math_evaluation():
    template = _compiled("Simplify x + 2")
    large = {"type": "variable", "name": "x"}
    for _ in range(7):
        large = {"type": "add", "left": large, "right": deepcopy(large)}
    deep = {"type": "variable", "name": "x"}
    for _ in range(15):
        deep = {"type": "neg", "value": deep}
    for expression in (large, deep):
        task = {**template, "expression": expression}
        with pytest.raises((ValueError, TypeError)):
            validate_algebra_task(task)


def test_large_symbolic_exponent_remains_a_small_unevaluated_expression():
    task = _compiled("Graph y = 2^(10000x + 10000)")
    assert task["expression"]["type"] == "pow"
    assert task["expression"]["right"]["type"] == "add"
    payload = build_public_homework_content(task)
    assert len(json.dumps(payload)) < 2000


@pytest.mark.parametrize("question", [
    "Help me factor x^2 - 5x + 6 without just giving me the answer.",
    "Can you help me factor x^2 - 5x + 6 step by step?",
])
def test_learning_qualifiers_do_not_block_an_original_algebra_problem(question):
    result = compile_homework_task(question)
    assert result.task == _compiled("Factor x^2 - 5x + 6")
    assert result.action in {"hint", "explain"}


@pytest.mark.parametrize("phase", ["triage", "safety"])
@pytest.mark.parametrize("question", ["Factor x^2 - 5x + 6", "Solve 2x + y = 7; x - y = 2"])
def test_actual_transport_sends_only_closed_algebra_content(wire, phase, question):
    compiled = compile_homework_task("Name: PRIVATE_ALGEBRA_STUDENT\nGrade: 10\n" + question)
    assert compiled.status == "ready"
    contract = {"task": compiled.task, "action": compiled.action, "stage": compiled.stage,
                "phase": phase, "include_image": False}
    if phase == "safety":
        contract["candidate"] = {"mode": "explanation", "text": "Start by identifying the mathematical operation.", "reference_ids": []}
    prompt = build_public_homework_prompt(**contract)
    connection = {"endpoint": "https://api.openai.com/v1", "public_homework_contract": contract,
                  "name": "PRIVATE_PROFILE", "id": "PRIVATE_SESSION"}
    provider.classify(connection, "synthetic-service-credential", "gpt-6-luna", prompt, 500)
    assert len(wire) == 1
    assert wire[0]["json"]["messages"] == prompt
    payload = json.loads(prompt[1]["content"][0]["text"])
    assert payload["task"] == compiled.task
    assert wire[0]["json"]["store"] is False
    encoded = json.dumps(wire[0])
    for private in ("PRIVATE_ALGEBRA_STUDENT", "PRIVATE_PROFILE", "PRIVATE_SESSION", question):
        assert private not in encoded


def test_algebra_transport_rejects_private_ast_fields_before_network(monkeypatch):
    compiled = compile_homework_task("Factor x^2 - 5x + 6")
    contract = {"task": compiled.task, "action": compiled.action, "stage": compiled.stage,
                "phase": "triage", "include_image": False}
    prompt = build_public_homework_prompt(**contract)
    payload = json.loads(prompt[1]["content"][0]["text"])
    payload["task"]["original_work"] = "PRIVATE_STUDENT_ANSWER"
    prompt[1]["content"][0]["text"] = json.dumps(payload)
    called = []

    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError("A tainted algebra request must fail before network access")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(provider.ProviderFailure, match="nothing was sent"):
        provider.classify({"endpoint": "https://api.openai.com/v1", "public_homework_contract": contract},
                          "synthetic-service-credential", "gpt-6-luna", prompt, 500)
    assert called == []
