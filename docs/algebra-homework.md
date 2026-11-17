# Algebra in Homework Help

Homework Help supports bounded high-school algebra questions without requiring a particular course enrollment or sending a learner's grade to a model. The feature supplies a justified next step, a checking method or a different worked example. It does not return an answer key, write submitted work or replace a teacher's assessment.

## Ask a complete question

You can ask about an idea without supplying an equation: `What is the quadratic formula?`, `How does factoring work?`, `Explain systems of equations`, or `How do exponent rules work?` These select public algebra concepts rather than forwarding the original wording.

- `Help me factor x^2 - 5x + 6 without giving me the answer.`
- `Solve 3(x - 2) = 2x + 5.`
- `Solve -2x + 3 > 7.`
- `Solve x + y = 5; x - y = 1 by elimination.`
- `Solve x^2 + x - 1 = 0 using the quadratic formula.`
- `Simplify (x^2 - 9)/(x - 3).`
- `Solve sqrt(x + 4) = x - 2.`
- `Given f(x) = x^2 - 3x + 2, find f(-2).`
- `Find the vertex of y = x^2 - 4x + 1.`
- `Graph 2x + 3y = 6.`

Use `^` for powers, `sqrt(...)` for square roots, parentheses to group terms, and one equation per line or a semicolon for a two-equation system. Clear Unicode powers such as `x²` are recognized; ambiguous `x2` is not guessed. `x` and `y` are supported variables; function definitions use `f(x)` or `g(x)`, with matching references.

Start a new problem with a verb such as **Solve**, **Factor**, **Simplify** or **Graph**. For an answer attempt, say **I got ...** or **My answer is ...**. When a bare expression could mean either, the assistant asks locally which you intended. It never changes the task silently and does not tell a provider what answer you tried.

## Mathematical coverage

The compiler retains signs, grouping, operator precedence, all relations in a system and inclusive/exclusive inequality endpoints. Supported families include linear and quadratic equations; linear inequalities and two-equation linear systems; polynomial factoring/expansion through degree four; rational, square-root and absolute-value expressions; bounded integer powers; positive constant-base exponentials with linear exponents; and one-variable function evaluation, graph, zero, vertex, slope, domain and range questions. Standard-form linear relations also support graph and slope guidance without converting a vertical line into a function. Signed, decimal and compound numeric expressions use the same bounded parser; simple whole-number arithmetic retains its existing simpler representation.

Guidance explicitly handles negative inequality scaling, zero coefficients, dependent/inconsistent systems, discriminants, denominator exclusions, square-root domains, zero/negative powers and extraneous roots introduced by transformations. An expression such as `sqrt(x^2)` must preserve the absolute-value condition; cancelling a factor must preserve its original excluded values. The model still needs an independent review before a response is released.

This is a tutoring feature, not a full published Grade 10 course, standards-alignment certification, worksheet generator or unrestricted symbolic calculator. The scope was reviewed against the [NYSED high-school mathematics standards](https://www.nysed.gov/sites/default/files/programs/standards-instruction/nys-next-generation-mathematics-p-12-standards.pdf); local course sequencing varies.

## Bounds and privacy

The exact finite bounds and independently checked fixtures are recorded in [the algebra UAT report](uat-algebra-2026-11-17.md). Unsupported variables, trigonometric/logarithmic syntax, arbitrary functions, complex-number tasks, ambiguous OCR and expressions beyond those bounds require clarification. Original question text, photographs, OCR, student attempts, identities, profiles and chat history stay in Academy. Only validated mathematical nodes, closed task/method selectors and code-derived domain guidance enter the existing generation and safety phases. There is no raw-input fallback, executable expression evaluation or new external tool access.

The existing school approval, child protections, encrypted audit, quotas, current-access checks and uncertain-send handling still apply. The code change does not alter model settings or enable the separate raw-content path.
