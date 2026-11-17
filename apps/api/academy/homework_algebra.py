"""Closed, bounded algebra syntax for de-identified Homework Help.

This module never evaluates Python or forwards source text. It parses complete
mathematical expressions into a small AST, validates them independently, and
derives instructional methods without returning an assessed final answer.
"""
from fractions import Fraction
import math
import re

MAX_NODES = 64
MAX_DEPTH = 12
MAX_TEXT = 600
MAX_LITERAL = 10000
MAX_DENOMINATOR = 10000
MAX_EXPONENT = 10
MAX_VALUE = 10**12
VARIABLES = {"x", "y"}
RELATIONS = {"eq", "lt", "le", "gt", "ge"}
METHODS = {"auto", "quadratic_formula", "completing_square", "factoring", "substitution", "elimination"}
FEATURES = {"graph", "zeros", "vertex", "slope", "domain", "range"}
_BINARY = {"add", "sub", "mul", "div", "pow"}
_UNARY = {"neg", "sqrt", "abs"}
_RELATION_TEXT = {"=": "eq", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
_RELATION_SYMBOL = {value: key for key, value in _RELATION_TEXT.items()}
_SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?|\.\d+)|(sqrt|abs)|([xy])|(\*\*|<=|>=|[+*/^()|=<>-]))", re.I)
_ATTEMPT = re.compile(r"^\s*(?:i\s+(?:got|tried|think(?:\s+(?:it|(?:the|my)\s+answer)\s+is)?)|my\s+(?:answer|solution)\s*(?:is|:)?|check\s+my\s+(?:answer|solution)\s*:?)\s*(.+)$", re.I)


def _number(value):
    value = Fraction(value)
    return {"type": "number", "value": [value.numerator, value.denominator]}


def _fraction(value):
    if (type(value) is not list or len(value) != 2 or any(type(n) is not int for n in value)
            or not 1 <= value[1] <= MAX_DENOMINATOR or abs(value[0]) > MAX_LITERAL * value[1]):
        raise ValueError("Unsupported algebra number")
    result = Fraction(*value)
    if [result.numerator, result.denominator] != value:
        raise ValueError("Algebra numbers must use reduced rational form")
    return result


def _bounded(value):
    if isinstance(value, Fraction):
        if abs(value) > MAX_VALUE or value.numerator.bit_length() > 160 or value.denominator.bit_length() > 160:
            raise ValueError("Algebra calculation exceeds its bounds")
    elif not math.isfinite(value) or abs(value) > MAX_VALUE:
        raise ValueError("Algebra calculation exceeds its bounds")
    return value


def _variables(node):
    if node["type"] == "variable":
        return {node["name"]}
    if node["type"] in _UNARY:
        return _variables(node["value"])
    if node["type"] in _BINARY:
        return _variables(node["left"]) | _variables(node["right"])
    return set()


def _evaluate(node, values=None):
    """Bounded arithmetic for validation only; never a student answer API."""
    values = values or {}
    kind = node["type"]
    if kind == "number":
        return Fraction(*node["value"])
    if kind == "variable":
        if node["name"] not in values:
            raise ValueError("Symbolic algebra cannot be evaluated without a value")
        return values[node["name"]]
    if kind in _UNARY:
        value = _evaluate(node["value"], values)
        if kind == "neg":
            return _bounded(-value)
        if kind == "abs":
            return abs(value)
        if value < 0:
            raise ValueError("The square root is outside the supported real domain")
        if isinstance(value, Fraction):
            top, bottom = math.isqrt(value.numerator), math.isqrt(value.denominator)
            if top * top == value.numerator and bottom * bottom == value.denominator:
                return Fraction(top, bottom)
        return _bounded(math.sqrt(value))
    left, right = _evaluate(node["left"], values), _evaluate(node["right"], values)
    if kind == "add":
        return _bounded(left + right)
    if kind == "sub":
        return _bounded(left - right)
    if kind == "mul":
        return _bounded(left * right)
    if kind == "div":
        if right == 0 or not isinstance(right, Fraction) and abs(right) < 1e-12:
            raise ValueError("Division by zero is outside the supported domain")
        return _bounded(left / right)
    if abs(right) > 32:
        raise ValueError("An evaluated exponent exceeds its bounds")
    if left == 0 and right <= 0 or left < 0 and (not isinstance(right, Fraction) or right.denominator != 1):
        raise ValueError("The power is outside the supported real domain")
    if left and right and float(right) * math.log(abs(float(left))) > math.log(MAX_VALUE):
        raise ValueError("An evaluated power exceeds its bounds")
    exponent = int(right) if isinstance(right, Fraction) and right.denominator == 1 else float(right)
    return _bounded(left ** exponent)


