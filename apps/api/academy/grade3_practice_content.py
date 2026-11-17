"""Original, bounded question templates for the exact NY Grade 3 math package.

A human publishes a concrete generated snapshot. Generation is local and
seeded; grading uses the retained key rather than regenerating a question.
The number, choice and model-reading exercises do not assess open reasoning.
"""
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from random import Random
import re
from typing import Callable


BANK_VERSION = "ny-grade3-math-practice-2026-27-v2"
MAX_QUESTIONS = 20
_DENOMINATORS = (2, 3, 4, 6, 8)
_STANDARDS_VERSION = "nys-next-generation-mathematics-2017-updated-2019"
_PRACTICES = (
    ("MP.1", "Choose a first step. Check whether your result makes sense for the problem."),
    ("MP.2", "Explain what the numbers in your model stand for."),
    ("MP.3", "Explain why your choice works. What makes another choice not work?"),
    ("MP.4", "Make a sketch or equation that matches the situation."),
    ("MP.5", "What tool or drawing would help you check your answer?"),
    ("MP.6", "Label the quantities and their units when you check your work."),
    ("MP.7", "Look for a useful group, part, or place-value pattern."),
    ("MP.8", "Try a new example. Which part of your reasoning still works?"),
)


def grade3_practice_course(course):
    """Recognize the exact stored state alignment, not a title or catalog code."""
    value = getattr(course, "value", None)
    curriculum = value.get("curriculum") if isinstance(value, dict) else None
    return (
        isinstance(curriculum, dict)
        and curriculum.get("mode", "state") == "state"
        and curriculum.get("state") == "US-NY"
        and curriculum.get("subject") == "mathematics"
        and curriculum.get("grades") == [3]
        and type(curriculum["grades"][0]) is int
        and curriculum.get("academic_year") == "2026-2027"
        and curriculum.get("standards_version") in (None, _STANDARDS_VERSION)
    )


def _whole(prompt, answer, *, figure=None):
    return _text(prompt, "whole", int(answer), figure=figure)


def _text(prompt, kind, value, *, figure=None):
    result = {"prompt": prompt, "answer_type": "text", "options": [],
              "answer_key": {"kind": kind, "value": value}}
    if figure is not None:
        result["figure"] = figure
    return result


def _fraction(prompt, numerator, denominator, *, parts=False, figure=None):
    # A part count question can require a named denominator. A fraction
    # location or magnitude question accepts mathematically equivalent forms.
    return _text(prompt, "fraction-parts" if parts else "fraction", [numerator, denominator], figure=figure)


def _choice(rng, prompt, answer, others, *, figure=None):
    labels = list(dict.fromkeys([str(answer), *(str(x) for x in others)]))
    if not 2 <= len(labels) <= 5:
        raise ValueError("A practice choice needs two to five distinct labels")
    rng.shuffle(labels)
    ids = "abcde"
    result = {"prompt": prompt, "answer_type": "choice",
              "options": [{"id": ids[i], "label": value} for i, value in enumerate(labels)],
              "answer_key": {"kind": "choice", "value": ids[labels.index(str(answer))]}}
    if figure is not None:
        result["figure"] = figure
    return result


def _mode(rng, variant, count):
    return rng.randrange(count) if variant is None else variant


def _distinct_factors(rng):
    return rng.sample(range(2, 10), 2)


def _equal_groups(rng, variant=None):
    a, b = _distinct_factors(rng)
    group, item = rng.choice((("bags", "marbles"), ("trays", "muffins"), ("baskets", "apples"), ("boxes", "crayons")))
    story = f"There are {a} {group} with {b} {item} in each."
    if _mode(rng, variant, 2):
        return _choice(rng, f"{story} Which expression counts all the {item}?",
                       f"{a} × {b}", [f"{a} + {b}", f"{a} × {b + 1}", f"{a - 1} × {b}"])
    return _choice(rng, f"{story} In {a} × {b}, what does the {a} stand for?",
                   f"the number of {group}", [f"the number of {item} in one group",
                   f"the total number of {item}", "the number left over"])


def _division_meanings(rng, variant=None):
    groups, each = _distinct_factors(rng)
    objects = rng.choice(("stickers", "beads", "shells", "blocks"))
    if _mode(rng, variant, 2):
        story = f"Share {groups * each} {objects} equally among {groups} children. In {groups * each} ÷ {groups}, what does the answer count?"
        answer, other = "objects for each child", "children receiving objects"
    else:
        story = f"Put {groups * each} {objects} into bags with {each} in each bag. In {groups * each} ÷ {each}, what does the answer count?"
        answer, other = "bags that can be filled", "objects in each bag"
    return _choice(rng, story, answer, [other, "objects before making groups", "objects left over"])


def _multiplication_situations(rng, variant=None):
    a, b = _distinct_factors(rng)
    mode = _mode(rng, variant, 4)
    if mode == 0:
        return _whole(f"A garden has {a} rows of {b} plants. How many plants are there?", a * b)
    if mode == 1:
        return _whole(f"A {a * b}-meter ribbon is cut into pieces of {b} meters. How many pieces are there?", a)
    if mode == 2:
        return _whole(f"{a * b} pencils are shared equally among {a} tables. How many does each table get?", b)
    return _choice(rng, f"A trail has {a} sections. Each is {b} meters long. Which expression gives the trail length?",
                   f"{a} × {b}", [f"{a} + {b}", f"{a * b} ÷ {a}", f"{a * b} + {b}"])


def _unknown_numbers(rng, variant=None):
    a, b = _distinct_factors(rng)
    equation, answer = ((f"n × {b} = {a * b}", a), (f"{a} × n = {a * b}", b),
                                   (f"{a} × {b} = n", a * b), (f"n ÷ {a} = {b}", a * b),
                                   (f"{a * b} ÷ n = {b}", a), (f"{a * b} ÷ {a} = n", b))[_mode(rng, variant, 6)]
    return _whole(f"What whole number makes {equation} true?", answer)


def _operation_properties(rng, variant=None):
    a, b = _distinct_factors(rng)
    c = rng.randint(2, b)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _choice(rng, f"Which expression has the same value as {a} × {b}?",
                       f"{b} × {a}", [f"{b} + {a}", f"{b} × {a + 1}", f"{b + 1} × {a}"])
    if mode == 1:
        return _choice(rng, f"Which expression regroups ({a} × {b}) × {c} without changing any factor?",
                       f"{a} × ({b} × {c})", [f"{a} × ({b} + {c})", f"{a} + ({b} × {c})", f"({a} + {b}) × {c}"])
    part = rng.randint(1, b - 1)
    rest = b - part
    return _choice(rng, f"Think of {b} as {part} + {rest}. Which expression splits {a} × {b} into two smaller products?",
                   f"({a} × {part}) + ({a} × {rest})",
                   [f"({a} × {part}) + {rest}", f"({a} + {part}) × {rest}", f"({a} × {part}) × ({a} × {rest})"])


