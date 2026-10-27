"""Pinned declarative GameSpec v1/v2 validator, adapted from Game Studio.

Source: NorthCountryEngineer/Game-Studio f09860b9973b0359ea36684aad66618bf7a67bbf,
backend/nce_api/games.py. No authoring, AWS client, code or remote asset execution.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
IDENTIFIER = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
UNSAFE_TEXT = re.compile(r'[\x00-\x1f\x7f-\x9f]|[<>]|https?://|www\.|(?:javascript|data|vbscript)[\s\ufeff]*:|(?<![a-zA-Z0-9_])(?:eval|fetch|import)[\s\ufeff]*\(', re.I)
PUBLIC_FIELDS = {'id', 'spec', 'revision', 'status', 'publishedRevision', 'createdAt', 'updatedAt', 'publishedAt'}
PRIVATE_FIELDS = PUBLIC_FIELDS | {'messages', 'testedRevision'}
_framework = None


class GameError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def fail(message, status=400):
    raise GameError(status, message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat()


def fields(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        fail('Unexpected or missing game fields.')


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        fail(label + ' is outside its allowed range.')
    return value


JS_WHITESPACE = ' \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'


def text(value, maximum, label, empty=False, safe=True):
    if not isinstance(value, str): fail(label + ' must be text.')
    trimmed = value.strip(JS_WHITESPACE)
    if len(value.encode('utf-16-le', errors='surrogatepass')) // 2 > maximum or (not empty and not trimmed) or safe and (value != trimmed or UNSAFE_TEXT.search(value)):
        fail(label + ' is invalid or too long.')
    try: value.encode('utf-8')
    except UnicodeEncodeError: fail(label + ' contains invalid Unicode.')
    return value


def identifier(value):
    if not isinstance(value, str) or not UUID.fullmatch(value): fail('Invalid game identifier.')
    return value


def validate_spec(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] not in {1, 2}:
        fail('Unsupported game version.')
    required = ('version', 'title', 'description', 'genre', 'theme', 'player', 'rules', 'objects', 'instructions')
    fields(value, required + (('progression', 'levels') if value['version'] == 2 else ()) + (('arcade',) if 'arcade' in value else ()))
    text(value['title'], 80, 'Game title'); text(value['description'], 300, 'Description', True)
    text(value['instructions'], 300, 'Instructions', True)
    if not isinstance(value['genre'], str) or value['genre'] not in {'collect', 'dodge', 'platformer'}: fail('Choose a supported game style.')
    if not isinstance(value['theme'], str) or value['theme'] not in {'pond', 'space', 'garden'}: fail('Choose a supported game world.')
    if 'arcade' in value:
        if value['genre'] != 'dodge': fail('Arcade options are supported only for dodge games.')
        arcade = value['arcade']
        fields(arcade, ('rotation', 'shooting', 'splitAsteroids', 'scoring', 'highScores'))
        if any(type(option) is not bool for option in arcade.values()): fail('Arcade options must be true or false.')
        if arcade['splitAsteroids'] and not arcade['shooting']: fail('Splitting asteroids requires shooting.')
        if arcade['highScores'] and not arcade['scoring']: fail('High scores require scoring.')
    player, rules, objects = value['player'], value['rules'], value['objects']
    fields(player, ('name', 'color', 'speed', 'jump'))
    text(player['name'], 40, 'Player name')
    if not isinstance(player['color'], str) or not re.fullmatch(r'#[a-fA-F0-9]{6}', player['color']): fail('Player color must have six hexadecimal digits.')
    integer(player['speed'], 2, 8, 'Player speed'); integer(player['jump'], 6, 14, 'Jump strength')
    validate_rules(rules)
    fields(objects, ('collectible', 'hazard'))
    text(objects['collectible'], 30, 'Collectible'); text(objects['hazard'], 30, 'Hazard')
    if value['version'] == 2:
        text(value['progression'], 300, 'Level progression')
        levels = value['levels']
        if not isinstance(levels, list) or not 1 <= len(levels) <= 8: fail('Choose from 1 to 8 levels.')
        seen = set()
        for level in levels:
            fields(level, ('id', 'title', 'brief', 'theme', 'rules', 'seed'))
            if not isinstance(level['id'], str) or not re.fullmatch(r'level-[1-8]', level['id']) or level['id'] in seen:
                fail('Level identifiers must be unique level-1 through level-8 values.')
            seen.add(level['id'])
            text(level['title'], 80, 'Level title'); text(level['brief'], 300, 'Level brief')
            if not isinstance(level['theme'], str) or level['theme'] not in {'pond', 'space', 'garden'}:
                fail('Choose a supported level world.')
            validate_rules(level['rules'])
            integer(level['seed'], 1, 2147483647, 'Level layout seed')
    return deepcopy(value)


def validate_rules(rules):
    fields(rules, ('goal', 'timeLimit', 'lives', 'difficulty'))
    for name, low, high in [('goal', 3, 40), ('timeLimit', 30, 180), ('lives', 1, 5), ('difficulty', 1, 5)]:
        integer(rules[name], low, high, name)
