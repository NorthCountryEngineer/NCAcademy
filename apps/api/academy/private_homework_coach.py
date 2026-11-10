"""Select reviewed coaching prompts using text and local OCR of normalized photos."""

from dataclasses import dataclass
import os
import re
import selectors
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired
import sys
from threading import BoundedSemaphore
from time import monotonic
from typing import Sequence

from .curated_media import MAX_IMAGE_OUTPUT_BYTES


@dataclass(frozen=True)
class CoachingResult:
    status: str
    answer: str | None
    error: str | None
    topic: str | None
    image_read: bool


@dataclass(frozen=True)
class Topic:
    cues: re.Pattern[str]
    hint: str
    follow_up: str


def _topic(cues: str, hint: str, follow_up: str) -> Topic:
    return Topic(re.compile(cues, re.IGNORECASE), hint, follow_up)


_TOPICS = {
    "multiplication": _topic(
        r"\b(?:multipl\w+|times tables?|repeated addition|arrays?)\b|\d\s*[×x*]\s*\d",
        "Try an equal-groups drawing. Draw one circle for each group and put the same number of dots in each circle. Which number tells you how many groups to draw?",
        "Arrange your dots in rows with the same number of dots in each row. Label how many rows you drew and how many dots are in each row. How could you check your total by adding the row sizes?",
    ),
    "division": _topic(
        r"\b(?:division|quotient|remainder|shar(?:e|es|ed|ing)(?:\s+\w+){0,4}\s+equally)\b"
        r"|\bdivid(?:e|es|ed|ing)\b(?!\s+into\s+(?:\w+\s+){0,2}equal\s+parts\b)"
        r"|\d\s*÷\s*\d|\d\s+/\s+\d",
        "Ask whether you know the number of groups or the size of each group. Use dots or counters to share equally or to make groups of the given size. What are you trying to find?",
        "Check each group has the same number of counters. Count the groups and the counters in each group. If you have leftovers, explain why they cannot make another complete group.",
    ),
    "equal_groups": _topic(
        r"\bequal groups\b",
        "Draw a circle for each group and use dots for the things in each group. Which amounts does your question already give you: the number of groups, the size of each group, or the total?",
        "Mark the amount you are trying to find on your drawing. Check that every group has the same size. How does the drawing show the missing amount?",
    ),
    "fractions": _topic(
        r"\b(?:fraction\w*|denominator\w*|numerator\w*|equal parts|halves|quarters?)\b|(?<!/)\b\d+/\d+\b(?!/)",
        "Draw the whole and split it into equal parts. The bottom number names how many equal parts make the whole. The top number counts the parts you are talking about. Which parts would you mark on your drawing?",
        "Try a number line from zero to one. Make equal spaces between zero and one and count the spaces from zero. How does that point match your drawing of the whole?",
    ),
    "place_value": _topic(
        r"\b(?:place value|expanded form|base.ten|tens place|ones place|hundreds place|thousands place)\b",
        "Make a place-value chart. Starting on the right, label the columns ones, tens, and hundreds. Put each digit in its column. What amount does the digit you are looking at represent?",
        "Draw the groups for each place or write each digit's value on its own. How can you put the values back together to check that they make your original number?",
    ),
    "rounding": _topic(
        r"\b(?:round(?:ed|ing)? (?:\w+\s+){0,4}(?:nearest|ten|hundred|thousand)|rounding|nearest (?:ten|hundred|thousand))\b",
        "Draw a number line and label the two nearest multiples of the place you are rounding to. Mark the halfway point and then your number. Which endpoint is closer?",
        "Check your reasoning by counting the distance to each endpoint. If the distances are equal, use the rounding rule from your class for the halfway point.",
    ),
    "addition": _topic(
        r"\b(?:addition|add|adding|sum|plus|combine)\b|\d\s*\+\s*\d",
        "Line up the numbers by place value and start with the ones. When you have ten of one place, regroup them as one of the next place. What happens when you combine the ones in your problem?",
        "Try checking with a number line. Start at one number and make jumps for the other number's hundreds, tens, and ones. Compare your endpoint with your place-value work.",
    ),
    "subtraction": _topic(
        r"\b(?:subtract\w*|minus|how many fewer|take away)\b|(?<![-−])\b\d+\s*[-−]\s*\d+\b(?![-−])",
        "Line up the numbers by place value. Start with the ones. If there aren't enough ones to subtract, trade one ten for ten ones. Which column do you need to look at first?",
        "Check a subtraction result by adding it back to the number you took away. What number should that addition return you to?",
    ),
    "area": _topic(
        r"\b(?:area|square units?|cover the surface)\b",
        "Use unit squares to cover the inside of the shape with no gaps or overlaps. For a rectangle, organize the squares into equal rows. How can you use the rows to count the squares?",
        "For a shape made from more than one rectangle, draw a line to split it. Think about how you could find each part's area and check that every square is counted once.",
    ),
    "perimeter": _topic(
        r"\b(?:perimeter|distance around|outside edge)\b",
        "Trace the outside edge of the shape and mark each side length as you go. Find the distance around by combining those lengths. Which edge do you need to trace first?",
        "Start at a corner and follow the outside until you reach it again. Check that you counted each outer edge once and used the same unit for every side.",
    ),
    "measurement": _topic(
        r"\b(?:measurement|measur\w+ length|centimeters?|millimeters?|kilometers?|meters?|inches|feet|yards?|liquid volume|milliliters?|liters?|grams?|kilograms?)\b",
        "Decide what kind of measurement the problem asks for and label the unit. If there is a measuring tool, look at the values on two labeled marks. What does each smaller step on the tool represent?",
        "Check whether the amounts use the same unit. For a comparison, draw the amounts on the same scale and explain how you can tell which is greater.",
    ),
    "time": _topic(
        r"\b(?:elapsed time|telling time|clock|minute hand|hour hand|start time|end time)\b",
        "Mark the starting time on a number line. Make jumps forward to useful times such as the next hour, and write how many minutes each jump takes. What is a useful first stop?",
        "Add the lengths of the jumps and then check by traveling backward from the ending time. Does your path reach the same starting time?",
    ),
    "money": _topic(
        r"\b(?:money|dollars?|coins?|cents?|penn(?:y|ies)|nickels?|dimes?|making change)\b|\$\d",
        "Make a list of the coins or bills and write the value beside each one. Use the same unit for each amount before combining or comparing values. What does the question ask you to find?",
        "Check your work by grouping equal coins first. If the question is about change, count up from the cost to the amount paid and compare with your first method.",
    ),
    "data": _topic(
        r"\b(?:bar graphs?|picture graphs?|line plots?|data tables?|graph scale|graph key|tally marks?)\b",
        "Read the graph or table title, labels, and scale. Pick the category the question asks about and check what each mark or symbol represents. Which part of the display answers the question?",
        "For a comparison, write down the values for the two categories in the same units. Decide whether you need the total, the difference, or just the larger value.",
    ),
    "geometry": _topic(
        r"\b(?:quadrilaterals?|polygons?|angles?|triangles?|rhomb(?:us|i)|parallel sides|right angles|geometr\w+)\b",
        "List what you can see about the shape: its sides, corners, equal lengths, and any parallel sides. Which property does the question ask you to use?",
        "Sketch the shape in a different position. Compare its sides and corners with your first drawing. Which of the properties stayed the same when you turned it?",
    ),
    "reading": _topic(
        r"\b(?:main idea|reading comprehension|text evidence|infer(?:ence|ring)?|character traits?|summari[sz]\w*|theme of|reading passage|point of view)\b",
        "Reread the question and underline what it asks you to explain. Find a short part of the reading that supports your thinking. Which detail would you point to, and why?",
        "Say your idea aloud in your own words, then connect it to the detail you chose. Check whether your explanation tells how the detail supports your idea.",
    ),
    "writing": _topic(
        r"\b(?:essay|paragraph|topic sentence|writing prompt|thesis statement|outline|revis\w+|drafting)\b",
        "Put the prompt into your own words. Pick one point you want to make and write down two details you could use to support it. What should your reader understand by the end?",
        "Read your first paragraph aloud. Check that its sentences work together to explain one main idea. Which sentence could use more evidence or a clearer explanation in your own words?",
    ),
    "science": _topic(
        r"\b(?:science|experiment\w*|hypothesis|observations?|fair test|water cycle|life cycle|ecosystem\w*|food chains?|habitat\w*|evaporation|condensation|states of matter|photosynthesis)\b",
        "Start with what you can observe and what the question asks you to explain. Sketch the process or make two columns for observations and explanations. What observation would support your explanation?",
        "For a fair test, change one thing at a time and decide what to measure. For a diagram, trace one step at a time and explain what changes from one step to the next.",
    ),
}