def _constant(node):
    return None if _variables(node) else _evaluate(node)


def _poly(node):
    """Exact small bivariate polynomial, or None for non-polynomial syntax."""
    kind = node["type"]
    if kind == "number":
        return {(0, 0): Fraction(*node["value"])}
    if kind == "variable":
        return {(1, 0) if node["name"] == "x" else (0, 1): Fraction(1)}
    if kind == "neg":
        value = _poly(node["value"])
        return None if value is None else {power: -n for power, n in value.items()}
    if kind in {"sqrt", "abs"}:
        if not _variables(node):
            value = _constant(node)
            return {(0, 0): value} if isinstance(value, Fraction) else None
        return None
    left, right = _poly(node["left"]), _poly(node["right"])
    if left is None or right is None:
        return None
    if kind == "div":
        denominator = _constant(node["right"])
        return None if not isinstance(denominator, Fraction) or not denominator else {power: n / denominator for power, n in left.items()}
    if kind == "pow":
        exponent = _constant(node["right"])
        if not isinstance(exponent, Fraction) or exponent.denominator != 1 or not 0 <= exponent <= MAX_EXPONENT:
            return None
        result = {(0, 0): Fraction(1)}
        for _ in range(int(exponent)):
            result = _poly_multiply(result, left)
            if result is None:
                return None
        return result
    if kind == "mul":
        return _poly_multiply(left, right)
    result = dict(left)
    for power, n in right.items():
        result[power] = result.get(power, Fraction(0)) + (n if kind == "add" else -n)
    return {power: n for power, n in result.items() if n} or {(0, 0): Fraction(0)}


def _poly_multiply(left, right):
    result = {}
    for a, av in left.items():
        for b, bv in right.items():
            power = (a[0] + b[0], a[1] + b[1])
            if sum(power) > MAX_EXPONENT:
                return None
            result[power] = _bounded(result.get(power, Fraction(0)) + av * bv)
    return {power: n for power, n in result.items() if n} or {(0, 0): Fraction(0)}


def _walk(node):
    yield node
    if node["type"] in _UNARY:
        yield from _walk(node["value"])
    elif node["type"] in _BINARY:
        yield from _walk(node["left"])
        yield from _walk(node["right"])


def _validate_node(node, count, depth=0):
    count[0] += 1
    if count[0] > MAX_NODES or depth > MAX_DEPTH or type(node) is not dict or type(node.get("type")) is not str:
        raise ValueError("Algebra expression is too complex or malformed")
    kind = node["type"]
    if kind == "number":
        if set(node) != {"type", "value"}:
            raise ValueError("Unexpected algebra number field")
        return _number(_fraction(node["value"]))
    if kind == "variable":
        if set(node) != {"type", "name"} or type(node["name"]) is not str or node["name"] not in VARIABLES:
            raise ValueError("Unsupported algebra variable")
        return dict(node)
    if kind in _UNARY:
        if set(node) != {"type", "value"}:
            raise ValueError("Unexpected unary algebra field")
        result = {"type": kind, "value": _validate_node(node["value"], count, depth + 1)}
    elif kind in _BINARY:
        if set(node) != {"type", "left", "right"}:
            raise ValueError("Unexpected binary algebra field")
        result = {"type": kind, "left": _validate_node(node["left"], count, depth + 1), "right": _validate_node(node["right"], count, depth + 1)}
        if kind == "div" and _constant(result["right"]) == 0:
            raise ValueError("An algebra denominator cannot be zero")
        if kind == "pow":
            exponent = _constant(result["right"])
            if exponent is not None:
                if not isinstance(exponent, Fraction) or exponent.denominator != 1 or abs(exponent) > MAX_EXPONENT:
                    raise ValueError("Only bounded integer powers are supported; use sqrt for square roots")
            else:
                base = _constant(result["left"])
                poly = _poly(result["right"])
                if base is None or not 0 < base <= 1000 or base == 1 or poly is None or max(map(sum, poly)) > 1 or len(_variables(result["right"])) != 1:
                    raise ValueError("Only a positive constant base with a linear exponent is supported")
    else:
        raise ValueError("Unsupported algebra expression node")
    if not _variables(result):
        _evaluate(result)
    return result


