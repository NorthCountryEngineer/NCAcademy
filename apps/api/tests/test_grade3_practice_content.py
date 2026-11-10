"""Reviewable invariants of the original Grade 3 problem templates."""
import ast
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from academy.grade3_practice_content import (
    BANK_VERSION,
    MAX_QUESTIONS,
    PRACTICE_LESSON_IDS,
    generate_practice,
    grade3_practice_course,
    grade_practice_item,
    practice_topics,
    public_practice_item,
)


# Independently enumerated from the official Grade 3 section, including
# printed parent statements and components. Practices are evaluated separately.
_EXPECTED = {
    "OA.1", "OA.2", "OA.3", "OA.4", "OA.5", "OA.6", "OA.7a", "OA.7b",
    "OA.8", "OA.8a", "OA.8b", "OA.9",
    "NBT.1", "NBT.2", "NBT.3", "NBT.4a", "NBT.4b",
    "NF.1", "NF.2", "NF.2a", "NF.2b", "NF.3", "NF.3a", "NF.3b", "NF.3c", "NF.3d",
    "MD.1", "MD.2a", "MD.2b", "MD.3", "MD.4", "MD.5", "MD.5a", "MD.5b", "MD.6",
    "MD.7", "MD.7a", "MD.7b", "MD.7c", "MD.7d", "MD.8a", "MD.8b", "G.1", "G.2",
}


def _questions(topic, seeds=16):
    return [item for seed in range(seeds) for item in generate_practice(topic, seed, MAX_QUESTIONS)]


def _correct_label(item):
    return next(option["label"] for option in item["options"] if grade_practice_item(item, option["id"]))


def _content_signature(items):
    return [(q["prompt"], q.get("figure")) for q in items]


def test_selectable_skills_and_units_match_every_official_alignment_and_source_reference():
    package = json.loads((Path(__file__).parents[1] / "academy/standards_data/ny_grade3_mathematics_2026.json").read_text())
    lessons = {f"reference:{d['id']}": set(d["standard_codes"]) for d in package["teaching_documents"]}
    topics = practice_topics()
    skills = [t for t in topics if not t["id"].startswith("unit-")]
    units = {t["unit"]: t for t in topics if t["id"].startswith("unit-")}
    assert BANK_VERSION
    assert len(skills) == len(_EXPECTED) == 44
    assert len({t["id"] for t in topics}) == len(topics) == 53
    assert set(s for t in skills for s in t["standards"]) == {f"NY-3.{s}" for s in _EXPECTED}
    assert len(PRACTICE_LESSON_IDS) == len(lessons) == 27
    assert PRACTICE_LESSON_IDS == set(lessons)
    assert set(units) == set(range(1, 10))
    for row in package["local_sequence"]:
        topic = units[row["order"]]
        assert set(topic["standards"]) == set(row["standard_codes"])
        # An assignment with one slot per skill in a unit should cover
        # them all before repeating a skill.
        items = generate_practice(topic["id"], "unit-review", len(topic["standards"]))
        assert {s for item in items for s in item["standards"]} == set(row["standard_codes"])
        assert {item["lesson_id"] for item in items} == {f"reference:{key}" for key in row["document_ids"]}
    for topic in skills:
        item, = generate_practice(topic["id"], "skill-review", 1)
        assert item["standards"] == topic["standards"]
        assert set(item["standards"]) <= lessons[item["lesson_id"]]


@pytest.mark.parametrize("topic", [row["id"] for row in practice_topics()])
def test_maximum_set_varies_prompt_content_and_retains_a_valid_private_key(topic):
    a = generate_practice(topic, "review-A", MAX_QUESTIONS)
    b = generate_practice(topic, "review-B", MAX_QUESTIONS)
    assert a == generate_practice(topic, "review-A", MAX_QUESTIONS)
    assert _content_signature(a) != _content_signature(b)
    for items in (a, b, generate_practice(topic, 0, MAX_QUESTIONS)):
        assert len(set(_content_signature(items))) == len(items) == MAX_QUESTIONS
        assert len({item["id"] for item in items}) == MAX_QUESTIONS
        for item in items:
            assert item["id"].isascii() and 1 <= len(item["id"]) <= 64
            assert item["practice"]["prompt"]
            assert item["lesson_id"] in PRACTICE_LESSON_IDS
            public = public_practice_item({**item, "unknown_server_data": "private"})
            assert set(public) == set(item) - {"answer_key"}
            assert not grade_practice_item(item, "not a valid response")
            if public["answer_type"] == "choice":
                labels = [o["label"] for o in public["options"]]
                assert 2 <= len(set(labels)) == len(labels) <= 5
                hits = [o for o in public["options"] if grade_practice_item(item, o["id"])]
                assert len(hits) == 1
                assert not grade_practice_item(item, hits[0]["label"])
                # Projection detaches the options and must not modify storage.
                public["options"][0]["label"] = "changed"
                assert item["options"][0]["label"] != "changed"
            else:
                key = item["answer_key"]
                value = key["value"]
                joiner = "/" if key["kind"].startswith("fraction") else ","
                answer = joiner.join(map(str, value)) if isinstance(value, list) else str(value)
                assert grade_practice_item(item, answer)


