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
| Concept questions | Algebra basics, equations/inequalities, factoring, quadratic formula, completing the square, systems/methods, exponents, radicals, rational expressions, functions, slope/intercepts | Closed-topic compiler, privacy and synthetic Student API checks |
| Linear equations | Variables on one or both sides, distribution, rational and decimal coefficients | Passed independent exact-arithmetic witnesses |
| Systems | Two equations, both variable orders, dependent and inconsistent equations | Passed; both relations retained |
| Inequalities | `<`, `>`, `<=`, `>=`, Unicode comparisons, negative multipliers, compound endpoints | Passed truth-value and endpoint checks |
| Polynomial expressions | Factoring, expansion, collection of like terms, implicit multiplication | Passed exact-arithmetic witnesses |
| Quadratics | Standard and factored forms, repeated roots, no real roots, degenerate leading coefficient | Passed; inappropriate explicit quadratic methods rejected |
| Functions and lines | Matching `f`/`g` definitions and evaluation; graph, zeros, vertex, slope, domain and range questions; standard-form and vertical lines | Passed; mismatched function labels rejected and vertical relations preserved |
| Powers and radicals | Parenthesis-sensitive powers, negative and zero exponents, square roots, `sqrt(x^2)` | Passed; original domain conditions retained |
| Bounds and malformed input | Oversized coefficients/exponents, zero denominators, deep/large ASTs, truncated syntax, extra expressions | Passed; no partial or previous-task fallback |
| Privacy and request integrity | Identity-wrapper invariance, exact canonical payload, malicious fields, no unrelated task reuse | Passed both prompt phases and mocked final transport |
| Photo and typed-math fidelity | Ambiguous OCR asterisks, explicit typed corrections, unsupported typed math with attached or retained photos | Passed; unsupported typed math cannot be replaced by a different photographed equation |

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
| ALG-09 | Live Grade 10 browser UAT at 12:44:17 p.m. EST on November 17 returned `unsupported_math` for “What is quadratic formula?” | Added closed public algebra concepts. Concept-only questions reach the appropriate topic without private text; failed formula parsing cannot discard an actual unsupported expression or unknown relationship. |
| ALG-10 | A natural answer attempt followed by `Can you check my method?` was rejected, although the answer alone worked. | Closed trailing checking phrases now preserve the original task; both provider phases still exclude the proposed answer. |
| ALG-11 | Live Grade 10 UAT at 1:29:33 p.m. EST on November 17 produced the valid intermediate equation `3y = 6 - 2x` for `Graph 2x + 3y = 6`, but independent review withheld it as `assignment_completion`. The generated response also asked the learner to repeat a step it had already shown. | Clarified the generation and review instructions: an equivalent intermediate equation may be shown without completing the graph, slope or intercepts; a shown step must lead to a new decision. Review remains mandatory, and a withholding verdict still prevents release without automatic retry. |
| ALG-12 | Subsequent live synthetic-photo UAT read Q4's printed `(x^2 - 9)/(x - 3)` as `(x*2 - 9)/(x - 3)`, silently changing the quadratic numerator into a linear one. The supplied synthetic worksheet was independently inspected and clearly contains `^2`. Review also found that unsupported explicit typed math could be replaced by a different photographed equation. | OCR-only asterisks now require local clarification instead of becoming multiplication. A complete explicitly typed problem overrides retained OCR, including a typed caret correction; deliberately typed multiplication remains supported. Unsupported explicit typed expressions stop locally without OCR or model calls, for both first uploads and retained photos. Generic requests such as “Help with question 4” still read the photo. The original photo remains in protected history. |

## Verification

From the repository root:

```sh
PYTHONPATH=apps/api .venv/bin/python -m pytest \
  apps/api/tests/test_algebra_concepts.py \
  apps/api/tests/test_algebra_independent_uat.py \
  apps/api/tests/test_homework_deidentification.py \
  apps/api/tests/test_homework_independent_uat.py -q
```