def _family(expressions):
    nodes = [node for expression in expressions for node in _walk(expression)]
    if any(node["type"] == "div" and _variables(node["right"]) or node["type"] == "pow" and (_constant(node["right"]) or 0) < 0 for node in nodes):
        return "rational"
    if any(node["type"] == "sqrt" and _variables(node["value"]) for node in nodes):
        return "radical"
    if any(node["type"] == "abs" and _variables(node["value"]) for node in nodes):
        return "absolute_value"
    if any(node["type"] == "pow" and _variables(node["right"]) for node in nodes):
        return "exponential"
    return "polynomial"


def _degree(expression):
    poly = _poly(expression)
    return None if poly is None else max(map(sum, poly))


def validate_algebra_task(task):
    if type(task) is not dict or task.get("kind") != "algebra" or type(task.get("action")) is not str:
        raise ValueError("Invalid public algebra task")
    action = task["action"]
    count = [0]
    if action in {"solve", "solve_system", "study_relation"}:
        expected = {"kind", "action", "relations", "feature" if action == "study_relation" else "method"}
        if set(task) != expected or type(task["relations"]) is not list:
            raise ValueError("Invalid algebra relation task")
        if action == "study_relation":
            if type(task["feature"]) is not str or task["feature"] not in {"graph", "slope"}:
                raise ValueError("Unsupported linear relation feature")
        elif type(task["method"]) is not str or task["method"] not in METHODS:
            raise ValueError("Invalid algebra solving method")
        if not 1 <= len(task["relations"]) <= 2 or action == "solve_system" and len(task["relations"]) != 2:
            raise ValueError("Only one equation or a bounded two-relation task is supported")
        relations = []
        for relation in task["relations"]:
            if type(relation) is not dict or set(relation) != {"left", "operator", "right"} or type(relation["operator"]) is not str or relation["operator"] not in RELATIONS:
                raise ValueError("Invalid algebra relation")
            relations.append({"left": _validate_node(relation["left"], count), "operator": relation["operator"], "right": _validate_node(relation["right"], count)})
        expressions = [node for relation in relations for node in (relation["left"], relation["right"])]
        variables = set().union(*(_variables(node) for node in expressions))
        if action == "study_relation":
            if len(relations) != 1 or relations[0]["operator"] != "eq" or any(_degree(node) is None or _degree(node) > 1 for node in expressions):
                raise ValueError("Only linear implicit graphs and slopes are supported")
            return {"kind": "algebra", "action": action, "relations": relations, "feature": task["feature"]}
        if action == "solve_system":
            if any(r["operator"] != "eq" for r in relations) or task["method"] not in {"auto", "substitution", "elimination"}:
                raise ValueError("Only linear equation systems are supported")
            if any(_degree(node) is None or _degree(node) > 1 for node in expressions):
                raise ValueError("Only linear equation systems are supported")
        else:
            if len(variables) > 1 or task["method"] in {"substitution", "elimination"}:
                raise ValueError("Use one variable, or a two-equation linear system")
            if len(relations) == 2 and (any(r["operator"] == "eq" for r in relations) or relations[0]["right"] != relations[1]["left"]):
                raise ValueError("Compound inequalities must retain their shared middle expression")
            family = _family(expressions)
            if family == "polynomial" and any(_degree(node) is None or _degree(node) > 2 for node in expressions):
                raise ValueError("Polynomial equations are supported through degree two")
            if task["method"] != "auto" and family != "polynomial":
                raise ValueError("This solving method does not match the equation family")
            if task["method"] in {"quadratic_formula", "completing_square", "factoring"}:
                difference = {"type": "sub", "left": relations[0]["left"], "right": relations[0]["right"]}
                if len(relations) != 1 or relations[0]["operator"] != "eq" or _degree(difference) != 2:
                    raise ValueError("A quadratic solving method requires a quadratic equation")
        return {"kind": "algebra", "action": action, "relations": relations, "method": task["method"]}
    if action not in {"factor", "expand", "simplify", "study_function", "evaluate_function"}:
        raise ValueError("Unsupported algebra action")
    expected = {"kind", "action", "expression"} | ({"feature"} if action == "study_function" else {"input"} if action == "evaluate_function" else set())
    if set(task) != expected:
        raise ValueError("Unexpected algebra task field")
    expression = _validate_node(task["expression"], count)
    variables = _variables(expression)
    if action in {"factor", "expand"} and (_degree(expression) is None or _degree(expression) > 4):
        raise ValueError("Factoring and expansion require a polynomial of degree at most four")
    result = {"kind": "algebra", "action": action, "expression": expression}
    if action in {"study_function", "evaluate_function"} and variables - {"x"}:
        raise ValueError("A function body must use only x")
    if action == "study_function":
        if type(task["feature"]) is not str or task["feature"] not in FEATURES:
            raise ValueError("Unsupported function feature")
        if task["feature"] == "vertex" and _degree(expression) != 2 or task["feature"] == "slope" and _degree(expression) not in {0, 1}:
            raise ValueError("The requested feature does not match the function")
        result["feature"] = task["feature"]
    if action == "evaluate_function":
        argument = _fraction(task["input"])
        _evaluate(expression, {"x": argument})
        result["input"] = [argument.numerator, argument.denominator]
    return result


