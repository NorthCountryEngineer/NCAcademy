"""Synthetic canonical-content and local worksheet recognition acceptance."""
from copy import deepcopy
import json

import pytest

from academy import homework_deidentification as privacy
from academy import provider


@pytest.mark.parametrize(("question", "task"), [
    ("Help with 21 ÷ 3", {"kind": "arithmetic", "operation": "division", "values": [21, 3]}),
    ("Explain 45 plus 18", {"kind": "arithmetic", "operation": "addition", "values": [45, 18]}),
    ("Subtract 14 from 32", {"kind": "arithmetic", "operation": "subtraction", "values": [32, 14]}),
    ("Draw an array with 3 rows and 5 dots in each row.", {"kind": "arithmetic", "operation": "multiplication", "values": [3, 5]}),
    ("A gardener puts 24 seedlings equally into 4 trays. How many go into each tray?", {"kind": "arithmetic", "operation": "division", "values": [24, 4]}),
    ("There are 21 toy ladybugs. Three students share them equally.", {"kind": "arithmetic", "operation": "division", "values": [21, 3]}),
    ("A rectangle has 3 rows of 7 unit squares. Explain its area.", {"kind": "rectangle", "operation": "area", "values": [3, 7]}),
    ("A rectangle is 4 by 6 units. What is its perimeter?", {"kind": "rectangle", "operation": "perimeter", "values": [4, 6]}),
    ("Draw a whole divided into 8 equal parts. Shade 3 parts.", {"kind": "fraction", "values": [3, 8]}),
    ("Compare fractions 1/2 and 3/4", {"kind": "fraction", "values": [1, 2, 3, 4]}),
    ("Round 236 to the nearest hundred", {"kind": "rounding", "values": [236, 100]}),
    ("Write a number greater than 400 and less than 500.", {"kind": "range", "values": [400, 500]}),
    ("The clock shows 2:15. Draw the hands 20 minutes later.", {"kind": "time", "values": [2, 15, 20]}),
    ("Name a shape with four equal sides", {"kind": "shape", "property": "four_equal_sides"}),
    ("Can you help explain photosynthesis?", {"kind": "topic", "topic": "photosynthesis"}),
    ("I don't understand main idea", {"kind": "topic", "topic": "main_idea"}),
])
def test_concrete_math_and_closed_subject_concepts(question, task):
    result = privacy.compile_homework_task(question)
    assert result.status == "ready"
    assert result.task == task
    assert privacy.validate_public_homework_task(result.task) == task


def test_numbered_photo_question_selection_and_followup(monkeypatch):
    transcript = "Name: FICTIONAL PII CANARY\nPhone: 315-555-1212\n1. Draw an array with 3 rows and 5 dots in each row.\n7. There are 21 toy ladybugs. Three students share them equally.\n8. A rectangle has 3 rows of 7 unit squares."
    monkeypatch.setattr(privacy, "_read_image", lambda *_: transcript)
    first = privacy.compile_homework_task("Help with question 1", [b"synthetic original picture"])
    seventh = privacy.compile_homework_task("Question 7. Look at the sheet.", [b"synthetic original picture"], first.task, first.stage, first.question_number)
    assert seventh.task == {"kind": "arithmetic", "operation": "division", "values": [21, 3]}
    assert seventh.question_number == 7 and seventh.image_read
    follow = privacy.compile_homework_task("Why? Can you explain a different way?", previous_task=seventh.task, previous_stage=0, previous_question=7)
    attempt = privacy.compile_homework_task("I think it is 9999", previous_task=follow.task, previous_stage=follow.stage, previous_question=7)
    assert follow.task == attempt.task == seventh.task
    assert (follow.stage, attempt.stage, attempt.action) == (1, 2, "check_method")
    wire = json.dumps(privacy.build_public_homework_prompt(attempt.task, attempt.action, attempt.stage))
    for forbidden in ("FICTIONAL", "315", "9999", "synthetic original picture", "question_number", "student_id"):
        assert forbidden not in wire


@pytest.mark.parametrize("question", [
    "My birthday is 3/4/2017", "Phone: 315-555-1212", "Date: 2026-11-11", "My age is 8",
    "I am 8 years old", "My home is 123 Maple Street", "arbitrary very private personal anecdote",
    "Explain 2 + 3 and 4 + 5", "Compare 1/2, 3/4, and 5/6", "add 2 and 3 then multiply 4 and 5",
    "999999999 / 3", "123456-78", "Divide 4 by 0",
])
def test_unsupported_private_or_ambiguous_content_stays_local(question):
    result = privacy.compile_homework_task(question)
    assert result.status == "clarify" and result.task is None