def _division_inverse(rng, variant=None):
    a, b = _distinct_factors(rng)
    if _mode(rng, variant, 2):
        return _whole(f"Think of {a * b} ÷ {a} as {a} × n = {a * b}. What is n?", b)
    return _choice(rng, f"Which equation can help find {a * b} ÷ {a} using a missing factor?",
                   f"{a} × n = {a * b}", [f"{a} + n = {a * b + b}", f"{a * b} × n = {a}", f"n × {a} = {a * b + a}"])


def _fluency_strategies(rng, variant=None):
    a, b = _distinct_factors(rng)
    if _mode(rng, variant, 2):
        return _whole(f"Use a related multiplication fact to find {a * b} ÷ {b}. What is the quotient?", a)
    part = rng.randint(1, b - 1)
    return _whole(f"You know {part} × {a} = {a * part}. Add {b - part} more groups of {a}. What is {b} × {a}?", a * b)


def _multiply_facts(rng, variant=None):
    a, b = rng.randint(0, 9), rng.randint(0, 9)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _whole(f"What is {a} × {b}?", a * b)
    if mode == 1:
        return _choice(rng, f"Choose the product of {a} and {b}.", a * b,
                       [a * b + 1, a * b + 2, max(0, a * b - 1)])
    a, b = max(1, a), max(1, b)
    return _whole(f"An array has {a} {'row' if a == 1 else 'rows'} with {b} {'dot' if b == 1 else 'dots'} in each. How many dots are there altogether?",
                  a * b, figure="\n".join(" ".join(["●"] * b) for _ in range(a)))


def _two_steps(rng, *, model=False, variant=None):
    a, b = _distinct_factors(rng)
    c = rng.randint(2, 9)
    mode = _mode(rng, variant, 4)
    if mode == 0:
        story = f"Each of {a} trays has {b} rolls. The baker sells {c} rolls." if c < a * b else f"Each of {a} trays has {b} rolls. The baker sells {a} rolls."
        sold = c if c < a * b else a
        question = "How many rolls are left?"
        equation = f"n = ({a} × {b}) − {sold}"
        wrong = [f"n = ({a} × {b}) + {sold}", f"n = ({a} + {b}) − {sold}", f"n = {a} × ({b} + {sold})"]
        answer = a * b - sold
    elif mode == 1:
        e = rng.randint(1, 6)
        story = f"There are {a * b} crayons in one bin and {a * e} in another. All the crayons are shared equally among {a} tables."
        question = "How many crayons go to each table?"
        equation = f"n = ({a * b} + {a * e}) ÷ {a}"
        wrong = [f"n = ({a * b} + {a * e}) × {a}", f"n = ({a * b} − {a * e}) ÷ {a}", f"n = {a * b} + ({a * e} ÷ {a})"]
        answer = b + e
    elif mode == 2:
        story = f"A team has {a * b} beads. The beads fill {a} bags equally. The team adds {c} more beads to one of the bags."
        question = "How many beads are now in that bag?"
        equation = f"n = ({a * b} ÷ {a}) + {c}"
        wrong = [f"n = ({a * b} ÷ {a}) − {c}", f"n = ({a * b} × {a}) + {c}", f"n = ({a * b} + {c}) ÷ {a}"]
        answer = b + c
    else:
        story = f"A club has {a * b + a * c} game cards. It gives away {a * c} cards, then shares the rest equally among {a} groups."
        question = "How many cards go to each group?"
        equation = f"n = ({a * b + a * c} − {a * c}) ÷ {a}"
        wrong = [f"n = ({a * b + a * c} + {a * c}) ÷ {a}", f"n = ({a * b + a * c} − {a * c}) × {a}",
                 f"n = {a * b + a * c} − ({a * c} ÷ {a})"]
        answer = b
    if model:
        return _choice(rng, f"{story} {question} Let n be the answer. Which equation matches the steps?", equation, wrong)
    return _whole(f"{story} {question}", answer)


def _equation_model(rng, variant=None):
    return _two_steps(rng, model=True, variant=variant)