def _normalize(text):
    text = text.replace("−", "-").replace("–", "-").replace("×", "*").replace("·", "*").replace("⋅", "*").replace("÷", "/").replace("≤", "<=").replace("≥", ">=")
    text = re.sub(r"(?<=[xy0-9)])([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)", lambda m: "^(" + m.group().translate(_SUPER) + ")", text)
    text = text.replace("√", "sqrt")
    return text


class _Parser:
    def __init__(self, text):
        self.tokens = []
        self.index = 0
        self.bar_depth = 0
        position = 0
        while position < len(text):
            match = _TOKEN.match(text, position)
            if not match:
                if text[position:].strip():
                    raise ValueError("Unknown text in algebra expression")
                break
            token = next(value for value in match.groups() if value is not None).lower()
            if self.tokens and self.tokens[-1][0] in VARIABLES and match.group(1) and not text[position:match.start(1)].strip():
                # x2 is an ambiguous OCR form, never silently x^2 or x*2.
                raise ValueError("A variable followed by a number needs an explicit operator")
            self.tokens.append((token, match.group(1) is not None))
            position = match.end()
            if len(self.tokens) > 120:
                raise ValueError("Algebra expression is too long")

    def peek(self):
        return self.tokens[self.index][0] if self.index < len(self.tokens) else None

    def take(self, expected=None):
        value = self.peek()
        if value is None or expected is not None and value != expected:
            raise ValueError("Incomplete algebra expression")
        self.index += 1
        return value

    def expression(self):
        node = self.product()
        while self.peek() in {"+", "-"}:
            operator = self.take()
            node = {"type": "add" if operator == "+" else "sub", "left": node, "right": self.product()}
        return node

    def product(self):
        node = self.unary()
        while True:
            token = self.peek()
            if token in {"*", "/"}:
                operator = self.take()
                node = {"type": "mul" if operator == "*" else "div", "left": node, "right": self.unary()}
            elif token in {"x", "y", "(", "sqrt", "abs"} or token == "|" and self.bar_depth == 0:
                node = {"type": "mul", "left": node, "right": self.unary()}
            else:
                return node

    def unary(self):
        if self.peek() == "+":
            self.take()
            return self.unary()
        if self.peek() == "-":
            self.take()
            return {"type": "neg", "value": self.unary()}
        return self.power()

    def power(self):
        node = self.atom()
        if self.peek() in {"^", "**"}:
            self.take()
            node = {"type": "pow", "left": node, "right": self.unary()}
        return node

    def atom(self):
        token = self.take()
        if re.fullmatch(r"\d+(?:\.\d+)?|\.\d+", token):
            if len(token) > 12:
                raise ValueError("Algebra number is too long")
            return _number(Fraction(token))
        if token in VARIABLES:
            return {"type": "variable", "name": token}
        if token == "(":
            node = self.expression()
            self.take(")")
            return node
        if token in {"sqrt", "abs"}:
            if self.peek() == "(":
                self.take()
                node = self.expression()
                self.take(")")
            else:
                node = self.atom()
            return {"type": token, "value": node}
        if token == "|":
            self.bar_depth += 1
            node = self.expression()
            self.take("|")
            self.bar_depth -= 1
            return {"type": "abs", "value": node}
        raise ValueError("Expected an algebra operand")


