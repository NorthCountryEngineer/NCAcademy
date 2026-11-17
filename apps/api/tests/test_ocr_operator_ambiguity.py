"""Photo operator ambiguity cannot silently change the mathematical task."""
import pytest

from academy import homework_deidentification as compiler


def test_ocr_asterisk_does_not_change_a_power_to_multiplication(monkeypatch):
    monkeypatch.setattr(compiler, "_read_image", lambda *_: "4. Simplify (x*2 - 9)/(x - 3)")
    result = compiler.compile_homework_task("Help me with question 4", [b"synthetic photo"])
    assert result.status == "clarify" and result.task is None
    assert result.reason == "ambiguous_math_symbol" and result.image_read
    assert result.question_number == 4


def test_ambiguous_reread_cannot_reuse_a_prior_task(monkeypatch):
    previous = compiler.compile_homework_task("Simplify (x^2 - 9)/(x - 3)")
    monkeypatch.setattr(compiler, "_read_image", lambda *_: "4. Simplify (x*2 - 9)/(x - 3)")
    result = compiler.compile_homework_task("Look again at question 4", [b"synthetic photo"], previous.task, 0, 4)
    assert result.status == "clarify" and result.task is None
    assert result.reason == "ambiguous_math_symbol"


@pytest.mark.parametrize("expression", ["(x^2 - 9)/(x - 3)", "(x*2 - 9)/(x - 3)"])
def test_complete_typed_expression_overrides_photo_ocr_without_guessing(expression, monkeypatch):
    def forbidden(*_args):
        raise AssertionError("The complete typed problem must not be replaced by OCR")
    monkeypatch.setattr(compiler, "_read_image", forbidden)
    result = compiler.compile_homework_task(f"Question 4: Simplify {expression}", [b"synthetic retained photo"])
    expected = compiler.compile_homework_task(f"Simplify {expression}")
    assert result.status == "ready" and result.task == expected.task
    assert result.question_number == 4 and result.image_read is False


def test_unambiguous_ocr_caret_preserves_the_power(monkeypatch):
    monkeypatch.setattr(compiler, "_read_image", lambda *_: "4. Simplify (x^2 - 9)/(x - 3)")
    result = compiler.compile_homework_task("Help me with question 4", [b"synthetic photo"])
    expected = compiler.compile_homework_task("Simplify (x^2 - 9)/(x - 3)")
    assert result.status == "ready" and result.task == expected.task
    assert result.image_read
