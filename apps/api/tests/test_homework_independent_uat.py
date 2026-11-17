"""Independent synthetic UAT: preserve each question's mathematical meaning."""
import pytest

from academy.homework_deidentification import compile_homework_task


@pytest.mark.parametrize("question", [
    "Explain 2 + 3%",
    "Explain negative two plus three",
    "Explain 2 plus 3 and then double it",
    "Explain double 2 plus 3",
    "Explain twenty - one plus two",
    "Explain fifty - two plus three",
    "Explain 2+3 factorial",
    "Explain 2+3 pi",
    "Explain 2/3 factorial",
    "Explain 2/3 + pi",
    "Compare fractions 1/2 and 3/4 factorial",
    "Explain 2 + 3 / apples",
])
def test_unsupported_operator_or_modifier_is_not_dropped(question):
    result = compile_homework_task(question)
    assert result.status == "clarify"
    assert result.task is None


@pytest.mark.parametrize("question", [
    "How does DNA replicate?",
    "Explain cellular respiration in anaerobic bacteria",
    "Explain the causes of World War I",
    "Why does Macbeth see a dagger?",
    "Explain three quarters",
    "Explain factorial 3! + 2",
    "Explain the square root of 9",
    "Explain 20% of 50",
    "Explain two thirds plus one third",
    "Three bags hold four marbles each. How many marbles?",
    "Explain 2➕3",
    "Explain 2➖3",
    "Explain 2⋅3",
    "Explain 1/2/3",
    "Explain 1-2-3",
    "Explain 555-010-9999",
    "Explain 555 010 9999",
    "Explain 17",
    "I think the answer is 3 or 4",
])
def test_new_unsupported_question_never_inherits_unrelated_old_math(question):
    previous = compile_homework_task("21 divided by 3")
    result = compile_homework_task(question, previous_task=previous.task, previous_stage=0)
    assert result.status == "clarify"
    assert result.task is None


@pytest.mark.parametrize("question", [
    "Why?",
    "I still do not understand",
    "Can you explain it another way?",
    "Another problem please",
    "I don't get it",
    "Can you give me a hint?",
    "What does that mean?",
    "I think the answer is 7",
    "7",
])
def test_unambiguous_context_only_followups_retain_the_task(question):
    previous = compile_homework_task("21 divided by 3")
    result = compile_homework_task(question, previous_task=previous.task, previous_stage=0)
    assert result.status == "ready"
    assert result.task == previous.task
    assert result.stage == 1


def test_explicit_hint_followup_keeps_hint_intent():
    previous = compile_homework_task("21 divided by 3")
    result = compile_homework_task("Can you give me a hint?", previous_task=previous.task)
    assert result.task == previous.task
    assert result.action == "hint"


@pytest.mark.parametrize(("question", "task"), [
    ("Explain 2 / 3", {"kind": "arithmetic", "operation": "division", "values": [2, 3]}),
    ("Why does 21 / 3 work?", {"kind": "arithmetic", "operation": "division", "values": [21, 3]}),
    ("I don't understand 21 / 3", {"kind": "arithmetic", "operation": "division", "values": [21, 3]}),
    ("Compare fractions 1/2 and 3/4", {"kind": "fraction", "values": [1, 2, 3, 4]}),
    ("Which is larger, 1/2 or 3/4?", {"kind": "fraction", "values": [1, 2, 3, 4]}),
])
def test_complete_division_and_fraction_requests_still_work(question, task):
    result = compile_homework_task(question)
    assert result.status == "ready"
    assert result.task == task


@pytest.mark.parametrize(("question", "topic"), [
    ("Explain cellular respiration", "cellular_respiration"),
    ("How are photosynthesis and cellular respiration related?", "photosynthesis_respiration_relationship"),
    ("Compare cellular respiration and photosynthesis.", "photosynthesis_respiration_relationship"),
    ("What is the difference between photosynthesis and cellular respiration?", "photosynthesis_respiration_relationship"),
    ("How does photosynthesis work?", "photosynthesis"),
    ("How can I find the main idea?", "main_idea"),
])
def test_complete_public_concept_requests_preserve_both_sides_of_a_relationship(question, topic):
    result = compile_homework_task(question)
    assert result.status == "ready"
    assert result.task == {"kind": "topic", "topic": topic}