def _expression(text):
    parser = _Parser(text)
    node = parser.expression()
    if parser.peek() is not None:
        raise ValueError("The complete algebra expression must be understood")
    return node


def looks_like_algebra(text):
    # Preserve the established elementary notation "3 x 7" as multiplication.
    scanned = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", text, flags=re.I)
    if re.search(r"[=<>^√⁰¹²³⁴⁵⁶⁷⁸⁹]|\*\*|(?<![a-z])[xy](?=\d|\b)|\b(?:sqrt|abs|algebra|quadratic|polynomial|factor\w*|expand|simplify|function|inequalit\w*|exponent\w*|radical\w*)\b", scanned, re.I):
        return True
    numeric = re.fullmatch(r"\s*(?:please\s+)?(?:explain|evaluate|calculate|help\s+(?:me\s+)?with)\s+([0-9\s.+−×÷*/()^-]+)[?.!\s]*", text, re.I)
    if not numeric:
        return False
    body = _normalize(numeric.group(1)).strip()
    if re.fullmatch(r"\d{1,4}[-/]\d{1,4}[-/]\d{1,4}|\d{3}\s+\d{3}\s+\d{4}", body):
        return False
    return bool(re.search(r"[()]|\d\.\d|^[+-]", body) or len(re.findall(r"[+*/-]", body)) > 1)


