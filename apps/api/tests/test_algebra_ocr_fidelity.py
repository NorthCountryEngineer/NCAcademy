"""Synthetic regression for the live caret-to-asterisk OCR failure."""
import json

import pytest

from academy import homework_deidentification as compiler
from test_curated_lessons import photo
from test_homework_deidentified_api import BASE, create_child, send, setup_public


AMBIGUOUS_OCR = "4. Simplify (x*2 - 9)/(x - 3)"
TYPED_CORRECTION = "Question 4: Simplify (x^2 - 9)/(x - 3)"


def test_ocr_asterisk_never_silently_replaces_a_printed_exponent(monkeypatch):
    monkeypatch.setattr(compiler, "_read_image", lambda *_: AMBIGUOUS_OCR)
    result = compiler.compile_homework_task("Help with question 4", [b"synthetic worksheet"])
    assert result.status == "clarify" and result.task is None
    assert result.reason == "ambiguous_math_symbol" and result.image_read


@pytest.mark.parametrize("text,operator", [
    (TYPED_CORRECTION, "pow"),
    ("Question 4: Simplify (x*2 - 9)/(x - 3)", "mul"),
])
def test_complete_typed_math_overrides_retained_ocr_without_changing_its_operator(monkeypatch, text, operator):
    def no_ocr(*args):
        pytest.fail("A complete typed correction must not be overwritten by retained OCR")

    monkeypatch.setattr(compiler, "_read_image", no_ocr)
    result = compiler.compile_homework_task(text, [b"synthetic retained worksheet"])
    assert result.status == "ready" and result.task["kind"] == "algebra"
    assert result.task["expression"]["type"] == "div"
    assert result.task["expression"]["left"]["left"]["type"] == operator
    assert result.question_number == 4 and not result.image_read


def test_unambiguous_ocr_caret_retains_the_original_quadratic(monkeypatch):
    monkeypatch.setattr(compiler, "_read_image", lambda *_: "4. Simplify (x^2 - 9)/(x - 3)")
    result = compiler.compile_homework_task("Help with question 4", [b"synthetic worksheet"])
    assert result.status == "ready" and result.image_read
    assert result.task["expression"]["left"]["left"]["type"] == "pow"


@pytest.mark.parametrize("text", ["Question 4: Solve sin(x) = 0", "Question 4: Solve log(x) = 2"])
@pytest.mark.parametrize("previous", [None, "Solve 2x + 3 = 7"])
def test_unsupported_explicit_typed_math_is_not_replaced_with_a_photo_problem(monkeypatch, text, previous):
    def no_ocr(*args):
        pytest.fail("Unsupported explicit typed math must not fall back to a different photo problem")

    monkeypatch.setattr(compiler, "_read_image", no_ocr)
    prior = compiler.compile_homework_task(previous).task if previous else None
    result = compiler.compile_homework_task(text, [b"synthetic retained worksheet"],
                                           previous_task=prior, previous_question=4 if prior else None)
    assert result.status == "clarify" and result.task is None
    assert result.reason == "unsupported_math" and not result.image_read