Result: **338 passed**. The suite uses original fixtures in `algebra_uat_cases.py`, evaluates the public AST independently with exact rational arithmetic, tests its domain behavior, and preserves elementary Homework Help regressions. It includes 76 concept checks, including a synthetic Student API request for “What is quadratic formula?” through both model phases. The only warning was the existing Starlette/AnyIO deprecation notice.

The transport checks replace DNS and HTTP with synthetic test doubles. They confirm that triage and safety receive the same closed algebra representation, no original private wrapper reaches the wire, storage remains disabled, and a private field inserted into the public AST is rejected before DNS. These checks do not assert that every possible model explanation will be correct; live student-flow acceptance and deployment verification are recorded separately.

The API regressions also passed. A synthetic local student submits the originally reported factoring question, then an explicit proposed answer. Both model phases retain the original polynomial; the answer expression remains only in encrypted Academy history. Replay returns the recorded response without repeating model calls. Unsupported trigonometry retains the original editable question and makes no model call.

For the concept request, triage and safety receive only the closed `quadratic_formula` topic. A private student-name prefix, account ID, session ID and school ID are absent from both prompts. The response is released through the real Student API path using mocked models; no live provider is called by the test.

The later graph-step and OCR follow-up adds independent checks in `test_algebra_prompt_boundaries.py` and `test_algebra_ocr_fidelity.py`. These verify algebraic equivalence of the observed intermediate step, mandatory approval even for that correct step, retention of final-answer withholding, unchanged replay without new sends, local rejection of ambiguous OCR, and successful typed correction without re-reading the retained photo. They also verify that unsupported explicit typed math never invokes OCR or a model in place of that request, including when a previously recognized photo is retained. Model-review decisions are mocked; the tests do not claim that a live model can never misclassify a response.

```sh
PYTHONPATH=apps/api .venv/bin/python -m pytest \
  apps/api/tests/test_algebra_prompt_boundaries.py \
  apps/api/tests/test_algebra_ocr_fidelity.py \
  apps/api/tests/test_homework_algebra_contract.py \
  apps/api/tests/test_homework_deidentified_egress.py -q
```

Graph/OCR follow-up result: **110 passed**, including the fifteen new independent cases.

## Deliberate limits

- The admitted variable alphabet is `x` and `y`; one-variable function bodies use `x`. Function labels are `f` or `g` and are removed from the canonical representation after checking that references match.
- Equation solving admits polynomials through degree two and two-equation linear systems. Factoring and expansion admit polynomials through degree four. Supported non-polynomial syntax includes real square roots, absolute values, rational expressions, and positive constant-base exponentials with a linear exponent.
- Standard-form linear graph and slope questions retain their equations, including vertical lines. General implicit nonlinear graphing is outside this parser's scope.
- Concept-only questions use a finite topic catalog, including explicitly supported substitution/elimination and slope/intercept comparisons. Actual formulas, unknown subject combinations, and private passages cannot be reduced to one familiar keyword.
- Literal powers are bounded integers with absolute value at most 10. Noninteger powers such as `x^(1/2)` require explicit supported square-root notation. Constant radicals outside the real domain, such as `sqrt(-1)`, ask for clarification.
- Each task has at most 64 AST nodes and depth 12. Constants have absolute value at most 10,000, and rational denominators are at most 10,000. Evaluation has additional magnitude and exponent limits; a large symbolic exponential remains symbolic rather than expanding into a huge number.
- Unknown clauses, incomplete expressions, ambiguous `x2` notation, arbitrary named variables, and unsupported multi-function requests ask for clarification as complete requests. They do not degrade to a smaller equation or inherit unrelated earlier work.
- An asterisk recognized from a worksheet image is treated as ambiguous because it may be a misread caret. Type the complete intended problem to resolve it; typed `*` and typed `^` retain their distinct meanings.
