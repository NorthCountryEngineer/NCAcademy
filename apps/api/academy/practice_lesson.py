"""Provider-free conversations over human-approved Grade 3 reference text."""
import re

from fastapi import HTTPException

from .standards import corpus_descriptor


MAX_QUESTION = 1200
LOCAL_BUDGET = {"max_turns": 24, "max_total_tokens": 2**31 - 1}
STOP = set("a an and are at be can could did do does for from had have how i in is it like me my of on or please should so some that the them then there these they this to up us was we what when where which why will with would you your help explain understand show tell know work problem question math example give another practice use using make mean numbers number more way".split())
ALIASES = {
    "multiplication": "multiply", "multiplying": "multiply", "multiplied": "multiply",
    "division": "divide", "dividing": "divide", "divided": "divide", "quotient": "divide",
    "rounding": "round", "rounded": "round", "addition": "add", "adding": "add",
    "subtraction": "subtract", "subtracting": "subtract", "fractions": "fraction",
    "fractional": "fraction", "equivalent": "equivalence", "equivalents": "equivalence",
    "graphs": "graph", "polygons": "polygon", "rectangles": "rectangle", "groups": "group",
    "trapezoids": "trapezoid", "arrays": "array", "vertices": "vertex", "clocks": "clock",
    "measurement": "measure", "measuring": "measure", "minutes": "minute",
    "factors": "factor", "products": "product", "units": "unit", "plots": "plot",
    "intervals": "interval", "lengths": "length", "parts": "part", "marks": "mark",
    "endpoints": "endpoint", "rows": "row", "columns": "column", "dots": "dot",
}


def _tokens(value):
    return {ALIASES.get(word, word) for word in re.findall(r"[a-z]{2,}", value.lower()) if word not in STOP}


def current_practice_references(db, course, school_id):
    from .grade3_practice_content import grade3_practice_course, PRACTICE_LESSON_IDS

    if not grade3_practice_course(course):
        raise HTTPException(403, "Reviewed practice chat is available only in this year's aligned Grade 3 math class")
    descriptor = corpus_descriptor(db, course, school_id)
    if not descriptor.get("ready_for_teaching") or descriptor.get("coverage") != "complete_standards":
        raise HTTPException(409, "The current class materials need human approval before practice chat")
    documents = {
        row["id"]: row for row in descriptor["documents"]
        if row["id"] in PRACTICE_LESSON_IDS and row["kind"] == "source_reference"
        and row.get("audience") == "all" and isinstance(row.get("content"), str) and row["content"].strip()
    }
    return descriptor, documents


def choose_reference(documents, question, focused_id, previous_id):
    """Rank built-in math text; follow-ups can stay with a current exact lesson."""
    if focused_id is not None and focused_id not in documents:
        raise HTTPException(409, "That reviewed practice lesson is not available. Return to the assignment to choose a current lesson.")
    tokens = _tokens(question)
    # Titles and rare terminology are stronger signals than shared words in
    # longer passages. Nothing in the question is executed or used as policy.
    weighted = []
    for key, doc in documents.items():
        title = tokens & _tokens(doc["title"])
        text = tokens & _tokens(doc["content"])
        score = 5 * len(title) + len(text)
        if score:
            weighted.append((score, key))
    best = max(weighted, default=(0, None))
    if focused_id:
        return documents[focused_id]
    if best[1] and (best[0] >= 5 or previous_id not in documents):
        return documents[best[1]]
    if previous_id in documents and (best[1] or re.search(r"\b(example|different|another|again|this|that|stuck|understand|slower|simpler|why|how|next|check)\b", question.lower())):
        return documents[previous_id]
    raise HTTPException(422, "Tell me the math idea you want to work on, like equal groups, rounding, fractions, or area.")


def _section_spans(content):
    """Keep exact, complete sentences; never paraphrase approved material."""
    sentences, start = [], 0
    for boundary in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9])", content):
        sentences.append((start, boundary.start()))
        start = boundary.end()
    if start < len(content):
        sentences.append((start, len(content)))
    if len(sentences) < 2:
        return []
    sections, index = [], 0
    while index < len(sentences):
        start, end = sentences[index]
        if index + 1 < len(sentences) and sentences[index + 1][1] - start <= 420:
            index += 1
            end = sentences[index][1]
        if end - start <= 420:
            sections.append((start, end))
        index += 1
    if len(sections) == 1 and sections[0] == (0, len(content)):
        return [span for span in sentences if span[1] - span[0] <= 420]
    return sections


def select_practice_excerpt(content, message, *, started, previous_spans=()):
    """Start with the overview, then focus on a relevant section.

    The overview is not counted as a focused section: a follow-up can revisit
    a small part of it without sending the entire lesson again. A deliberate
    request to repeat may revisit a section. Exhaustion returns no excerpt.
    """
    if not started:
        return {"start": 0, "end": min(len(content), 1600), "mode": "overview"}
    sections = _section_spans(content)
    repeat = bool(re.search(r"\b(repeat|again|remind)\b", message.casefold()))
    seen = set(previous_spans)
    available = sections if repeat else [span for span in sections if span not in seen]
    if not available:
        return None
    tokens = _tokens(message)
    wants_model = bool(re.search(r"\b(draw|drawing|picture|sketch|different way)\b", message.casefold()))
    wants_example = bool(re.search(r"\b(example|another|show)\b", message.casefold()))

    def rank(span):
        text = content[span[0]:span[1]]
        terms = _tokens(text)
        score = 3 * len(tokens & terms)
        if wants_model:
            score += 2 * len(terms & {"draw", "drawing", "sketch", "picture", "array", "model"})
        if wants_example and re.search(r"\d", text):
            score += 1
        return score, -span[0]

    start, end = max(available, key=rank)
    return {"start": start, "end": end, "mode": "focus"}


EXHAUSTED_PRACTICE_TEXT = (
    "I don't have another example or explanation in this lesson. "
    "Tell me which step is still confusing, or ask your teacher for another example."
)


def next_question(message, continued, turn_index=0):
    """Fixed study prompts encourage the student's own approach."""
    text = message.casefold()
    if re.search(r"\b(check|verify|right|reasonable)\b", text):
        prompts = (
            "Try checking the step with a picture or a different operation. What does your check tell you?",
            "Which part of your picture or calculation could you use to check that step?",
            "Does your result fit the quantities in the question? Explain one way you checked.",
        )
    elif re.search(r"\b(another|different|drawing|picture|draw)\b", text):
        prompts = (
            "Try showing the idea with a sketch or objects. Which parts of your picture represent the numbers?",
            "Choose one part of the idea to draw. What does each mark or object stand for?",
            "How does your drawing connect to the quantities in the question?",
        )
    elif re.search(r"\b(example|show|simpler|slower)\b", text) or continued:
        prompts = (
            "Try the first step from this part of the lesson. What would you do next?",
            "Which sentence helps with the step you are working on? Explain it in your own words.",
            "What do you understand so far, and which step would you like to look at again?",
        )
    else:
        prompts = ("In your own words, what is the question asking you to find?",)
    return prompts[turn_index % len(prompts)]