def test_student_can_correct_ambiguous_ocr_while_the_original_image_stays_private(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch, allow_images=True, triage_image_capable=True,
                       tutor_image_capable=True, safety_image_capable=True, max_daily_tokens=1000000)
    monkeypatch.setattr(compiler, "_read_image", lambda *_: AMBIGUOUS_OCR)
    child, headers, session_id, account = create_child(client, app, env)
    try:
        assistant = child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
        policy = assistant["image_policy_sha256"]
        original = send(child, headers, session_id, "Help with question 4", "ambiguous-ocr-original",
                        images=[{"name": "PRIVATE_ALGEBRA_WORKSHEET.png", "data_url": photo()}],
                        image_use_confirmed=True, expected_image_policy_sha256=policy)
        assert original.status_code == 200 and original.json()["status"] == "clarify", original.text
        assert original.json()["deidentification"]["reason"] == "ambiguous_math_symbol"
        assert original.json()["message"] == "Help with question 4"
        assert len(original.json()["images"]) == 1 and env["public_calls"] == []

        def no_ocr(*args):
            pytest.fail("The typed correction must take precedence over the retained photo")

        monkeypatch.setattr(compiler, "_read_image", no_ocr)
        corrected = send(child, headers, session_id, TYPED_CORRECTION, "typed-ocr-correction",
                         previous_image_message_id=original.json()["id"], image_use_confirmed=True,
                         expected_image_policy_sha256=policy)
        assert corrected.status_code == 200 and corrected.json()["status"] == "released", corrected.text
        assert [call["model"] for call in env["public_calls"]] == ["triage", "safety"]
        for call in env["public_calls"]:
            task = call["payload"]["task"]
            assert task["kind"] == "algebra" and task["action"] == "simplify"
            assert task["expression"]["left"]["left"]["type"] == "pow"
            assert all(part["type"] == "text" for part in call["prompt"][1]["content"])
            wire = json.dumps(call["prompt"])
            for private in ("PRIVATE_ALGEBRA_WORKSHEET", account["id"], session_id):
                assert private not in wire
        history = child.get(f"{BASE}/sessions/{session_id}").json()["messages"]
        assert history[0] == original.json()
        assert len(history[0]["images"]) == 1
    finally:
        child.close()


@pytest.mark.parametrize("start_with_generic_photo_question", [False, True])
def test_student_unsupported_typed_math_never_runs_ocr_or_models_against_attached_or_retained_photo(
        client, app, monkeypatch, start_with_generic_photo_question):
    env = setup_public(client, app, monkeypatch, allow_images=True, triage_image_capable=True,
                       tutor_image_capable=True, safety_image_capable=True, max_daily_tokens=1000000)
    reads = []

    def read_original(*args):
        reads.append(True)
        return "4. Solve 2x + 3 = 7"

    monkeypatch.setattr(compiler, "_read_image", read_original)
    child, headers, session_id, account = create_child(client, app, env)
    try:
        assistant = child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
        policy = assistant["image_policy_sha256"]
        first_question = "Help with question 4" if start_with_generic_photo_question else "Question 4: Solve sin(x) = 0"
        original = send(child, headers, session_id, first_question, "typed-math-photo-source",
                        images=[{"name": "PRIVATE_UNSUPPORTED_MATH_SOURCE.png", "data_url": photo()}],
                        image_use_confirmed=True, expected_image_policy_sha256=policy)
        assert original.status_code == 200, original.text
        assert original.json()["status"] == ("released" if start_with_generic_photo_question else "clarify")
        assert len(reads) == (1 if start_with_generic_photo_question else 0)
        calls_before = len(env["public_calls"])
        assert calls_before == (2 if start_with_generic_photo_question else 0)
        assert len(original.json()["images"]) == 1

        def no_ocr(*args):
            pytest.fail("A retained image cannot replace the new unsupported typed equation")

        monkeypatch.setattr(compiler, "_read_image", no_ocr)
        question = "Question 4: Solve log(x) = 2"
        unsupported = send(child, headers, session_id, question, "unsupported-retained-photo-equation",
                           previous_image_message_id=original.json()["id"], image_use_confirmed=True,
                           expected_image_policy_sha256=policy)
        assert unsupported.status_code == 200 and unsupported.json()["status"] == "clarify", unsupported.text
        assert unsupported.json()["message"] == question
        assert unsupported.json()["deidentification"]["task_summary"] is None
        assert len(env["public_calls"]) == calls_before
        repeated = send(child, headers, session_id, question, "unsupported-retained-photo-equation",
                        previous_image_message_id=original.json()["id"], image_use_confirmed=True,
                        expected_image_policy_sha256=policy)
        assert repeated.json() == unsupported.json() and len(env["public_calls"]) == calls_before
        history = child.get(f"{BASE}/sessions/{session_id}").json()["messages"]
        assert history[0] == original.json() and len(history[0]["images"]) == 1
    finally:
        child.close()
