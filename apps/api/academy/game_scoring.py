"""Host-owned deterministic points; untrusted observations are never grades."""
from copy import deepcopy
from decimal import Decimal
import math

from fastapi import HTTPException

from .game_studio_schemas import GameScoringPolicy


def scoring_policy(value, spec):
    if value is None:
        return None
    policy = GameScoringPolicy.model_validate(value).snapshot()
    if policy["method"] == "arcade_target" and not spec.get("arcade", {}).get("scoring"):
        raise HTTPException(422, "Arcade target scoring requires a game with arcade points enabled")
    return policy


def validate_result(value, spec):
    """Mirror the pinned engine, accepting only a terminal whole-play report."""
    def invalid():
        raise HTTPException(422, "Result does not match the exact imported game")
    arcade = bool(spec.get("arcade", {}).get("scoring"))
    fields = {"outcome", "elapsed", "score", "reviewedLevelIds"} | ({"points"} if arcade else set())
    if not isinstance(value, dict) or set(value) != fields or not isinstance(value.get("outcome"), str) or value["outcome"] not in {"won", "lost"}:
        invalid()
    levels = spec["levels"] if spec["version"] == 2 else [{"id": "level-1", "rules": spec["rules"]}]
    ids = value["reviewedLevelIds"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= len(levels) or ids != [level["id"] for level in levels[:len(ids)]]:
        invalid()
    played = levels[:len(ids)]
    maximum = sum(level["rules"]["goal"] for level in played)
    elapsed, score = value["elapsed"], value["score"]
    if type(elapsed) not in {int, float} or not 0 <= elapsed <= sum(level["rules"]["timeLimit"] for level in played) or not math.isfinite(elapsed) or type(score) is not int or not 0 <= score <= maximum:
        invalid()
    if value["outcome"] == "won" and (len(ids) != len(levels) or score != maximum):
        invalid()
    if arcade:
        limit = sum(level["rules"]["goal"] * 10 + (level["rules"]["timeLimit"] * 2 + 8) * (120 if spec["arcade"]["splitAsteroids"] else 20) for level in played)
        if type(value["points"]) is not int or not 0 <= value["points"] <= limit:
            invalid()
    return deepcopy(value)


def calculate_points(spec, result, policy):
    policy = scoring_policy(policy, spec)
    result = validate_result(result, spec)
    if policy["method"] == "completion":
        numerator, denominator = int(result["outcome"] == "won"), 1
    elif policy["method"] == "arcade_target":
        denominator = policy["arcade_target"]
        numerator = min(result["points"], denominator)
    else:
        numerator = result["score"]
        denominator = sum(level["rules"]["goal"] for level in spec["levels"]) if spec["version"] == 2 else spec["rules"]["goal"]
    factor = 100 if policy["rounding"] == "hundredth" else 1
    scaled = (2 * numerator * policy["max_points"] * factor + denominator) // (2 * denominator)
    earned = scaled / factor if factor == 100 else scaled
    return {"earned_points": earned, "max_points": policy["max_points"], "numerator": numerator, "denominator": denominator, "method": policy["method"], "provisional": True}


def select_attempt(attempts, policy):
    completed = [attempt for attempt in attempts if attempt.get("calculation")]
    if not completed:
        return None
    if policy["attempt_policy"] == "latest":
        return max(completed, key=lambda item: item["ordinal"])
    if policy["attempt_policy"] == "best":
        return min(completed, key=lambda item: (-item["calculation"]["earned_points"], item["ordinal"]))
    return min(completed, key=lambda item: item["ordinal"])


def validate_award(points, maximum):
    value = Decimal(str(points))
    if not value.is_finite() or value < 0 or value > maximum or value * 100 != (value * 100).to_integral_value():
        raise HTTPException(422, "Reviewed points must be within the attempt's scale, with at most two decimal places")
    return float(value)
