"""Independent acceptance for closed, nonpersonal algebra concept questions."""
from copy import deepcopy
import json

import pytest

from academy.homework_deidentification import (
    build_public_homework_prompt,
    compile_homework_task,
    validate_public_homework_task,
)
from test_homework_ai import result, triage_result
from test_homework_deidentified_api import create_child, send, setup_public


CONCEPTS = (
    ("Explain algebra", "algebra_basics"),
    ("How does algebra work?", "algebra_basics"),
    ("What is a linear equation?", "linear_equations"),
    ("How do linear equations work?", "linear_equations"),
    ("Explain linear inequalities", "linear_inequalities"),
    ("How do inequalities work?", "linear_inequalities"),
    ("How does factoring work?", "polynomial_factoring"),
    ("Explain polynomial factoring", "polynomial_factoring"),
    ("What is the quadratic formula?", "quadratic_formula"),
    ("What is quadratic formula?", "quadratic_formula"),
    ("How do I know when to use the quadratic formula?", "quadratic_formula"),
    ("What is completing the square?", "completing_square"),
    ("Explain completing the square", "completing_square"),
    ("What is a quadratic equation?", "quadratics"),
    ("Explain quadratic equations", "quadratics"),
    ("Explain systems of equations", "linear_systems"),
    ("Explain linear systems", "linear_systems"),
    ("How does substitution work?", "system_substitution"),
    ("How does elimination work?", "system_elimination"),
    ("What is the difference between substitution and elimination?", "system_methods"),
    ("What are exponent rules?", "exponent_rules"),
    ("Explain laws of exponents", "exponent_rules"),
    ("Explain radicals", "radicals"),
    ("How do square roots work?", "radicals"),
    ("Explain rational expressions", "rational_expressions"),
    ("How do rational expressions work?", "rational_expressions"),
    ("What is function notation?", "function_notation"),
    ("Explain functions", "function_notation"),
    ("What is slope?", "slope"),
    ("Explain intercepts", "intercepts"),
    ("Explain slope intercept form", "slope_intercepts"),
    ("How are slope and intercepts related?", "slope_intercepts"),
)


@pytest.mark.parametrize("question,topic", CONCEPTS)
def test_complete_algebra_concept_questions_select_the_intended_closed_topic(question, topic):
    result = compile_homework_task(question)
    assert result.status == "ready", (question, result.reason)
    assert result.task == {"kind": "topic", "topic": topic}
    assert validate_public_homework_task(result.task) == result.task


@pytest.mark.parametrize("question,topic", CONCEPTS[::2])
def test_concept_prompts_do_not_depend_on_private_headers_or_history(question, topic):
    clean = compile_homework_task(question)
    wrapped = compile_homework_task(
        "Name: PRIVATE_CONCEPT_STUDENT\nUsername: PRIVATE_CONCEPT_LOGIN\n"
        "School: PRIVATE_CONCEPT_SCHOOL\nGrade: 10\nAge: 15\n"
        "Email: private-concept@example.invalid\n" + question)
    assert clean.status == wrapped.status == "ready"
    assert clean.task == wrapped.task == {"kind": "topic", "topic": topic}
    candidate = {"mode": "explanation", "text": "Identify the relationship between the mathematical quantities.", "reference_ids": []}
    for phase in ("triage", "safety"):
        options = {"phase": phase, "include_image": False}
        if phase == "safety":
            options["candidate"] = candidate
        a = build_public_homework_prompt(clean.task, clean.action, clean.stage, **options)
        b = build_public_homework_prompt(wrapped.task, wrapped.action, wrapped.stage, **options)
        assert a == b
        payload = json.loads(a[1]["content"][0]["text"])
        assert payload["task"] == {"kind": "topic", "topic": topic}
        assert not {"history", "message", "student_id", "course_id", "profile", "grade", "age"}.intersection(payload)
        encoded = json.dumps(a)
        for private in ("PRIVATE_CONCEPT_STUDENT", "PRIVATE_CONCEPT_LOGIN", "PRIVATE_CONCEPT_SCHOOL", "private-concept@example.invalid"):
            assert private not in encoded