def is_algebra_attempt(text):
    text = _normalize(text).strip().rstrip("?.")
    text = re.sub(r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?(?=check\s+my\s+)", "", text, flags=re.I)
    match = _ATTEMPT.match(text)
    if not match:
        return False
    tail = re.sub(r"\s*(?:[.!?,;]\s*)?(?:is that (?:right|correct)|(?:can|could|would) you (?:please )?check(?: (?:it|my (?:answer|work|method)))?)\s*$", "", match.group(1), flags=re.I).strip()
    tail = re.sub(r"\b(?:sqrt|abs)\b", "", tail, flags=re.I)
    return bool(tail and re.fullmatch(r"[xy0-9.\s+*/^()|=<>{};,-]+", tail, re.I))


def is_bare_algebra_input(text):
    text = _normalize(text).strip().rstrip("?.")
    check = re.sub(r"\b(?:sqrt|abs)\b", "", text, flags=re.I)
    return bool((looks_like_algebra(text) or re.search(r"\d", text)) and re.fullmatch(r"[xy0-9.\s+*/^()|=<>{};,-]+", check, re.I))


def _request(text):
    text = _normalize(text).strip().rstrip("?. ")
    if len(text) > MAX_TEXT or _ATTEMPT.match(text):
        raise ValueError("Use the original bounded problem, not a student answer")
    text = re.sub(r"\s*(?:without\s+(?:just\s+)?(?:giving|telling|showing)\s+me\s+(?:the\s+)?(?:final\s+)?answer|step\s+by\s+step|show\s+(?:me\s+)?(?:the\s+)?steps|please)\s*$", "", text, flags=re.I).strip()
    for _ in range(4):
        next_text = re.sub(r"^(?:please\s+|(?:can|could|would)\s+you\s+|how\s+(?:do|can)\s+i\s+|how\s+to\s+|i\s+need\s+help\s+(?:with|to)\s+|help\s+(?:me\s+)?(?:with\s+|to\s+)?|(?:show|walk)\s+me\s+(?:how\s+to|through)\s+|give\s+me\s+(?:a\s+)?hint\s+(?:for|on|with)\s+|understand\s+(?:how\s+to\s+)?|i\s+don't\s+understand\s+)", "", text, flags=re.I)
        if next_text == text:
            break
        text = next_text.strip()
    method = "auto"
    methods = {"quadratic formula": "quadratic_formula", "completing the square": "completing_square", "factoring": "factoring", "substitution": "substitution", "elimination": "elimination"}
    for phrase, value in methods.items():
        match = re.search(r"\s+(?:using|by|with)\s+(?:the\s+)?" + re.escape(phrase) + r"$", text, re.I)
        if match:
            method, text = value, text[:match.start()].strip()
            break
    feature = "graph"
    feature_match = re.match(r"^(?:find|identify|determine)\s+(?:the\s+)?(zeros?|roots?|x-intercepts?|vertex|slope|domain|range)\s+(?:of|for)\s+", text, re.I)
    if feature_match:
        feature = {"zero": "zeros", "zeros": "zeros", "root": "zeros", "roots": "zeros", "x-intercept": "zeros", "x-intercepts": "zeros"}.get(feature_match.group(1).lower(), feature_match.group(1).lower())
        return text[feature_match.end():], "study_function", method, feature
    match = re.match(r"^(solve|factor(?:ise|ize)?|expand|simplify|evaluate|calculate|graph|analy[sz]e|study|explain)\b\s*", text, re.I)
    action = "auto"
    if match:
        verb = match.group(1).lower()
        action = "factor" if verb.startswith("factor") else "study_function" if verb in {"graph", "analyze", "analyse", "study"} else "auto" if verb in {"explain", "calculate"} else verb
        text = text[match.end():]
    text = re.sub(r"^(?:for\s+[xy]\s*:?\s*|(?:the\s+)?(?:equation|inequality|system|expression|function)\s*(?:of\s+equations\s*)?:?\s*)", "", text, flags=re.I).strip()
    return text, action, method, feature


def parse_algebra_task(text):
    """Parse a whole supported algebra request, or return None with no I/O."""
    if type(text) is not str or not looks_like_algebra(text):
        return None
    try:
        body, action, method, feature = _request(text)
        expression_at = re.fullmatch(r"(?P<body>.+?)\s+(?:at|when|for)\s+x\s*=\s*(?P<input>.+)", body, re.I)
        if expression_at and action == "evaluate" and method == "auto" and "=" not in expression_at.group("body"):
            value = _constant(_expression(expression_at.group("input")))
            if not isinstance(value, Fraction):
                return None
            return validate_algebra_task({"kind": "algebra", "action": "evaluate_function", "expression": _expression(expression_at.group("body")), "input": [value.numerator, value.denominator]})
        given = re.fullmatch(r"given\s+(?P<function>[fg])\(x\)\s*=\s*(?P<body>.+?)[,;]\s*(?:help\s+me\s+)?(?:find|evaluate)\s+(?P=function)\((?P<input>.+)\)", body, re.I)
        if given:
            if method != "auto":
                return None
            value = _constant(_expression(given.group("input")))
            if not isinstance(value, Fraction):
                return None
            return validate_algebra_task({"kind": "algebra", "action": "evaluate_function", "expression": _expression(given.group("body")), "input": [value.numerator, value.denominator]})
        evaluation = re.fullmatch(r"(?P<function>[fg])\((?P<input>.+?)\)\s+(?:given|if|for|where)\s+(?P=function)\(x\)\s*=\s*(?P<body>.+)", body, re.I)
        if not evaluation:
            reverse = re.fullmatch(r"(?P<function>[fg])\(x\)\s*=\s*(?P<body>.+?)[;,]\s*(?:find|evaluate)\s+(?P=function)\((?P<input>.+)\)", body, re.I)
            if not reverse:
                reverse = re.fullmatch(r"(?P<function>[fg])\(x\)\s*=\s*(?P<body>.+?)(?:[;,]|\s+)\s*(?:at|when)\s+x\s*=\s*(?P<input>.+)", body, re.I)
            if reverse:
                evaluation = reverse
        if evaluation:
            if method != "auto":
                return None
            argument, expression = evaluation.group("input"), evaluation.group("body")
            value = _constant(_expression(argument))
            if not isinstance(value, Fraction):
                return None
            return validate_algebra_task({"kind": "algebra", "action": "evaluate_function", "expression": _expression(expression), "input": [value.numerator, value.denominator]})
        function = re.fullmatch(r"(?:[fg]\(x\)|y)\s*=\s*(.+)", body, re.I)
        if function and action != "solve":
            if method != "auto":
                return None
            expression = _expression(function.group(1))
            if action in {"factor", "expand", "simplify"}:
                return validate_algebra_task({"kind": "algebra", "action": action, "expression": expression})
            return validate_algebra_task({"kind": "algebra", "action": "study_function", "expression": expression, "feature": feature})
        body = body.strip()
        if body.startswith("{") and body.endswith("}"):
            body = body[1:-1].strip()
        separate = re.split(r"\s*(?:;|\n|\band\b|,)\s*", body)
        if len(separate) > 2 or any(not item for item in separate):
            return None
        if len(separate) == 2:
            relations = []
            for part in separate:
                pieces = re.split(r"(<=|>=|=|<|>)", part)
                if len(pieces) != 3 or pieces[1] != "=":
                    return None
                relations.append({"left": _expression(pieces[0]), "operator": "eq", "right": _expression(pieces[2])})
            if action not in {"auto", "solve"}:
                return None
            return validate_algebra_task({"kind": "algebra", "action": "solve_system", "relations": relations, "method": method})
        pieces = re.split(r"(<=|>=|=|<|>)", body)
        if len(pieces) in {3, 5}:
            if action not in {"auto", "solve", "study_function"}:
                return None
            expressions = [_expression(item) for item in pieces[::2]]
            relations = [{"left": expressions[i], "operator": _RELATION_TEXT[pieces[2 * i + 1]], "right": expressions[i + 1]} for i in range(len(expressions) - 1)]
            if action == "study_function":
                if method != "auto":
                    return None
                return validate_algebra_task({"kind": "algebra", "action": "study_relation", "relations": relations, "feature": feature})
            return validate_algebra_task({"kind": "algebra", "action": "solve", "relations": relations, "method": method})
        if len(pieces) != 1 or method != "auto":
            return None
        expression = _expression(body)
        if action == "study_function":
            return validate_algebra_task({"kind": "algebra", "action": action, "expression": expression, "feature": feature})
        if action not in {"auto", "factor", "expand", "simplify"}:
            return None
        return validate_algebra_task({"kind": "algebra", "action": "simplify" if action == "auto" else action, "expression": expression})
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError, RecursionError):
        return None