def _round(number, unit):
    return ((number + unit // 2) // unit) * unit


def _reasonableness(rng, variant=None):
    p, q, u = rng.sample(range(2, 7), 3)
    n, m, k = (10 * x + rng.choice((-2, -1, 1, 2)) for x in (p, q, u))
    rounded_total = (p + q - u) * 10
    if rounded_total < 10 or n + m <= k:
        n += 20
        rounded_total += 20
    if _mode(rng, variant, 2):
        return _whole(f"The library has {n} books on one cart and {m} on another. It puts away {k}. Round each number to the nearest ten first. About how many books are left on the carts?", rounded_total)
    return _choice(rng, f"Estimate {n} + {m} − {k} by rounding each number to the nearest ten first. Which estimate helps you check your answer?",
                   rounded_total, rng.sample([n for n in range(max(0, rounded_total - 30), rounded_total + 40, 10) if n != rounded_total], 3))


def _arithmetic_patterns(rng, variant=None):
    a, b = _distinct_factors(rng)
    if _mode(rng, variant, 2):
        seq = ", ".join(str(a * k) for k in range(b - 1, b + 2))
        return _whole(f"Keep adding the same amount: {seq}, __. What is the next number?", a * (b + 2))
    return _choice(rng, f"In the row for multiplying by {a}, the product goes from {a} × {b - 1} to {a} × {b}. How does the product change?",
                   f"it increases by {a}", [f"it increases by {b}", f"it increases by {a + b}", f"it decreases by {a}"])


def _rounding(rng, variant=None):
    unit = rng.choice((10, 100))
    number = rng.randint(11, 999)
    target = "ten" if unit == 10 else "hundred"
    rounded = _round(number, unit)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _whole(f"Round {number:,} to the nearest {target}. If it is exactly halfway, round up.", rounded)
    lower = number // unit * unit
    upper = lower + unit
    if mode == 1:
        return _choice(rng, f"Which estimate is {number:,} rounded to the nearest {target}? Round halfway values up.",
                       rounded, [max(0, rounded - unit), rounded + unit, rounded + 2 * unit])
    return _whole(f"A number line shows {lower:,} and {upper:,}, the neighboring multiples of {unit}. Which endpoint should {number:,} round to? Use the greater endpoint when halfway.", rounded)


def _add_subtract(rng, variant=None):
    a, b = rng.randint(110, 499), rng.randint(100, 499)
    mode = _mode(rng, variant, 3)
    if mode != 2:
        if mode == 0:
            return _whole(f"Find {a} + {b}.", a + b)
        return _choice(rng, f"Which expression breaks {b} into hundreds and the rest to find {a} + {b}?",
                       f"{a} + {b // 100 * 100} + {b % 100}",
                       [f"{a} + {b // 100} + {b % 100}", f"{a} + {b // 100 * 100 + 100} + {b % 100}",
                        f"{a} + {b // 100 * 100} + {b % 100 + 10}"])
    return _whole(f"Find {a + b} − {b}.", a)


def _tens_products(rng, variant=None):
    a, b = rng.randint(1, 9), rng.randint(1, 9)
    if _mode(rng, variant, 2):
        return _whole(f"What is {a} × {b * 10}?", a * b * 10)
    return _whole(f"There are {a} groups with {b} tens in each. What number do all those tens make?", a * b * 10)


def _place_value(rng, variant=None):
    n = rng.randint(1000, 9999)
    places = (("thousands", 1000), ("hundreds", 100), ("tens", 10), ("ones", 1))
    name, value = rng.choice(places)
    if _mode(rng, variant, 2):
        return _whole(f"In {n:,}, what is the value of the digit in the {name} place?", (n // value % 10) * value)
    # Explicitly ask for bundles while allowing smaller units left over.
    return _whole(f"How many full hundreds can you make with {n:,} ones?", n // 100)


_SMALL = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("zero ten twenty thirty forty fifty sixty seventy eighty ninety").split()


def _number_words(n):
    if n >= 1000:
        return _number_words(n // 1000) + " thousand" + (" " + _number_words(n % 1000) if n % 1000 else "")
    if n >= 100:
        return _number_words(n // 100) + " hundred" + (" " + _number_words(n % 100) if n % 100 else "")
    if n >= 20:
        return _TENS[n // 10] + ("-" + _SMALL[n % 10] if n % 10 else "")
    return _SMALL[n]


def _expanded(n):
    return " + ".join(f"{n // unit % 10 * unit:,}" for unit in (1000, 100, 10, 1) if n // unit % 10)


def _number_forms(rng, variant=None):
    n = rng.randint(1000, 9898)
    others = [n + 1, n + 10, n + 100]
    mode = _mode(rng, variant, 4)
    if mode == 0:
        return _whole(f"Write this number with digits: {_number_words(n)}.", n)
    if mode == 1:
        return _whole(f"Write {_expanded(n)} as one number.", n)
    if mode == 2:
        return _choice(rng, f"Which is {n:,} in expanded form?", _expanded(n), [_expanded(x) for x in others])
    return _choice(rng, f"Which words name {n:,}?", _number_words(n), [_number_words(x) for x in others])


def _strip(n, d):
    return " | ".join("X" if i < n else "·" for i in range(d))


def _fraction_parts(rng, variant=None):
    d = rng.choice(_DENOMINATORS)
    n = rng.randint(1, d)
    model = rng.choice(("paper strip", "ribbon", "rectangle", "length of tape"))
    if _mode(rng, variant, 2):
        return _fraction(f"A {model} has {d} equal parts. {n} {'part is' if n == 1 else 'parts are'} shaded. Write the shaded fraction with {d} as the bottom number.", n, d, parts=True, figure=_strip(n, d))
    return _whole(f"A {model} has {d} equal parts. Each is 1/{d} of the whole. How many of those parts make {n}/{d}?", n)


def _line(d, count, target):
    labels = ["0" if i == 0 else "1" if i == d else "2" if i == 2 * d else " " for i in range(count + 1)]
    marks = ["^" if i == target else "|" for i in range(count + 1)]
    return "---".join(marks) + "\n" + "   ".join(labels)


def _fraction_line(rng, variant=None):
    d = rng.choice(_DENOMINATORS)
    n = rng.choice([x for x in range(1, 2 * d) if x != d])
    figure = _line(d, 2 * d, n)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _fraction(f"This number line starts at 0. Each whole has {d} equal intervals. The arrow is {n} {'interval' if n == 1 else 'intervals'} from 0. What fraction of a whole is its distance from 0?", n, d, figure=figure)
    if mode == 1:
        return _whole(f"Each interval on this number line is 1/{d}. How many of these intervals take you from 0 to the arrow at {n}/{d}?", n, figure=figure)
    return _choice(rng, f"Each whole on this number line has {d} equal intervals. Which fraction names the arrow {n} {'interval' if n == 1 else 'intervals'} after 0?",
                   f"{n}/{d}", [f"{n + 1}/{d}", f"{n + 2}/{d}", f"{n + 3}/{d}"], figure=figure)


def _unit_fraction_line(rng, variant=None):
    d = rng.choice(_DENOMINATORS)
    figure = _line(d, d, 1)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _fraction(f"On this {rng.choice(('number line', 'measuring scale', 'marked path'))}, 0 to 1 is split into {d} equal intervals. The arrow marks the first endpoint after 0. Name that point with {d} as the bottom number.", 1, d, parts=True, figure=figure)
    if mode == 1:
        return _whole(f"A {rng.choice(('frog', 'robot', 'marker', 'game piece'))} moves along a number line in equal jumps. Its first jump ends at 1/{d}. How many such jumps make one whole?", d)
    return _choice(rng, f"A {rng.choice(('number line', 'measuring strip', 'ruler'))} from 0 to 1 has {d} equal intervals. Which fraction is the length of just one interval?",
                   f"1/{d}", [f"{d}/1", f"{d}/{d}", f"{d + 1}/{d}"], figure=figure)


def _iterate_fraction_line(rng, variant=None):
    d = rng.choice(_DENOMINATORS)
    n = rng.randint(d + 1, 2 * d)
    model = rng.choice(("ribbon length", "number line", "measuring scale"))
    if _mode(rng, variant, 2):
        return _fraction(f"On a {model}, make {n} jumps of 1/{d} starting from 0. At what fraction do you land? Keep {d} as the bottom number.", n, d, parts=True, figure=_line(d, 2 * d, n))
    return _whole(f"On a {model}, a jump is 1/{d} of a whole. How many jumps of this size take you from 0 to {n}/{d}?", n)


def _same_whole(rng, variant=None):
    d = rng.choice(_DENOMINATORS)
    a, b = rng.randint(1, d), rng.randint(1, d)
    mode = _mode(rng, variant, 3)
    figure = f"First:  {_strip(a, d)}\nSecond: {_strip(b, d)}"
    if mode == 0:
        answer = "The shaded amounts are equal." if a == b else "The first has more shaded." if a > b else "The second has more shaded."
        return _choice(rng, f"Two paper strips are the same size. The first has {a}/{d} shaded and the second has {b}/{d} shaded. What can you say about the amounts?",
                       answer, ["The shaded amounts are equal.", "The first has more shaded.", "The second has more shaded."], figure=figure)
    if mode == 1:
        return _whole(f"Two equal-size strips have {a}/{d} and {b}/{d} shaded. How many 1/{d} parts are shaded on the strip with more shading?", max(a, b), figure=figure)
    symbol = "=" if a == b else ">" if a > b else "<"
    return _choice(rng, f"Which comparison is true for {a}/{d} and {b}/{d} of the same whole?",
                   f"{a}/{d} {symbol} {b}/{d}",
                   [f"{a}/{d} {sign} {b}/{d}" for sign in ("<", "=", ">")], figure=figure)


_EQUIVALENT = ((1, 2, 2), (1, 2, 3), (1, 2, 4), (1, 3, 2), (2, 3, 2), (1, 4, 2), (3, 4, 2), (2, 4, 2))


def _paired_strips(n, d, factor):
    """Show the same-length whole with two sets of equally spaced cuts."""
    denominator = d * factor
    width = 2 * denominator

    def row(numerator, parts):
        step = width // parts
        return "".join("|" if i % step == 0 else "X" if i < numerator * step else "·" for i in range(width + 1))

    return f"{d} parts: {row(n, d)}\n{denominator} parts: {row(n * factor, denominator)}"


def _equivalent_location(rng, variant=None):
    n, d, factor = rng.choice(_EQUIVALENT)
    denom, numerator = d * factor, n * factor
    figure = _paired_strips(n, d, factor)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _choice(rng, f"Which fraction with {denom} equal parts names the same location as {n}/{d}?",
                       f"{numerator}/{denom}", [f"{n}/{denom}", f"{numerator + 1}/{denom}", f"{denom}/{denom}"], figure=figure)
    if mode == 1:
        return _whole(f"These strips show the same shaded length. The top strip shades {n} of {d} parts. How many of the {denom} bottom parts are shaded?", numerator, figure=figure)
    return _fraction(f"The same point is named {numerator}/{denom} on one number line. Name it using {d} equal parts per whole. Keep {d} as the bottom number.", n, d, parts=True, figure=figure)


def _build_equivalent(rng, variant=None):
    n, d, factor = rng.choice(_EQUIVALENT)
    item = rng.choice(("paper strip", "rectangle", "ribbon", "length of tape"))
    denom = d * factor
    if _mode(rng, variant, 2):
        return _fraction(f"A {item} is shaded {n}/{d}. Split each part into {factor} equal pieces without changing the shaded amount. Write the fraction using {denom} as the bottom number.", n * factor, denom, parts=True,
                         figure=f"Before splitting: {_strip(n, d)}")
    return _choice(rng, f"On a {item}, {n}/{d} and {n * factor}/{denom} shade the same amount. What explains this?",
                   f"Each old part is split into {factor} equal pieces. The shaded length stays the same.",
                   ["The shaded length gets longer when there are more pieces.",
                    "The pieces are larger when the bottom number is larger.",
                    "The whole changes when you rename the fraction."],
                   figure=_paired_strips(n, d, factor))


def _whole_fractions(rng, variant=None):
    d = rng.choice((1, *_DENOMINATORS))
    n = rng.randint(1, 6)
    if _mode(rng, variant, 2):
        return _whole(f"Each whole has {d} equal {'part' if d == 1 else 'parts'}. How many whole units is {n * d}/{d}?", n)
    return _fraction(f"Write {n} as a fraction with {d} as the bottom number.", n * d, d, parts=True)


def _compare_fractions(rng, variant=None):
    mode = _mode(rng, variant, 3)
    if rng.randrange(2):
        d1 = d2 = rng.choice(_DENOMINATORS)
        n1, n2 = (rng.randint(1, d1), rng.randint(1, d1)) if mode == 0 else rng.sample(range(1, d1 + 1), 2)
    else:
        d1, d2 = rng.sample(_DENOMINATORS, 2)
        n1 = n2 = rng.randint(1, min(d1, d2))
    a, b = n1 * d2, n2 * d1
    if mode == 0:
        return _choice(rng, f"The fractions refer to the same whole. Which symbol makes this true? {n1}/{d1} __ {n2}/{d2}",
                       "<" if a < b else ">" if a > b else "=", ["<", "=", ">"])
    if mode == 1:
        return _fraction(f"Two same-size ribbons have {n1}/{d1} and {n2}/{d2} colored. Write the greater fraction.", *( (n1, d1) if a > b else (n2, d2)))
    return _fraction(f"Compare {n1}/{d1} and {n2}/{d2} of the same whole. Write the smaller fraction.", *((n1, d1) if a < b else (n2, d2)))


def _clock(minutes):
    hour, minute = divmod(minutes, 60)
    return f"{hour % 12 or 12}:{minute:02}"


def _time_minutes(rng, variant=None):
    h = rng.randint(1, 11)
    m = rng.randint(1, 59)
    minutes = rng.randint(3, 59)
    start, end = 60 * h + m, 60 * h + m + minutes
    mode = _mode(rng, variant, 4)
    if mode == 0:
        return _whole(f"Practice starts at {_clock(start)} and ends at {_clock(end)} the same morning. How many minutes does it last?" if end < 720 else f"A short practice starts at {_clock(start)} and ends at {_clock(end)} less than one hour later. How many minutes does it last?", minutes)
    if mode == 1:
        return _text(f"A walk starts at {_clock(start)} and lasts {minutes} minutes. What time does it end? Write hour:minute.", "clock", _clock(end))
    if mode == 2:
        return _text(f"A game ends at {_clock(end)}. It lasted {minutes} minutes. What time did it start? Write hour:minute.", "clock", _clock(start))
    marker, ticks = divmod(m, 5)
    marker = marker or 12
    place = f"exactly on the {marker}" if not ticks else f"{ticks} small minute {'tick' if ticks == 1 else 'ticks'} clockwise past the {marker}"
    return _text(f"The short hand of a clock is between {h} and {h + 1}. The long hand is {place}. What time is it? Write hour:minute.", "clock", _clock(start))


def _metric_estimates(rng, variant=None):
    if _mode(rng, variant, 2) == 0:
        step, marks = rng.choice((1, 2, 5)), rng.randint(2, 6)
        unit = rng.choice(("kilograms", "liters"))
        instrument = "scale" if unit == "kilograms" else "measuring jug"
        return _whole(f"A {instrument} starts at 0 and each numbered step is {step} {unit}. Read the mark {marks} steps after 0. What is the measurement in {unit}?", step * marks,
                      figure="  |  ".join(str(step * i) for i in range(marks + 1)) + f" {unit}")
    name, amount, correct = rng.choice((
        ("an apple", rng.randint(100, 220), "grams"), ("a paper clip", rng.randint(1, 3), "grams"),
        ("a full school backpack", rng.randint(3, 7), "kilograms"), ("an adult bicycle", rng.randint(10, 16), "kilograms"),
        ("a small bucket of water", rng.randint(4, 8), "liters"), ("a large jug of water", rng.randint(2, 4), "liters"),
    ))
    quantity = "holds about" if correct == "liters" else "has a mass of about"
    return _choice(rng, f"Choose a sensible unit: {name.capitalize()} {quantity} {amount} __.",
                   correct, ["grams", "kilograms", "liters"])


def _same_unit_measure(rng, variant=None):
    thing, unit = rng.choice((("water", "liters"), ("rice", "grams"), ("dog food", "kilograms")))
    if unit == "grams":
        a, b = rng.randint(120, 460), rng.randint(10, 110)
    else:
        a, b = rng.randint(5, 12), rng.randint(1, 4)
    mode = _mode(rng, variant, 4)
    if mode == 0:
        return _whole(f"A container has {a} {unit} of {thing}. Add {b} more {unit}. How many {unit} are there now?", a + b)
    if mode == 1:
        return _whole(f"A container has {a} {unit} of {thing}. Remove {b} {unit}. How many {unit} are left?", a - b)
    groups = rng.randint(2, 8)
    if mode == 2:
        return _whole(f"Each of {groups} containers holds {b} {unit} of {thing}. How many {unit} are in all the containers?", groups * b)
    return _whole(f"Split {groups * b} {unit} of {thing} equally among {groups} containers. How many {unit} go in each?", b)


def _scaled_graph(rng, variant=None):
    names = rng.choice((("Cats", "Dogs", "Fish"), ("Red", "Blue", "Green"), ("Oaks", "Pines", "Maples")))
    scale = rng.choice((2, 3, 4, 5, 10))
    a, b, c = rng.sample(range(2, 9), 3)
    counts = [a, b, c]
    amounts = [x * scale for x in counts]
    marker = rng.choice(("■", "●"))
    figure = "\n".join([f"{marker} = {scale} items", *[f"{label}: {marker * n}" for label, n in zip(names, counts)]])
    mode = _mode(rng, variant, 3)
    if mode == 0:
        data = "\n".join(f"{label}: {value}" for label, value in zip(names, amounts))
        return _text(f"Make a scaled graph for these counts: {', '.join(f'{label} {value}' for label, value in zip(names, amounts))}. Each mark stands for {scale} items. Type how many marks to draw for {', '.join(names)}, in that order, separated by commas.", "counts", counts, figure=data)
    if mode == 1:
        return _whole(f"Each mark stands for {scale} items. The graph has {a} marks for {names[0]} and {b} for {names[1]}. How many more items does the larger group have?", abs(a - b) * scale, figure=figure)
    hi = counts.index(max(counts))
    remaining = [i for i in range(3) if i != hi]
    total_marks = sum(counts[i] for i in remaining)
    if total_marks == counts[hi]:
        return _whole(f"Each mark stands for {scale} items. There are {a}, {b}, and {c} marks for {', '.join(names)} in that order. How many items are there altogether?", sum(counts) * scale, figure=figure)
    return _whole(f"Each mark stands for {scale} items. There are {a}, {b}, and {c} marks for {', '.join(names)} in that order. Compare {names[remaining[0]]} and {names[remaining[1]]} together with {names[hi]}. How many items is the difference?", abs(total_marks - counts[hi]) * scale, figure=figure)


def _inch_mark(quarter_ticks):
    whole, part = divmod(quarter_ticks, 4)
    fraction = ("", "1/4", "1/2", "3/4")[part]
    return f"{whole} {fraction}" if whole and part else fraction or str(whole)


def _line_plot(counts, marks):
    width = max(len(mark) for mark in marks) + 2
    rows = ["".join(("X" if value >= height else " ").center(width) for value in counts).rstrip() for height in range(max(counts), 0, -1)]
    return "\n".join([*rows, "".join(mark.center(width) for mark in marks).rstrip(), "Length in inches"])


def _fraction_measurement(rng, variant=None):
    start = rng.randint(0, 10)
    quarter_step = rng.choice((1, 2))
    positions = [start + i * quarter_step for i in range(4)]
    labels = [_inch_mark(i) for i in positions]
    mode = _mode(rng, variant, 3)
    if mode == 0:
        # A ruler read, beginning at zero, with the selected tick offset by a
        # known number of whole inches plus a fractional part.
        n, part = rng.randint(1, 8), rng.randint(1, 3)
        marks = [_inch_mark(4 * n + i) for i in range(4)]
        return _choice(rng, f"A paper strip starts at 0 on a ruler. Its end is {part} {'tick' if part == 1 else 'ticks'} after the {n}-inch mark. There are four equal spaces in each inch. What is the length in inches?",
                       marks[part], marks, figure="  |  ".join(marks))
    counts = [rng.randint(0, 3) for _ in positions]
    if not any(counts):
        counts[0] = 2
    if mode == 1:
        measures = [label for label, count in zip(labels, counts) for _ in range(count)]
        rng.shuffle(measures)
        return _text(f"Lengths in inches: {'; '.join(measures)}. Make a line plot with marks at {'; '.join(labels)} inches. Enter the number of X marks at each length in that order, separated by commas.", "counts", counts)
    target = rng.randrange(4)
    return _whole(f"On this line plot, one X means one object. The counts from left to right are {', '.join(map(str, counts))}. The labels are {'; '.join(labels)} inches. How many objects are {_inch_mark(positions[target])} inches long?",
                  counts[target], figure=_line_plot(counts, labels))


def _tiles(widths, second=0):
    return "\n".join(" ".join(["■"] * (n - second) + ["□"] * second) for n in widths)


def _area_meaning(rng, variant=None):
    a, b = _distinct_factors(rng)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _choice(rng, f"A rectangular mat is {a} units long and {b} units wide. What does its area measure?",
                       "how much flat surface it covers, in square units",
                       ["the length of its boundary, in units", "the number of its corners", "how high it stands above the ground"])
    if mode == 1:
        return _choice(rng, f"A class is working on a {a}-by-{b} floor plan. Which job requires measuring area?", "Finding how many square tiles cover a floor.",
                       ["Finding the length of ribbon around a poster.", "Finding the time a game lasts.", "Finding the mass of a backpack."])
    return _choice(rng, f"A board is covered by {a * b} unit squares with no gaps or overlaps. Which statement describes its area?",
                   f"The board covers {a * b} square units.",
                   [f"The board's boundary is {a * b} units long.", f"The board is {a * b} units tall.", f"The board has {a * b} corners."])


def _unit_square(rng, variant=None):
    unit, plural = rng.choice((("inch", "inches"), ("centimeter", "centimeters"), ("foot", "feet"), ("meter", "meters")))
    count = rng.randint(2, 30)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _choice(rng, f"A {rng.choice(('tile', 'paper square', 'grid square'))} has sides 1 {unit} long. How much area does it measure?",
                       f"1 square {unit}", [f"4 square {plural}", f"2 square {plural}", f"1 {unit} of boundary"])
    if mode == 1:
        return _whole(f"A square measuring 1 {unit} on each side is one square {unit}. How many of these unit squares cover a {rng.choice(('board', 'mat'))} with area {count} square {plural}?", count)
    return _choice(rng, f"You measure a {rng.choice(('board', 'floor', 'poster', 'tabletop'))} using 1-square-{unit} units. Which tile can be used?", f"A square 1 {unit} long and 1 {unit} wide.",
                   [f"A square 2 {plural} long and 2 {plural} wide.", f"A rectangle 1 {unit} long and 2 {plural} wide.", f"A line 1 {unit} long."])


def _covering_area(rng, variant=None):
    a, b = _distinct_factors(rng)
    n = a * b
    obj = rng.choice(("board", "mat", "poster", "tabletop"))
    if _mode(rng, variant, 2):
        return _whole(f"A {obj} is covered by {n} unit squares with no gaps or overlaps. What is its area in square units?", n)
    return _choice(rng, f"You want to measure a {obj} using {n} equal square tiles. Which way of covering the whole {obj} will let you count its area?",
                   "Cover it with no gaps and no overlaps.",
                   ["Leave gaps but do not overlap.", "Overlap tiles to close any gaps.", "Put tiles only along the edges."])


def _count_tiles(rng, variant=None):
    h, w = rng.randint(2, 5), rng.randint(2, 8)
    rows = [w] * h
    rows[0] -= 1
    rows[-1] -= rng.randint(1, w - 1)
    total = sum(rows)
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _whole(f"The rows have {'; '.join(str(n) for n in rows)} tiles. Each tile is one square unit. Find the area in square units.", total, figure=_tiles(rows))
    if mode == 1:
        return _choice(rng, "Count the unit squares in this shape. Which number is its area in square units?",
                       total, [total + 1, total + 2, total - 1], figure=_tiles(rows))
    return _whole(f"This shape covers {total} unit squares. Add a new row of {w} unit squares without overlap. What area will the enlarged shape cover?", total + w, figure=_tiles(rows))


def _rectangle_area(rng, variant=None):
    h, w = _distinct_factors(rng)
    if _mode(rng, variant, 2):
        # A few dimension pairs have equal numerical area and perimeter.
        # Avoid accepting a second true value in those cases.
        perimeter_expression = f"2 × ({h} + {w})" if 2 * (h + w) != h * w else f"2 × ({h} + {w - 1})"
        return _choice(rng, f"A tiled rectangle has {h} rows, each with {w} square tiles. Which calculation finds its area?",
                       f"{h} × {w}", [f"{h} + {w}", perimeter_expression, f"({h} + 1) × {w}"],
                       figure=_tiles([w] * h))
    return _whole(f"A tiled rectangle has {h} rows of {w} unit squares. How many square units does it cover?", h * w, figure=_tiles([w] * h))


def _area_applications(rng, variant=None):
    h, w = _distinct_factors(rng)
    item, unit = rng.choice((("rug", "feet"), ("garden", "meters"), ("paper card", "centimeters"), ("picture mat", "inches")))
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _whole(f"A rectangular {item} is {h} {unit} wide and {w} {unit} long. What is its area in square {unit}?", h * w)
    if mode == 1:
        return _whole(f"A rectangular {item} has area {h * w} square {unit}. It is {w} {unit} long. How wide is it in {unit}?", h)
    return _choice(rng, f"Which calculation finds the area of a rectangular {item} that is {h} {unit} by {w} {unit}?",
                   f"{h} × {w}", [f"{h} + {w}", f"{h} × {w + 1}", f"{h + 1} × {w}"])


def _distributive_area(rng, variant=None):
    h, a, b = rng.sample(range(2, 8), 3)
    # Two side-by-side rectangles of the same height. The drawings and
    # words describe the same geometry; the symbols never carry color only.
    figure = _tiles([a + b] * h, second=b)
    if _mode(rng, variant, 2):
        return _choice(rng, f"Split a tiled rectangle of height {h} into sections of width {a} and {b}. Which sum gives the area of the whole rectangle?",
                       f"({h} × {a}) + ({h} × {b})",
                       [f"({h} × {a}) + {b}", f"{h} + {a} + {b}", f"({h} × {a}) × ({h} × {b})"], figure=figure)
    return _whole(f"Split a rectangle of height {h} into widths {a} and {b}. How many square units do both parts cover together?", h * (a + b), figure=figure)


def _compound_area(rng, variant=None):
    a, b = rng.sample(range(2, 8), 2)
    h, k = rng.sample(range(2, 7), 2)
    if _mode(rng, variant, 2) == 0:
        return _whole(f"An L-shaped board is made from two nonoverlapping rectangles. The left is {a} units wide and {h} units tall. The right is {b} units wide and {k} units tall. What is the total area in square units?", a * h + b * k)
    return _whole(f"An L-shaped floor has two nonoverlapping rectangles side by side. The total width is {a + b} meters. The left part is {a} meters wide and {h} meters tall. The right part is {k} meters tall. What is the whole floor area in square meters?", a * h + b * k)


def _compound_missing(rng, variant=None):
    return _compound_area(rng, variant=variant)


def _polygon_perimeter(rng, variant=None):
    count = rng.randint(3, 6)
    name = {3: "triangle", 4: "quadrilateral", 5: "pentagon", 6: "hexagon"}[count]
    # The longest side is shorter than the sum of the remaining sides.
    lengths = [rng.randint(4, 7) for _ in range(count)]
    total = sum(lengths)
    unit = rng.choice(("centimeters", "meters", "inches"))
    if _mode(rng, variant, 2):
        missing = rng.randrange(count)
        known = [str(n) for i, n in enumerate(lengths) if i != missing]
        return _whole(f"A {name} has a perimeter of {total} {unit}. All but one side measure {', '.join(known)} {unit}. What is the missing side length in {unit}?", lengths[missing])
    return _whole(f"A {name} has side lengths {', '.join(str(n) for n in lengths)} {unit}. Find its perimeter in {unit}.", total)


def _area_perimeter(rng, variant=None):
    a, b = rng.sample(range(2, 7), 2)
    if _mode(rng, variant, 2):
        a, b = sorted((a, b))
        b += 3
        c, d = a + 1, b - 1
    else:
        c, d = 2 * a, b
        b *= 2
    if 2 * (a + b) == 2 * (c + d):
        answer = "Same perimeter, different area."
    else:
        answer = "Same area, different perimeter."
    return _choice(rng, f"One rectangle is {a} units by {b} units. Another is {c} units by {d} units. How do their areas and perimeters compare?",
                   answer, ["Same perimeter, different area.", "Same area, different perimeter.",
                            "Same area and same perimeter.", "Both the area and perimeter are different."])


_POLYGONS = {
    3: ("triangle", "  ●\n ╱ ╲\n●───●"),
    4: ("quadrilateral", "  ●────●\n ╱    ╱\n●────●"),
    5: ("pentagon", "   ●\n  ╱ ╲\n ●   ●\n │   │\n ●───●"),
    6: ("hexagon", "  ●───●\n ╱     ╲\n●       ●\n ╲     ╱\n  ●───●"),
}


def _classify_polygons(rng, variant=None):
    n = rng.choice(tuple(_POLYGONS))
    label, figure = _POLYGONS[n]
    mode = _mode(rng, variant, 4)
    context = rng.choice(("on a sign", "in a drawing", "on a sheet of paper", "in a pattern", "on a game tile"))
    if mode == 0:
        return _choice(rng, f"A closed shape {context} has {n} straight sides and {n} corners. What kind of polygon is it?",
                       label, [p[0] for p in _POLYGONS.values()], figure=figure)
    if mode == 1:
        return _whole(f"A {label} appears {context}. How many corners does it have?", n, figure=figure)
    if mode == 2:
        return _choice(rng, f"Which description could be a {label} {context}?",
                       f"a closed shape with {n} straight sides",
                       [f"an open path of {n} straight sides", f"a closed shape with {n - 1} straight sides and one curved side",
                        f"a closed shape with {n + 1} straight sides"], figure=None)
    return _choice(rng, f"A shape {context} is closed and has {n} straight sides. Its sides are not all the same length. What can you still call it?",
                   label, [p[0] for p in _POLYGONS.values()])


def _equal_area_shares(rng, variant=None):
    d = rng.choice(_DENOMINATORS)
    a = rng.randint(2, 7)
    item = rng.choice(("piece of paper", "rectangle", "whole strip", "board"))
    mode = _mode(rng, variant, 3)
    if mode == 0:
        return _fraction(f"Split a {item} of {a * d} square units into {d} equal-area shares. What fraction of the whole is one share? Keep {d} as the bottom number.", 1, d, parts=True)
    if mode == 1:
        width = rng.randint(2, 8)
        figure = "A: " + "■ " * width + "\n   " + "■ " * width + "\nB: " + "■ " * (2 * width)
        return _choice(rng, f"One whole has two regions. Region A has 2 rows of {width} square units. Region B has 1 row of {2 * width} square units. What fraction of the whole is either region?",
                       "1/2", ["1/3", "1/4", "Different shapes cannot be equal shares."], figure=figure)
    return _whole(f"Make {d} equal-area shares of a {item} with area {a * d} square units. How many square units should each share cover?", a)



_VARIANT_COUNTS = {
    _equal_groups: 2, _division_meanings: 2, _multiplication_situations: 4,
    _unknown_numbers: 6, _operation_properties: 3, _division_inverse: 2,
    _fluency_strategies: 2, _multiply_facts: 3, _two_steps: 4, _equation_model: 4,
    _reasonableness: 2, _arithmetic_patterns: 2, _rounding: 3, _add_subtract: 3,
    _tens_products: 2, _place_value: 2, _number_forms: 4, _fraction_parts: 2,
    _fraction_line: 3, _unit_fraction_line: 3, _iterate_fraction_line: 2,
    _same_whole: 3, _equivalent_location: 3, _build_equivalent: 2,
    _whole_fractions: 2, _compare_fractions: 3, _time_minutes: 4,
    _metric_estimates: 2, _same_unit_measure: 4, _scaled_graph: 3,
    _fraction_measurement: 3, _area_meaning: 3, _unit_square: 3,
    _covering_area: 2, _count_tiles: 3, _rectangle_area: 2,
    _area_applications: 3, _distributive_area: 2, _compound_area: 2,
    _compound_missing: 2, _polygon_perimeter: 2, _area_perimeter: 2,
    _classify_polygons: 4, _equal_area_shares: 3,
}

@dataclass(frozen=True)
class _Skill:
    id: str
    title: str
    unit: int
    code: str
    lesson: str
    practices: tuple[int, ...]
    build: Callable[[Random], dict]


# Component skills have their own prompts. Parent-level rows practice the
# broader relationship, rather than claiming that every component was tested
# by one scored answer.
_SKILLS = (
    _Skill("equal-groups", "Equal groups and products", 1, "OA.1", "oa-1", (2, 4), _equal_groups),
    _Skill("division-meanings", "Sharing and grouping division", 1, "OA.2", "oa-2", (2, 4), _division_meanings),
    _Skill("multiplication-division-stories", "Multiplication and division stories", 1, "OA.3", "oa-3", (1, 4), _multiplication_situations),
    _Skill("missing-numbers", "Missing numbers in multiplication or division", 1, "OA.4", "oa-4", (2, 7), _unknown_numbers),
    _Skill("multiplication-properties", "Switch, regroup and split factors", 2, "OA.5", "oa-5", (3, 7), _operation_properties),
    _Skill("division-missing-factor", "Use multiplication to divide", 2, "OA.6", "oa-6", (2, 7), _division_inverse),
    _Skill("fact-strategies", "Multiplication and division fact strategies", 2, "OA.7a", "oa-7", (7, 8), _fluency_strategies),
    _Skill("multiplication-facts", "Multiplication fact recall", 2, "OA.7b", "oa-7", (7, 8), _multiply_facts),
    _Skill("arithmetic-patterns", "Patterns in arithmetic", 2, "OA.9", "oa-9", (7, 8), _arithmetic_patterns),
    _Skill("area-meaning", "What area measures", 3, "MD.5", "md-5", (2, 6), _area_meaning),
    _Skill("square-unit", "A unit square", 3, "MD.5a", "md-5", (5, 6), _unit_square),
    _Skill("covering-area", "Cover an area with squares", 3, "MD.5b", "md-5", (3, 5), _covering_area),
    _Skill("counting-square-units", "Count square units", 3, "MD.6", "md-6", (5, 6), _count_tiles),
    _Skill("area-products-and-sums", "Build area with products and sums", 3, "MD.7", "md-7", (4, 7), _compound_area),
    _Skill("rectangle-tiles", "Rectangle tiles and multiplication", 3, "MD.7a", "md-7", (4, 7), _rectangle_area),
    _Skill("rectangle-area", "Rectangle area in everyday problems", 3, "MD.7b", "md-7", (4, 6), _area_applications),
    _Skill("split-rectangle", "Split a rectangle into two products", 3, "MD.7c", "md-7", (3, 7), _distributive_area),
    _Skill("compound-area", "Area of joined rectangles", 3, "MD.7d", "md-7", (1, 7), _compound_missing),
    _Skill("rounding", "Round to tens and hundreds", 4, "NBT.1", "nbt-1", (1, 6), _rounding),
    _Skill("addition-subtraction", "Add and subtract within 1,000", 4, "NBT.2", "nbt-2", (1, 7), _add_subtract),
    _Skill("multiply-by-tens", "Multiply a single digit by tens", 4, "NBT.3", "nbt-3", (2, 7), _tens_products),
    _Skill("four-digit-place-value", "Place value in four-digit numbers", 4, "NBT.4a", "nbt-4", (2, 7), _place_value),
    _Skill("four-digit-number-forms", "Four-digit numbers in words, digits and expanded form", 4, "NBT.4b", "nbt-4", (6, 7), _number_forms),
    _Skill("two-step-stories", "Solve two-step stories", 5, "OA.8", "oa-8", (1, 4), _two_steps),
    _Skill("two-step-equations", "Write a model for a two-step story", 5, "OA.8a", "oa-8", (2, 4), _equation_model),
    _Skill("reasonable-estimates", "Estimate and check two-step answers", 5, "OA.8b", "oa-8", (1, 8), _reasonableness),
    _Skill("fraction-parts", "Fractions from equal parts", 6, "NF.1", "nf-1", (2, 4), _fraction_parts),
    _Skill("fraction-number-line", "Fractions on a number line", 6, "NF.2", "nf-2", (4, 5), _fraction_line),
    _Skill("first-unit-fraction", "Find the first unit fraction on a line", 6, "NF.2a", "nf-2", (4, 5), _unit_fraction_line),
    _Skill("fraction-jumps", "Use equal jumps, including beyond one", 6, "NF.2b", "nf-2", (4, 8), _iterate_fraction_line),
    _Skill("fraction-size", "Compare fraction sizes for the same whole", 6, "NF.3", "nf-3", (2, 3), _same_whole),
    _Skill("equivalent-fraction-location", "Recognize equivalent fractions", 6, "NF.3a", "nf-3", (3, 4), _equivalent_location),
    _Skill("build-equivalent-fractions", "Build and explain equivalent fractions", 6, "NF.3b", "nf-3", (3, 7), _build_equivalent),
    _Skill("whole-number-fractions", "Write whole numbers as fractions", 6, "NF.3c", "nf-3", (2, 7), _whole_fractions),
    _Skill("compare-fractions", "Compare fractions that share a top or bottom number", 6, "NF.3d", "nf-3", (3, 6), _compare_fractions),
    _Skill("equal-area-shares", "Divide shapes into equal-area shares", 6, "G.2", "g-2", (3, 4), _equal_area_shares),
    _Skill("time-and-elapsed-minutes", "Read times and find elapsed minutes", 7, "MD.1", "md-1", (5, 6), _time_minutes),
    _Skill("metric-measurements", "Grams, kilograms and liters", 7, "MD.2a", "md-2", (5, 6), _metric_estimates),
    _Skill("measurement-stories", "Solve measurement stories with the same units", 7, "MD.2b", "md-2", (2, 6), _same_unit_measure),
    _Skill("scaled-graphs", "Build and read scaled graphs", 8, "MD.3", "md-3", (4, 6), _scaled_graph),
    _Skill("inch-measurements-and-line-plots", "Measure halves and quarters of an inch; make line plots", 8, "MD.4", "md-4", (5, 6), _fraction_measurement),
    _Skill("polygon-perimeter", "Find a perimeter or a missing side", 9, "MD.8a", "md-8", (1, 6), _polygon_perimeter),
    _Skill("area-versus-perimeter", "Compare area and perimeter", 9, "MD.8b", "md-8", (3, 7), _area_perimeter),
    _Skill("classify-polygons", "Classify polygons", 9, "G.1", "g-1", (3, 6), _classify_polygons),
)

_UNITS = (
    "Equal groups and unknown quantities",
    "Multiplication strategies, division, fluency and patterns",
    "Square units, rectangle area and decomposing regions",
    "Place value, four-digit numbers and arithmetic",
    "Two-step situations and reasonable estimates",
    "Fraction numbers, equivalence and equal-area shares",
    "Minute intervals and metric measurement",
    "Scaled graphs and fractional-inch measurement data",
    "Polygon classification, perimeter and area comparisons",
)
_PRACTICE_LESSON_PREFIX = "reference:ny-g3-"
PRACTICE_LESSON_IDS = frozenset(_PRACTICE_LESSON_PREFIX + skill.lesson for skill in _SKILLS) | {
    "reference:ny-g3-mathematical-practices",
}
_TOPICS = {s.id: (s.title, s.unit, (s,)) for s in _SKILLS}
_TOPICS.update({f"unit-{n}": (f"Unit {n} mix: {title}", n, tuple(s for s in _SKILLS if s.unit == n)) for n, title in enumerate(_UNITS, 1)})


def practice_topics():
    """Return selectable unit mixes and individual standard topics."""
    return [{"id": topic_id, "title": title, "unit": unit,
             "standards": [f"NY-3.{s.code}" for s in skills]}
            for topic_id, (title, unit, skills) in _TOPICS.items()]


def _variant_order(rng, available, count):
    """Balance mathematical forms without putting them in a visible cycle."""
    forms = list(range(available))
    rng.shuffle(forms)
    order = [forms[i % available] for i in range(count)]
    for _ in range(64):
        rng.shuffle(order)
        if not any(order[i] == order[i + 1] == order[i + 2] for i in range(len(order) - 2)):
            return order
    # A bounded fallback also preserves counts and never needs model calls.
    remaining = {form: order.count(form) for form in forms}
    result = []
    while len(result) < count:
        choices = [form for form in forms if remaining[form] and not (len(result) >= 2 and result[-2:] == [form, form])]
        rng.shuffle(choices)
        form = max(choices, key=remaining.get)
        result.append(form)
        remaining[form] -= 1
    return result


def generate_practice(topic_id, seed, count):
    """Materialize one fixed snapshot. Inputs are bounded by the API too."""
    if not isinstance(topic_id, str) or topic_id not in _TOPICS:
        raise ValueError("Unknown Grade 3 practice topic")
    if not (type(seed) is str and 1 <= len(seed) <= 256 or type(seed) is int and 0 <= seed < 2**256):
        raise ValueError("A bounded seed is required")
    if type(count) is not int or not 1 <= count <= MAX_QUESTIONS:
        raise ValueError("Invalid practice length")
    rng = Random(f"{BANK_VERSION}:{seed}")
    skills = _TOPICS[topic_id][2]
    cycle, schedule = [], []
    for _ in range(count):
        if not cycle:
            cycle = list(skills)
            rng.shuffle(cycle)
        schedule.append(cycle.pop())
    variants = {skill.id: _variant_order(rng, _VARIANT_COUNTS[skill.build], sum(s.id == skill.id for s in schedule)) for skill in skills}
    seen, items = set(), []
    for i, skill in enumerate(schedule):
        variant = variants[skill.id].pop()
        for _ in range(250):
            item = skill.build(rng, variant=variant)
            # A different option shuffle alone is not a new question.
            content = (item["prompt"], item.get("figure"))
            if content not in seen:
                seen.add(content)
                break
        else:
            raise ValueError("Could not make a distinct practice question")
        code, reflection = _PRACTICES[rng.choice(skill.practices) - 1]
        items.append({**item, "id": f"q{i + 1}", "standards": [f"NY-3.{skill.code}"],
                      "lesson_id": _PRACTICE_LESSON_PREFIX + skill.lesson,
                      "practice": {"code": code, "prompt": reflection}})
    return items


_WHOLE = re.compile(r"(?:[0-9]{1,7}|[0-9]{1,3}(?:,[0-9]{3}){1,2})")
_FRACTION = re.compile(r"([0-9]{1,4})\s*/\s*([0-9]{1,2})")
_CLOCK = re.compile(r"(0?[1-9]|1[0-2]):([0-5]?[0-9])")
_PUBLIC = ("id", "prompt", "options", "answer_type", "standards", "lesson_id", "figure", "practice")


def grade_practice_item(item, answer):
    """Check a bounded structured response against its saved server-only key."""
    if type(answer) is not str or len(answer) > 120:
        return False
    answer = answer.strip()
    key = item["answer_key"]
    kind, value = key["kind"], key["value"]
    if kind == "choice":
        return answer == value
    if kind == "whole":
        return bool(_WHOLE.fullmatch(answer)) and int(answer.replace(",", "")) == value
    if kind in {"fraction", "fraction-parts"}:
        match = _FRACTION.fullmatch(answer)
        if not match:
            return kind == "fraction" and bool(_WHOLE.fullmatch(answer)) and Fraction(int(answer.replace(",", "")), 1) == Fraction(*value)
        n, d = map(int, match.groups())
        if d == 0:
            return False
        return [n, d] == value if kind == "fraction-parts" else Fraction(n, d) == Fraction(*value)
    if kind == "clock":
        match = _CLOCK.fullmatch(answer)
        return bool(match) and f"{int(match[1])}:{int(match[2]):02}" == value
    if kind == "counts":
        parts = [part.strip() for part in answer.split(",")]
        return len(parts) == len(value) and all(re.fullmatch(r"[0-9]{1,3}", part) for part in parts) and [int(part) for part in parts] == value
    return False


def public_practice_item(item):
    """Project only named student-safe fields; never serialize the answer key."""
    return {field: deepcopy(item[field]) for field in _PUBLIC if field in item}