_COMPLETION_REQUEST = re.compile(
    r"\b(?:just|only)\s+(?:tell me|give me|say|show me|write|solve|answer)\b"
    r"|\b(?:give|tell|show)\s+me\s+(?:the\s+)?(?:final\s+)?(?:answers?|solutions?)\b"
    r"|\b(?:what(?:'s| is| are)\s+(?:the\s+)?(?:final\s+)?(?:answers?|solutions?))\b"
    r"|\b(?:do|complete|finish|write|solve|answer)\b[^.!?\n]{0,64}\b(?:for me|for us)\b"
    r"|\b(?:write|complete|finish|do|solve)\s+(?:my|our|this|the)\s+"
    r"(?:whole\s+|entire\s+)?(?:homework|assignment|worksheet|essay|paper|report|paragraph)\b"
    r"|\b(?:answer key|(?:answers?|solutions?)\s+only|without (?:steps|explanations?))\b",
    re.IGNORECASE,
)
_FOLLOW_UP = re.compile(
    r"\b(?:another hint|more help|next (?:step|hint)|what next|now what|still stuck|"
    r"try again|another way|can you explain|how do I check)\b|^\s*why[?.!\s]*$",
    re.IGNORECASE,
)
_WORK_REFUSAL = "I can give you a hint or help you check your reasoning. Tell me where you're stuck or what you've tried so far."
_CLARIFY = "Which part are you working on? Name the topic or tell me what the question asks and where you got stuck."
_CLARIFY_PHOTO = "I couldn't identify the topic from the photo. Type the topic or the part of the question that is confusing."