@pytest.mark.parametrize("question", [
    "Explain factoring and climate change",
    "How is the quadratic formula related to DNA replication?",
    "Explain the quadratic formula for cubic equations",
    "Explain factoring in ring theory",
    "Explain factorials",
    "Explain linear algebra",
    "Explain rational drug design",
    "Explain rational expressions and photosynthesis",
    "Explain how slope is connected to DNA",
    "Explain a function in Python",
    "Explain the slope of my private roof design",
    "Explain systems of equations in my private report",
    "Explain function notation in this passage: PRIVATE_PASSAGE_SENTINEL",
    "Analyze my classmate's solution to factoring: PRIVATE_ANSWER_SENTINEL",
])
def test_unknown_relationships_private_passages_and_word_collisions_are_not_reduced_to_a_known_topic(question):
    previous = compile_homework_task("Solve 2x + 3 = 7")
    for task in (None, previous.task):
        result = compile_homework_task(question, previous_task=task)
        assert result.status in {"clarify", "blocked"}, (question, result.task)
        assert result.task is None


@pytest.mark.parametrize("question", [
    "Explain sin(x) = 0",
    "Explain log(x) + 1 = 2",
    "Explain the quadratic formula for sin(x) = 0",
    "Explain factoring log(x) + 2",
    "Explain systems of equations x+y=2 and sin(x)=1",
    "Explain quadratic formula x^2 - 5x + 6 = 0 and log(x)",
    "Explain radicals sqrt(x) + SECRET_PRIVATE_VARIABLE = 2",
    "Explain rational expressions 1/(x-2) + PRIVATE_FORMULA",
])
def test_actual_unsupported_expressions_never_fall_back_to_generic_algebra_concepts(question):
    previous = compile_homework_task("Explain factoring")
    for task in (None, previous.task):
        result = compile_homework_task(question, previous_task=task)
        assert result.status == "clarify", (question, result.task)
        assert result.task is None


def test_a_concept_switch_drops_old_equation_then_contextual_followup_keeps_the_concept():
    original = compile_homework_task("Solve 2x + 3 = 7")
    concept = compile_homework_task("How does factoring work?", previous_task=original.task, previous_stage=4)
    assert concept.status == "ready" and concept.task == {"kind": "topic", "topic": "polynomial_factoring"}
    assert concept.stage == 0
    follow = compile_homework_task("Can you explain it another way?", previous_task=concept.task, previous_stage=concept.stage)
    assert follow.status == "ready" and follow.task == concept.task
    assert follow.stage == 1
    prompt = build_public_homework_prompt(follow.task, follow.action, follow.stage, include_image=False)
    payload = json.loads(prompt[1]["content"][0]["text"])
    assert payload["task"] == concept.task
    assert not {"relations", "previous_task", "history"}.intersection(payload)


@pytest.mark.parametrize("mutation", [
    lambda task: task.update(message="PRIVATE_ORIGINAL_QUESTION"),
    lambda task: task.update(history=["PRIVATE_PAST_MESSAGE"]),
    lambda task: task.update(topic="quadratic_formula PRIVATE_PROFILE"),
    lambda task: task.update(topic={"label": "quadratic formula", "name": "PRIVATE_NAME"}),
])
def test_concept_validation_does_not_accept_dynamic_labels_or_private_fields(mutation):
    result = compile_homework_task("What is the quadratic formula?")
    assert result.status == "ready"
    task = deepcopy(result.task)
    mutation(task)
    with pytest.raises((ValueError, TypeError)):
        validate_public_homework_task(task)
    with pytest.raises((ValueError, TypeError)):
        build_public_homework_prompt(task, include_image=False)


def test_local_student_quadratic_formula_question_uses_only_closed_topic_in_both_model_phases(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch)
    child, headers, session_id, account = create_child(client, app, env)
    original = app.state.classify

    def concept_response(*args):
        response = original(*args)
        if args[2] == "triage":
            return result(triage_result(response={"mode": "explanation", "text":
                "For ax^2 + bx + c = 0, the quadratic formula is x = (-b ± sqrt(b^2 - 4ac))/(2a). What do a, b, and c represent?",
                "reference_ids": []}))
        return response

    monkeypatch.setattr(app.state, "classify", concept_response)
    question = "Name: PRIVATE_CONCEPT_STUDENT\nWhat is quadratic formula?"
    try:
        response = send(child, headers, session_id, question, "student-quadratic-formula-concept")
        assert response.status_code == 200 and response.json()["status"] == "released", response.text
        assert "quadratic formula" in response.json()["answer"]
        assert [call["model"] for call in env["public_calls"]] == ["triage", "safety"]
        for call in env["public_calls"]:
            assert call["payload"]["task"] == {"kind": "topic", "topic": "quadratic_formula"}
            assert not {"message", "history", "profile", "student_id", "course_id", "relations"}.intersection(call["payload"])
            wire = json.dumps(call["prompt"])
            for private in ("PRIVATE_CONCEPT_STUDENT", account["id"], session_id, env["school"], question):
                assert private not in wire
    finally:
        child.close()
