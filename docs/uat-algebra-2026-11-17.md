# Algebra UAT — November 17, 2026

This record covers synthetic Grade 10-style algebra questions in Homework Help. It does not certify alignment to a particular school course or a complete state standards package. No real learner work or live provider call is used in the automated checks.

## Acceptance criteria

- Preserve the complete mathematical task, including negative signs, operation order, parentheses, equality or inequality direction, and all equations in a system.
- Keep variable names within the admitted mathematical alphabet. Do not send names, question text, worksheet labels, instructions, answers, identifiers, or private history to a provider.
- Use a bounded typed mathematical representation, validated again before prompt construction. Reject unsupported or ambiguous syntax as a whole; never replace it with a simpler fragment or the previous task.
- Provide tutoring guidance without placing a computed final answer in the canonical task or bypassing the independent output review.
- Keep elementary arithmetic, fractions, and ordinary contextual follow-ups working.

## Independent coverage matrix

| Area | Representative checks | Evidence status |
| --- | --- | --- |
| Linear equations | Variables on one or both sides, distribution, rational and decimal coefficients | Passed independent exact-arithmetic witnesses |
| Systems | Two equations, both variable orders, dependent and inconsistent equations | Passed; both relations retained |
| Inequalities | `<`, `>`, `<=`, `>=`, Unicode comparisons, negative multipliers, compound endpoints | Passed truth-value and endpoint checks |
| Polynomial expressions | Factoring, expansion, collection of like terms, implicit multiplication | Passed exact-arithmetic witnesses |
| Quadratics | Standard and factored forms, repeated roots, no real roots, degenerate leading coefficient | Passed; inappropriate explicit quadratic methods rejected |
| Functions and lines | Matching `f`/`g` definitions and evaluation; graph, zeros, vertex, slope, domain and range questions; standard-form and vertical lines | Passed; mismatched function labels rejected and vertical relations preserved |
| Powers and radicals | Parenthesis-sensitive powers, negative and zero exponents, square roots, `sqrt(x^2)` | Passed; original domain conditions retained |
| Bounds and malformed input | Oversized coefficients/exponents, zero denominators, deep/large ASTs, truncated syntax, extra expressions | Passed; no partial or previous-task fallback |
| Privacy and request integrity | Identity-wrapper invariance, exact canonical payload, malicious fields, no unrelated task reuse | Passed both prompt phases and mocked final transport |

A clarification is acceptable only when it preserves the user's task and clearly avoids a false answer; it does not count as support for that algebra form.

## Findings corrected during independent review

| ID | Finding | Correction and regression |
| --- | --- | --- |
| ALG-01 | Common `Given f(x)=..., find f(a)` ordering did not parse. | Added the complete definition-first form; retained the exact function input. |
| ALG-02 | A request to evaluate `g` could silently use the supplied definition of `f`. | Function labels must match in every admitted ordering. Matching `g` remains supported. |
| ALG-03 | Zero powers could omit the nonzero-base condition even though evaluation rejects `0^0`. | `x^0` and `(x-1)^0` retain the same domain obligation as negative powers. |
| ALG-04 | A compound inequality could suppress the negative-scaling reminder based only on its first relation. | Every inequality receives the conditional reversal rule; both relations and inclusive endpoints stay intact. |
| ALG-05 | Explicit quadratic methods could be accepted for a linear equation and then ignored. | A quadratic method now requires a quadratic equation. |
| ALG-06 | A bare new equation could be treated as an answer to an unrelated previous algebra question. | Ambiguous bare input now asks locally whether it is a new problem or an answer; it never silently reuses the old task. |
| ALG-07 | The ordinary phrase `I think the answer is ...` did not retain the original algebra task. | Explicit answer-checking phrases retain the original task and send only a general checking strategy, never the student's expression. |
| ALG-08 | An attempt labelled as a different question could reuse the previous question, while punctuation after a typed question label could block parsing. | Explicit question-number changes require the original problem; ordinary colon/period labels preserve the selected equation. |

## Verification

From the repository root:

```sh
PYTHONPATH=apps/api .venv/bin/python -m pytest \
  apps/api/tests/test_algebra_independent_uat.py \
  apps/api/tests/test_homework_deidentification.py \
  apps/api/tests/test_homework_independent_uat.py -q
```

Result: **262 passed**. The suite uses original fixtures in `algebra_uat_cases.py`, evaluates the public AST independently with exact rational arithmetic, tests its domain behavior, and preserves elementary Homework Help regressions. The only warning was the existing Starlette/AnyIO deprecation notice.

The transport checks replace DNS and HTTP with synthetic test doubles. They confirm that triage and safety receive the same closed algebra representation, no original private wrapper reaches the wire, storage remains disabled, and a private field inserted into the public AST is rejected before DNS. These checks do not assert that every possible model explanation will be correct; live student-flow acceptance and deployment verification are recorded separately.

The API regressions also passed. A synthetic local student submits the originally reported factoring question, then an explicit proposed answer. Both model phases retain the original polynomial; the answer expression remains only in encrypted Academy history. Replay returns the recorded response without repeating model calls. Unsupported trigonometry retains the original editable question and makes no model call.

## Deliberate limits

- The admitted variable alphabet is `x` and `y`; one-variable function bodies use `x`. Function labels are `f` or `g` and are removed from the canonical representation after checking that references match.
- Equation solving admits polynomials through degree two and two-equation linear systems. Factoring and expansion admit polynomials through degree four. Supported non-polynomial syntax includes real square roots, absolute values, rational expressions, and positive constant-base exponentials with a linear exponent.
- Standard-form linear graph and slope questions retain their equations, including vertical lines. General implicit nonlinear graphing is outside this parser's scope.
- Literal powers are bounded integers with absolute value at most 10. Noninteger powers such as `x^(1/2)` require explicit supported square-root notation. Constant radicals outside the real domain, such as `sqrt(-1)`, ask for clarification.
- Each task has at most 64 AST nodes and depth 12. Constants have absolute value at most 10,000, and rational denominators are at most 10,000. Evaluation has additional magnitude and exponent limits; a large symbolic exponential remains symbolic rather than expanding into a huge number.
- Unknown clauses, incomplete expressions, ambiguous `x2` notation, arbitrary named variables, and unsupported multi-function requests ask for clarification as complete requests. They do not degrade to a smaller equation or inherit unrelated earlier work.
