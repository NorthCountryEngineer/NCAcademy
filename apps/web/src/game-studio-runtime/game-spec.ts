import type { GameArcadeOptions, GameLevel, GameSpec } from './game-types';

/** Data is the only author-controlled input to the fixed game engine. */
export function validateGameSpec(value: unknown): GameSpec {
  function object(input: unknown, keys: string[], label: string): Record<string, unknown> {
    if (!input || typeof input !== 'object' || Array.isArray(input) || ![Object.prototype, null].includes(Object.getPrototypeOf(input))) throw new Error(`${label} must be an object.`);
    const record = input as Record<string, unknown>;
    if (Object.keys(record).length !== keys.length || keys.some(key => !Object.prototype.hasOwnProperty.call(record, key)) || Object.keys(record).some(key => !keys.includes(key))) throw new Error(`${label} has missing or unexpected fields.`);
    return record;
  }
  function text(input: unknown, max: number, label: string, allowEmpty = false): string {
    if (typeof input !== 'string' || (!allowEmpty && !input.trim()) || input !== input.trim() || input.length > max || /[\u0000-\u001f\u007f-\u009f]/u.test(input) || /[<>]|(?:https?:\/\/|www\.|javascript\s*:|data\s*:|vbscript\s*:)|\b(?:eval|fetch|import)\s*\(/iu.test(input)) throw new Error(`${label} must be plain text up to ${max} characters, without links or code.`);
    return input;
  }
  function integer(input: unknown, min: number, max: number, label: string): number {
    if (typeof input !== 'number' || !Number.isInteger(input) || input < min || input > max) throw new Error(`${label} must be a whole number from ${min} to ${max}.`);
    return input;
  }
  const version = value && typeof value === 'object' ? (value as Record<string, unknown>).version : undefined;
  if (version !== 1 && version !== 2) throw new Error('Unsupported game version.');
  const hasArcade = Object.prototype.hasOwnProperty.call(value, 'arcade');
  const spec = object(value, ['version', 'title', 'description', 'genre', 'theme', 'player', 'rules', 'objects', 'instructions', ...(version === 2 ? ['progression', 'levels'] : []), ...(hasArcade ? ['arcade'] : [])], 'Game');
  const player = object(spec.player, ['name', 'color', 'speed', 'jump'], 'Player');
  const rules = object(spec.rules, ['goal', 'timeLimit', 'lives', 'difficulty'], 'Rules');
  const objects = object(spec.objects, ['collectible', 'hazard'], 'Objects');
  if (!['collect', 'dodge', 'platformer'].includes(spec.genre as string)) throw new Error('Choose collect, dodge, or platformer.');
  if (!['pond', 'space', 'garden'].includes(spec.theme as string)) throw new Error('Choose pond, space, or garden.');
  if (typeof player.color !== 'string' || !/^#[0-9a-f]{6}$/iu.test(player.color)) throw new Error('Player color must be a six-digit hex color.');
  const clean: GameSpec = {
    version,
    title: text(spec.title, 80, 'Title'), description: text(spec.description, 300, 'Description', true),
    genre: spec.genre as GameSpec['genre'], theme: spec.theme as GameSpec['theme'],
    player: { name: text(player.name, 40, 'Player name'), color: player.color, speed: integer(player.speed, 2, 8, 'Speed'), jump: integer(player.jump, 6, 14, 'Jump') },
    rules: { goal: integer(rules.goal, 3, 40, 'Goal'), timeLimit: integer(rules.timeLimit, 30, 180, 'Time'), lives: integer(rules.lives, 1, 5, 'Lives'), difficulty: integer(rules.difficulty, 1, 5, 'Difficulty') },
    objects: { collectible: text(objects.collectible, 30, 'Collectible'), hazard: text(objects.hazard, 30, 'Hazard') },
    instructions: text(spec.instructions, 300, 'Instructions', true),
  };
  if (hasArcade) {
    if (spec.genre !== 'dodge') throw new Error('Arcade options are supported only for dodge games.');
    const arcade = object(spec.arcade, ['rotation', 'shooting', 'splitAsteroids', 'scoring', 'highScores'], 'Arcade options');
    if (Object.values(arcade).some(value => typeof value !== 'boolean')) throw new Error('Arcade options must be true or false.');
    if (arcade.splitAsteroids && !arcade.shooting) throw new Error('Splitting asteroids requires shooting.');
    if (arcade.highScores && !arcade.scoring) throw new Error('High scores require scoring.');
    clean.arcade = { ...arcade } as GameArcadeOptions;
  }
  if (version === 2) {
    clean.progression = text(spec.progression, 300, 'Progression');
    if (!Array.isArray(spec.levels) || spec.levels.length < 1 || spec.levels.length > 8) throw new Error('A game needs 1 to 8 levels.');
    const ids = new Set<string>();
    clean.levels = spec.levels.map(input => {
      const level = object(input, ['id', 'title', 'brief', 'theme', 'rules', 'seed'], 'Level');
      if (typeof level.id !== 'string' || !/^level-[1-8]$/u.test(level.id) || ids.has(level.id)) throw new Error('Level identifiers must be unique, from level-1 to level-8.');
      ids.add(level.id);
      if (!['pond', 'space', 'garden'].includes(level.theme as string)) throw new Error('Choose pond, space, or garden for each level.');
      const settings = object(level.rules, ['goal', 'timeLimit', 'lives', 'difficulty'], 'Level rules');
      return { id: level.id, title: text(level.title, 80, 'Level title'), brief: text(level.brief, 300, 'Level brief'), theme: level.theme as GameLevel['theme'], seed: integer(level.seed, 1, 2147483647, 'Layout seed'), rules: { goal: integer(settings.goal, 3, 40, 'Level goal'), timeLimit: integer(settings.timeLimit, 30, 180, 'Level time'), lives: integer(settings.lives, 1, 5, 'Level lives'), difficulty: integer(settings.difficulty, 1, 5, 'Level difficulty') } };
    });
  }
  return clean;
}