_MAX_IMAGES = 2
_OCR_OUTPUT_LIMIT = 4096
_OCR_DEADLINE_SECONDS = 4.0
_OCR_SLOTS = BoundedSemaphore(2)
_OCR_RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_homework_ocr_runner.py")
_OCR_ENV = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "HOME": "/nonexistent",
    "PATH": "/usr/bin:/bin",
    "OMP_THREAD_LIMIT": "1",
    "OMP_NUM_THREADS": "1",
}


def _capture(process: Popen[bytes], content: bytes, deadline: float) -> bytes:
    output = bytearray()
    remaining = memoryview(content)
    with selectors.DefaultSelector() as selector:
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            time_left = deadline - monotonic()
            if time_left <= 0:
                return b""
            for key, event in selector.select(time_left):
                if event == selectors.EVENT_WRITE:
                    remaining = remaining[os.write(key.fd, remaining[:4096]):]
                    if not remaining:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    chunk = os.read(key.fd, _OCR_OUTPUT_LIMIT + 1 - len(output))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    output.extend(chunk)
                    if len(output) > _OCR_OUTPUT_LIMIT:
                        return b""
    if process.wait(timeout=max(0, deadline - monotonic())) != 0:
        return b""
    return bytes(output)


def _read_image(content: bytes, deadline: float) -> str:
    if (not isinstance(content, bytes) or len(content) > MAX_IMAGE_OUTPUT_BYTES
            or not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9")
            or monotonic() >= deadline):
        return ""
    try:
        process = Popen(
            [sys.executable, "-I", "-B", _OCR_RUNNER],
            stdin=PIPE, stdout=PIPE, stderr=DEVNULL, close_fds=True, cwd="/", env=_OCR_ENV,
        )
        with process:
            try:
                return _capture(process, content, deadline).decode("utf-8", errors="replace").strip()
            finally:
                if process.poll() is None:
                    process.kill()
    except (OSError, TimeoutExpired):
        return ""


def _match(text: str) -> str | None:
    # Only compare a closed vocabulary and symbol patterns. The input never
    # controls commands, templates, arithmetic, reference links or output text.
    matches = [key for key, topic in _TOPICS.items() if topic.cues.search(text)]
    if "area" in matches and "measurement" in matches:
        matches.remove("measurement")
    if "perimeter" in matches and "measurement" in matches:
        matches.remove("measurement")
    if "equal_groups" in matches and ("division" in matches or "multiplication" in matches):
        matches.remove("equal_groups")
    return matches[0] if len(matches) == 1 else None


def coach_homework(message: str, images: Sequence[bytes] = (), prior_topic: str | None = None) -> CoachingResult:
    """Choose a fixed hint from the current text, a printed photo, or a prior topic."""
    message = message[:4096].replace("’", "'")
    if _COMPLETION_REQUEST.search(message):
        return CoachingResult("blocked", None, _WORK_REFUSAL, None, False)

    topic = _match(message)
    image_read = False
    if images and _OCR_SLOTS.acquire(blocking=False):
        try:
            deadline = monotonic() + _OCR_DEADLINE_SECONDS
            detected = set()
            for image in images[:_MAX_IMAGES]:
                recognized = _read_image(image, deadline)
                if recognized:
                    image_read = True
                    detected.add(_match(recognized))
            if topic is None and len(detected) == 1:
                topic = detected.pop()
        finally:
            _OCR_SLOTS.release()

    follow_up = prior_topic in _TOPICS and bool(_FOLLOW_UP.search(message))
    if follow_up and topic in (None, prior_topic):
        return CoachingResult("released", _TOPICS[prior_topic].follow_up, None, prior_topic, image_read)
    if topic:
        return CoachingResult("released", _TOPICS[topic].hint, None, topic, image_read)
    return CoachingResult("clarify", _CLARIFY_PHOTO if images else _CLARIFY, None, None, image_read)