@pytest.mark.parametrize("question", [
    "How are photosynthesis and DNA replication related?",
    "Explain the light dependent reactions in photosynthesis.",
    "What is the main idea of this passage: A private family story?",
    "Explain why photosynthesis caused the event in my private story.",
    "Analyze my essay about a private family experience.",
])
def test_a_recognized_keyword_does_not_erase_unknown_topics_or_private_passage_requests(question):
    previous = compile_homework_task("21 divided by 3")
    result = compile_homework_task(question, previous_task=previous.task)
    assert result.status == "clarify"
    assert result.task is None


def test_unsupported_trigonometry_is_distinguished_from_unreadable_input():
    result = compile_homework_task("Help me solve sin(x) = 0 without just giving me the answer.")
    assert result.status == "clarify" and result.task is None
    assert result.reason == "unsupported_math"


@pytest.mark.parametrize("question", [
    "Explain (2+3)*x", "Explain x(2+3)", "Explain 2+3×x",
    "Explain 2+3 < x", "Explain 2+3 > x", "Explain √4 + 2",
    "Explain 2 + (3)", "Explain 2 + -3", "Explain 2 + −3", "Explain 4⁻² + 3",
])
def test_new_algebra_support_replaces_old_rejection_without_reusing_prior_arithmetic(question):
    previous = compile_homework_task("21 divided by 3")
    result = compile_homework_task(question, previous_task=previous.task)
    assert result.status == "ready"
    assert result.task["kind"] == "algebra" and result.task != previous.task


def test_bare_new_equation_cannot_silently_reuse_an_unrelated_prior_algebra_problem():
    previous = compile_homework_task("Solve 2x + 3 = 7")
    fresh = compile_homework_task("x^2 - 5x + 6 = 0")
    result = compile_homework_task("x^2 - 5x + 6 = 0", previous_task=previous.task)
    assert result.task != previous.task
    if result.status == "ready":
        assert result.task == fresh.task
    else:
        assert result.status == "clarify" and result.task is None
        assert result.reason == "algebra_context_needed"


def test_changed_question_number_cannot_reuse_the_previous_algebra_problem_for_an_attempt():
    previous = compile_homework_task("Solve 2x + 3 = 7")
    result = compile_homework_task("Question 2 I got x=2", previous_task=previous.task, previous_question=1)
    assert result.status == "clarify" and result.task is None
    assert result.question_number == 2


@pytest.mark.parametrize("label", ["Question 1:", "Question 1.", "Question 1"])
def test_explicit_question_label_does_not_change_the_algebra(label):
    plain = compile_homework_task("Solve 2x + 3 = 7")
    result = compile_homework_task(f"{label} Solve 2x + 3 = 7")
    assert result.status == "ready" and result.task == plain.task
    assert result.question_number == 1


def test_public_science_depth_does_not_depend_on_private_age_or_profile():
    import json
    from academy.homework_deidentification import build_public_homework_prompt
    questions = [f"Name: PRIVATE_LEARNER_{grade}\nGrade: {grade}\nAge: {age}\nHow are photosynthesis and cellular respiration related?"
                 for grade, age in [(3, 8), (10, 15)]]
    tasks = [compile_homework_task(question).task for question in questions]
    prompts = [build_public_homework_prompt(task) for task in tasks]
    assert tasks[0] == tasks[1] == {"kind": "topic", "topic": "photosynthesis_respiration_relationship"}
    assert prompts[0] == prompts[1]
    assert "PRIVATE_LEARNER" not in json.dumps(prompts)
    assert "young children" not in prompts[0][0]["content"]
    assert "child-safety protections" in prompts[0][0]["content"]
    review = build_public_homework_prompt(tasks[0], phase="safety", candidate={"mode": "explanation", "text": "A public science explanation.", "reference_ids": []})
    assert "Treat unknown ages conservatively as children" in review[0]["content"]