def _notation(node):
    kind = node["type"]
    if kind == "number":
        top, bottom = node["value"]
        return str(top) if bottom == 1 else f"({top}/{bottom})"
    if kind == "variable":
        return node["name"]
    if kind == "neg":
        return "-(" + _notation(node["value"]) + ")"
    if kind in {"sqrt", "abs"}:
        return kind + "(" + _notation(node["value"]) + ")"
    symbol = {"add": "+", "sub": "-", "mul": "*", "div": "/", "pow": "^"}[kind]
    return "(" + _notation(node["left"]) + symbol + _notation(node["right"]) + ")"


def algebra_summary(task):
    task = validate_algebra_task(task)
    if "relations" in task:
        label = "Linear system: " if task["action"] == "solve_system" else "Linear relation " + task["feature"] + ": " if task["action"] == "study_relation" else "Algebra: "
        return label + "; ".join(_notation(r["left"]) + " " + _RELATION_SYMBOL[r["operator"]] + " " + _notation(r["right"]) for r in task["relations"])
    result = task["action"].replace("_", " ").capitalize() + ": " + _notation(task["expression"])
    if task["action"] == "evaluate_function":
        result += " at x=" + _notation(_number(Fraction(*task["input"])))
    if task["action"] == "study_function":
        result += " (" + task["feature"] + ")"
    return result


