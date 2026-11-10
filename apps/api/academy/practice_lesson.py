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


def next_question(message, continued):
    """Fixed study prompts encourage the student's own approach."""
    text = message.casefold()
    if re.search(r"\b(check|verify|right|reasonable)\b", text):
        return "Try checking the step with a picture or a different operation. What does your check tell you?"
    if re.search(r"\b(another|different|drawing|picture|draw)\b", text):
        return "Try showing the idea with a sketch or objects. Which parts of your picture represent the numbers?"
    if re.search(r"\b(example|show|simpler|slower)\b", text) or continued:
        return "Look at how the lesson starts, then try that first step on a new example of your own. What would you do next?"
    return "In your own words, what is the question asking you to find?"