def test_private_wrapper_does_not_change_mathematics():
    clean = privacy.compile_homework_task("Help with 21 ÷ 3")
    wrapped = privacy.compile_homework_task("My name is FICTIONAL STUDENT.\nPhone: 315-555-1212\nAddress: 123 Maple Street\nHelp with 21 ÷ 3")
    assert clean.task == wrapped.task
    assert privacy.build_public_homework_prompt(clean.task) == privacy.build_public_homework_prompt(wrapped.task)


def test_private_essay_is_not_sent_for_generic_writing_guidance():
    result = privacy.compile_homework_task("My essay is about FICTIONAL FAMILY CANARY and a private experience.")
    assert result.task == {"kind": "topic", "topic": "writing_structure"}
    wire = json.dumps(privacy.build_public_homework_prompt(result.task))
    assert "FICTIONAL FAMILY CANARY" not in wire and "private experience" not in wire


def test_no_silent_reuse_for_new_unsupported_math():
    old = privacy.compile_homework_task("21 ÷ 3")
    assert privacy.compile_homework_task("Explain 2+3 and 4+5", previous_task=old.task).status == "clarify"
    assert privacy.compile_homework_task("Question 8", previous_task=old.task, previous_question=7).status == "clarify"


def test_unreadable_numbered_question_does_not_guess(monkeypatch):
    monkeypatch.setattr(privacy, "_read_image", lambda *_: "1. Draw an array with 3 rows and 5 dots.")
    result = privacy.compile_homework_task("Question 7", [b"photo"])
    assert result.status == "clarify" and result.reason == "question_not_readable"


def test_private_safety_input_never_requires_a_provider():
    result = privacy.compile_homework_task("Fictional testing: I want to hurt myself. Explain 21 ÷ 3")
    assert result.status == "blocked" and result.task is None and result.input_risk_codes == ("self_harm",)
    assert privacy.compile_homework_task("Just give me the answer to 21 ÷ 3").status == "blocked"


def test_explanation_variants_advance_without_forwarding_attempts():
    result = privacy.compile_homework_task("21 ÷ 3")
    prompts = set()
    for stage in range(16):
        assert result.status == "ready" and result.stage == stage
        prompts.add(json.dumps(privacy.build_public_homework_prompt(result.task, result.action, result.stage), sort_keys=True))
        result = privacy.compile_homework_task("Another way please", previous_task=result.task, previous_stage=result.stage)
    assert len(prompts) == 16
    assert result.status == "clarify" and result.reason == "choose_specific_step"


def test_fresh_diagram_has_no_original_bytes_or_metadata():
    task = privacy.compile_homework_task("21 ÷ 3").task
    from PIL import Image
    from io import BytesIO
    image = privacy.render_public_homework_image(task)
    assert image == privacy.render_public_homework_image(deepcopy(task))
    parsed = Image.open(BytesIO(image))
    assert parsed.size == (800, 480) and not parsed.getexif()
    assert b"student" not in image and b"FICTIONAL" not in image


@pytest.mark.parametrize("mutation", ["raw_text", "raw_photo", "child_id", "contract_extra", "connection_child_context", "destination"])
def test_final_transport_guard_rejects_taint_before_dns(monkeypatch, mutation):
    task = privacy.compile_homework_task("21 ÷ 3").task
    contract = {"task": task, "action": "hint", "stage": 0, "phase": "triage"}
    connection = {"endpoint": "https://api.openai.com/v1", "public_homework_contract": contract}
    prompt = privacy.build_public_homework_prompt(**contract)
    if mutation == "raw_text":
        prompt[1]["content"][0]["text"] += "FICTIONAL PRIVATE QUESTION"
    elif mutation == "raw_photo":
        prompt[1]["content"][1]["image_url"]["url"] = "data:image/jpeg;base64,PRIVATE"
    elif mutation == "child_id":
        prompt.append({"role": "user", "content": "student:private"})
    elif mutation == "contract_extra":
        contract["student_id"] = "private"
    elif mutation == "connection_child_context":
        connection["safety_identifier"] = "academy_" + "a" * 64
    else:
        connection["endpoint"] = "https://example.com/v1"
    monkeypatch.setattr(provider.socket, "getaddrinfo", lambda *a, **kw: pytest.fail("privacy rejection must precede DNS"))
    with pytest.raises(provider.ProviderFailure, match="nothing was sent"):
        provider.classify(connection, "synthetic-key", "gpt-6-luna", prompt, 500)