def algebra_guidance(task):
    """Code-owned hints and domain obligations; deliberately no final solution."""
    task = validate_algebra_task(task)
    expressions = [node for r in task.get("relations", []) for node in (r["left"], r["right"])] or [task["expression"]]
    family = _family(expressions)
    conditions = []
    for expression in expressions:
        for node in _walk(expression):
            condition = None
            if node["type"] == "div" and _variables(node["right"]):
                condition = {"expression": node["right"], "operator": "ne", "value": [0, 1]}
            elif node["type"] == "pow":
                exponent = _constant(node["right"])
                if exponent is not None and exponent <= 0 and _variables(node["left"]):
                    condition = {"expression": node["left"], "operator": "ne", "value": [0, 1]}
            elif node["type"] == "sqrt" and _variables(node["value"]):
                condition = {"expression": node["value"], "operator": "ge", "value": [0, 1]}
            if condition and condition not in conditions:
                conditions.append(condition)
    action = task["action"]
    method = {"factor": "look_for_common_factors_then_products", "expand": "distribute_then_combine_like_terms", "simplify": "preserve_domain_then_apply_algebra_laws", "study_function": "identify_function_family_then_requested_feature", "study_relation": "inspect_linear_coefficients_before_graphing_or_slope", "evaluate_function": "substitute_input_before_operations", "solve_system": "align_coefficients_then_eliminate_or_substitute"}.get(action)
    detail = {}
    if action == "solve":
        relation = task["relations"][0]
        difference = {"type": "sub", "left": relation["left"], "right": relation["right"]}
        poly = _poly(difference)
        if family == "polynomial" and poly is not None:
            degree = max(map(sum, poly))
            if degree == 0:
                method = "compare_remaining_constants_to_classify_solutions"
            elif degree == 1:
                method = "collect_variable_terms_then_isolate"
                detail["reverse_inequality_when_dividing_by_negative"] = any(r["operator"] != "eq" for r in task["relations"])
            else:
                method = "factor_or_use_quadratic_formula"
                if task["method"] != "auto":
                    method = task["method"]
                detail["check_discriminant_before_claiming_real_roots"] = True
        else:
            method = {"rational": "record_denominator_exclusions_then_clear_denominators", "radical": "isolate_radical_then_square_and_check_original", "absolute_value": "isolate_absolute_value_then_consider_both_signs", "exponential": "compare_bases_or_use_inverse_operations"}[family]
    if action == "solve_system":
        detail["check_dependent_or_inconsistent_equations"] = True
        if task["method"] != "auto":
            method = task["method"]
    if action in {"study_function", "study_relation"}:
        detail["feature"] = task["feature"]
    if action == "study_relation":
        detail["check_vertical_horizontal_or_degenerate_line"] = True
        detail["when_line_is_vertical_slope_is_undefined_and_it_is_not_a_function_of_x"] = True
    if action == "factor":
        detail["verify_factors_by_expanding"] = True
        detail["do_not_assume_integer_factors_exist"] = True
    if any(r["operator"] != "eq" for r in task.get("relations", [])):
        detail["reverse_inequality_when_dividing_by_negative"] = True
        detail["unknown_sign_scaling_requires_cases"] = True
    if any(node["type"] == "sqrt" and node["value"]["type"] == "pow" and _constant(node["value"]["right"]) == 2
           for expression in expressions for node in _walk(expression)):
        detail["square_root_of_square_requires_absolute_value"] = True
    return {"family": family, "method": method, "domain_conditions": conditions, "check_transformed_candidates_in_original": bool(conditions), "do_not_supply_final_answer": True, **detail}