def test_mathematical_practices_are_explicit_ungraded_reflection():
    items = [q for t in practice_topics() for q in generate_practice(t["id"], "reflection-review", 6)]
    assert {q["practice"]["code"] for q in items} == {f"MP.{i}" for i in range(1, 9)}
    assert all(all(s.startswith("NY-3.") for s in q["standards"]) for q in items)


def test_products_and_rounding_match_independent_arithmetic():
    for item in _questions("multiplication-facts"):
        a, b = map(int, re.fullmatch(r"What is (\d) × (\d)\?", item["prompt"]).groups())
        assert grade_practice_item(item, str(sum([a] * b)))
        assert not grade_practice_item(item, str(a * b + 1))
    seen = set()
    for item in _questions("rounding"):
        n, target = re.search(r"Round ([\d,]+) to the nearest (ten|hundred)\.", item["prompt"]).groups()
        n = int(n.replace(",", ""))
        k = {"ten": 10, "hundred": 100}[target]
        expected = min(range(0, 1001, k), key=lambda m: (abs(m - n), -m))
        seen.add(target)
        assert grade_practice_item(item, f"{expected:,}")
    assert seen == {"ten", "hundred"}


def _value_of_expression(text):
    """Evaluate only the generated constant/arithmetic expression grammar."""
    def walk(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        assert isinstance(node, ast.BinOp)
        a, b = walk(node.left), walk(node.right)
        if isinstance(node.op, ast.Add):
            return a + b
        assert isinstance(node.op, ast.Mult)
        return a * b
    return walk(ast.parse(text.replace("×", "*"), mode="eval").body)


def test_rectangle_tile_model_has_one_numerically_correct_expression():
    seen_tie = False
    for item in _questions("rectangle-tiles"):
        # Count marks in the figure rather than reading the stored dimensions.
        area = item["figure"].count("■")
        if item["answer_type"] == "text":
            assert grade_practice_item(item, str(area))
            continue
        n_rows = len(item["figure"].splitlines())
        seen_tie |= 2 * (n_rows + area // n_rows) == area
        expected_ids = [o["id"] for o in item["options"] if _value_of_expression(o["label"]) == area]
        assert len(expected_ids) == 1
        assert grade_practice_item(item, expected_ids[0])
    assert seen_tie


def test_equal_area_or_perimeter_has_a_different_other_quantity():
    relations = set()
    for item in _questions("area-versus-perimeter"):
        a, b, c, d = map(int, re.findall(r"\d+", item["prompt"]))
        eq_area, eq_perim = a * b == c * d, a + b == c + d
        assert eq_area ^ eq_perim
        relations.add((eq_area, eq_perim))
        expect = "Same area, different perimeter." if eq_area else "Same perimeter, different area."
        assert _correct_label(item) == expect
    assert len(relations) == 2


def test_equivalent_fraction_whole_has_equal_visual_width_and_unique_equivalent_option():
    denominators = {2, 3, 4, 6, 8}
    for item in _questions("equivalent-fraction-location"):
        target, = re.findall(r"\d+/\d+", item["prompt"])
        equivalent = [o for o in item["options"] if Fraction(o["label"]) == Fraction(target)]
        assert len(equivalent) == 1
        assert grade_practice_item(item, equivalent[0]["id"])
        strips = [row.split(": ", 1)[-1] for row in item["figure"].splitlines()]
        assert len(strips) == 2 and len(strips[0]) == len(strips[1])
        assert strips[0].index("·") == strips[1].index("·")
        for option in item["options"]:
            assert int(option["label"].split("/")[-1]) in denominators


def test_fraction_comparisons_and_beyond_one_are_not_floating_point():
    for item in _questions("compare-fractions"):
        left, right = map(Fraction, re.findall(r"\d+/\d+", item["prompt"]))
        expected = "<" if left < right else ">" if left > right else "="
        assert _correct_label(item) == expected
    for item in _questions("fraction-jumps"):
        if item["answer_type"] == "text" and "make" in item["prompt"]:
            jumps, unit = map(int, re.search(r"make (\d+) jumps of 1/(\d+)", item["prompt"]).groups())
            assert jumps > unit
            assert grade_practice_item(item, f"{jumps}/{unit}")
            assert not grade_practice_item(item, f"{unit}/{jumps}")


def test_scaled_graph_and_fractional_line_plot_counts_match_the_described_data():
    for item in _questions("scaled-graphs"):
        if item["answer_key"]["kind"] != "counts":
            continue
        amounts = [int(row.split(": ")[-1]) for row in item["figure"].splitlines()]
        scale = int(re.search(r"Each mark stands for (\d+)", item["prompt"])[1])
        counts = [n // scale for n in amounts]
        assert all(n % scale == 0 for n in amounts)
        assert grade_practice_item(item, ", ".join(map(str, counts)))
        assert not grade_practice_item(item, ", ".join(map(str, amounts)))
    for item in _questions("inch-measurements-and-line-plots"):
        if item["answer_key"]["kind"] != "counts":
            continue
        measurements, labels = re.search(
            r"Lengths in inches: (.*?)\. Make a line plot with marks at (.*?) inches\.",
            item["prompt"],
        ).groups()
        counts = Counter(measurements.split("; "))
        assert set(counts) <= set(labels.split("; "))
        expected = [counts[label] for label in labels.split("; ")]
        assert len(expected) == 4 and sum(expected) >= 1
        assert grade_practice_item(item, ",".join(map(str, expected)))
        assert not grade_practice_item(item, ",".join(map(str, [sum(expected), *expected])))


@pytest.mark.parametrize("kind,value,correct,wrong", [
    ("whole", 1024, ("1024", "1,024", " 1024 "), ("1,02,4", "1024.0", "1 024")),
    ("fraction", [1, 2], ("1/2", "2 / 4", "4/8", "3/6"), ("2/3", "1/0", "5/10", "1 / 2 / 3")),
    ("fraction", [8, 4], ("8/4", "4/2", "2"), ("3", "1/4", "4/8")),
    ("fraction-parts", [2, 4], ("2/4", "2 / 4"), ("1/2", "4/8", "0.5")),
    ("clock", "9:05", ("9:05", "09:05", "9:5"), ("9.05", "21:05", "9:60", "9:50")),
    ("counts", [4, 0, 6], ("4,0,6", "4, 0, 6"), ("4,6", "4,6,0", "4,0,6,0", "4,0,+6")),
    ("choice", "b", ("b",), ("B", "a", "bb")),
])
def test_bounded_response_grammar(kind, value, correct, wrong):
    item = {"answer_key": {"kind": kind, "value": value}}
    for answer in correct:
        assert grade_practice_item(item, answer)
    for answer in (*wrong, " " * 121, None, True, 1, [value]):
        assert not grade_practice_item(item, answer)


@pytest.mark.parametrize("fields", [
    {"state": "US-NJ"}, {"subject": "science"}, {"grades": [3, 4]}, {"grades": [3.0]},
    {"academic_year": "2025-2026"}, {"standards_version": "unreviewed"}, {"mode": "custom"},
])
def test_exact_course_context_required(fields):
    exact = {"state": "US-NY", "subject": "mathematics", "grades": [3], "academic_year": "2026-2027"}
    assert grade3_practice_course(SimpleNamespace(value={"curriculum": exact}))
    assert not grade3_practice_course(SimpleNamespace(value={"curriculum": {**exact, **fields}}))
    assert not grade3_practice_course(SimpleNamespace(value={"title": "Math Grade 3"}))


@pytest.mark.parametrize("topic,seed,count", [
    ("not-a-topic", 1, 5), ("unit-3", None, 3), ("unit-3", "", 3), ("unit-3", -1, 3),
    ("unit-3", 1, 0), ("unit-3", 1, MAX_QUESTIONS + 1), ("unit-3", 1, True),
])
def test_generation_rejects_unbounded_or_unknown_inputs(topic, seed, count):
    with pytest.raises(ValueError):
        generate_practice(topic, seed, count)
