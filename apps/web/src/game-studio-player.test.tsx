// Gameplay regression cases adapted from Game Studio f09860b; see runtime provenance.
// @ts-expect-error Node built-ins run in Vitest; the browser package has no Node type dependency.
import assert from 'node:assert/strict';
// @ts-expect-error Node built-ins run in Vitest; the browser package has no Node type dependency.
import { Script, createContext } from 'node:vm';
// @ts-expect-error Node built-ins run in Vitest; the browser package has no Node type dependency.
import { createHash } from 'node:crypto';
// @ts-expect-error Node built-ins run in Vitest; the browser package has no Node type dependency.
import { readFileSync } from 'node:fs';
import { test, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { GameStudioPlayer } from './GameStudioPlayer';
import { validateGameSpec } from './game-studio-runtime/game-spec';
import { createGameEngine, gameSandboxDocument, startGameSandbox, validateGameResult, type GameState } from './game-studio-runtime/game-runtime';
import { readGameStudioMessage } from './game-studio-runtime/player-messages';
import { GAME_STUDIO_RUNTIME_CONTRACT, GAME_STUDIO_RUNTIME_DIGEST } from './game-studio-runtime/runtime-build';
import provenance from './game-studio-runtime/provenance.json';
import type { GameResult, GameSpec } from './game-studio-runtime/game-types';

function defaultGameSpec(): GameSpec {
  return { version: 1, title: 'Practice adventure', description: 'Collect water snacks.', genre: 'collect', theme: 'pond', player: { name: 'Explorer', color: '#efadc4', speed: 5, jump: 10 }, rules: { goal: 10, timeLimit: 60, lives: 3, difficulty: 2 }, objects: { collectible: 'water snack', hazard: 'rock' }, instructions: 'Use arrows to collect snacks and avoid rocks.' };
}

test('game schema rejects extra and missing fields at every nesting level', () => {
  const original = defaultGameSpec();
  for (const change of [
    { ...original, script: 'alert(1)' },
    { ...original, player: { ...original.player, url: 'https://example.com' } },
    { ...original, rules: { ...original.rules, unlimited: true } },
    { ...original, objects: { ...original.objects, asset: 'secret' } },
    { ...original, version: 2 },
  ]) assert.throws(() => validateGameSpec(change));
  const missing = { ...original } as Record<string, unknown>; delete missing.instructions;
  assert.throws(() => validateGameSpec(missing), /missing or unexpected/);
  assert.throws(() => validateGameSpec({ ...original, player: [] }));
});

test('game schema enforces bounded integers, plain text, and fixed rendering options', () => {
  for (const bad of [-1, 0, 2.5, 41, Infinity, NaN, '10']) assert.throws(() => validateGameSpec({ ...defaultGameSpec(), rules: { ...defaultGameSpec().rules, goal: bad } }));
  for (const field of ['lives', 'difficulty']) for (const bad of [0, 6, 1.2]) assert.throws(() => validateGameSpec({ ...defaultGameSpec(), rules: { ...defaultGameSpec().rules, [field]: bad } }));
  for (const field of ['speed', 'jump']) assert.throws(() => validateGameSpec({ ...defaultGameSpec(), player: { ...defaultGameSpec().player, [field]: 99 } }));
  for (const title of ['<script>alert(1)</script>', 'https://example.com', 'javascript:alert(1)', 'data:text/html,hi', 'fetch(secret)', 'hidden\u0085control', 'line\nbreak', 'x'.repeat(81)]) assert.throws(() => validateGameSpec({ ...defaultGameSpec(), title }));
  assert.throws(() => validateGameSpec({ ...defaultGameSpec(), player: { ...defaultGameSpec().player, color: 'url(secret)' } }));
  assert.throws(() => validateGameSpec({ ...defaultGameSpec(), theme: 'remote' }));
  assert.doesNotThrow(() => validateGameSpec({ ...defaultGameSpec(), description: '', instructions: '', player: { ...defaultGameSpec().player, color: '#AABBCC' } }));
});

test('validator copies nested data and cannot be used to mutate the input', () => {
  const original = defaultGameSpec(), copy = validateGameSpec(original);
  copy.player.speed = 8; copy.rules.goal = 20; copy.objects.hazard = 'boulder';
  assert.equal(original.player.speed, 5); assert.equal(original.rules.goal, 10); assert.equal(original.objects.hazard, 'rock');
});

test('collection collision increments score, removes the snack, and reaches the goal', () => {
  const engine = createGameEngine(), spec = defaultGameSpec(), state = engine.create(spec);
  state.score = spec.rules.goal - 1;
  state.entities = [{ id: 1, kind: 'snack', x: state.player.x, y: state.player.y, w: 22, h: 22, vx: 0, vy: 0 }];
  engine.step(state, spec, {}, 1 / 60);
  assert.equal(state.score, spec.rules.goal); assert.equal(state.outcome, 'won'); assert.equal(state.entities.length, 0);
  const elapsed = state.elapsed; engine.step(state, spec, { right: true }, 1 / 60); assert.equal(state.elapsed, elapsed);
});

test('hazards remove one life per invulnerability window and cannot multiply damage in one tick', () => {
  const engine = createGameEngine(), spec = defaultGameSpec(), state = engine.create(spec);
  state.invulnerable = 0;
  const hazard = { id: 10, kind: 'hazard' as const, x: state.player.x, y: state.player.y, w: 30, h: 30, vx: 0, vy: 0 };
  state.entities = [hazard, { ...hazard, id: 11 }];
  engine.step(state, spec, {}, 1 / 60); assert.equal(state.lives, 2);
  engine.step(state, spec, {}, 1 / 60); assert.equal(state.lives, 2);
  state.invulnerable = 0; state.lives = 1; engine.step(state, spec, {}, 1 / 60);
  assert.equal(state.lives, 0); assert.equal(state.outcome, 'lost');
});

test('timeout loses, movement stays in bounds, and simulation ignores invalid time', () => {
  const engine = createGameEngine(), spec = defaultGameSpec(), state = engine.create(spec);
  state.entities = [];
  for (let i = 0; i < 500; i++) engine.step(state, spec, { left: true, up: true }, 1 / 60);
  assert.equal(state.player.x, 0); assert.equal(state.player.y, 12);
  const before = structuredClone(state); engine.step(state, spec, {}, NaN); assert.deepEqual(state, before);
  state.elapsed = spec.rules.timeLimit - 0.001; engine.step(state, spec, {}, 1 / 60);
  assert.equal(state.outcome, 'lost'); assert.equal(state.elapsed, spec.rules.timeLimit);
});

test('platformer jumps, lands on elevated platforms, and requires releasing jump before jumping again', () => {
  const engine = createGameEngine(), spec = { ...defaultGameSpec(), genre: 'platformer' as const }, state = engine.create(spec);
  state.entities = []; state.player.x = 100;
  engine.step(state, spec, { jump: true }, 1 / 60); assert.ok(state.player.vy < 0);
  let landed = false;
  for (let i = 0; i < 120; i++) { engine.step(state, spec, { jump: true }, 1 / 60); if (state.player.grounded) { landed = true; break; } }
  assert.equal(landed, true); assert.equal(state.player.y + state.player.h, 420);
  engine.step(state, spec, { jump: true }, 1 / 60); assert.equal(state.player.vy, 0);
  engine.step(state, spec, {}, 1 / 60); engine.step(state, spec, { jump: true }, 1 / 60); assert.ok(state.player.vy < 0);
});

test('dodge mode spawns moving snacks and hazards and reaps missed objects', () => {
  const engine = createGameEngine(), spec = { ...defaultGameSpec(), genre: 'dodge' as const }, state = engine.create(spec);
  for (let i = 0; i < 120; i++) engine.step(state, spec, {}, 1 / 60);
  assert.ok(state.entities.some(entity => entity.kind === 'snack' && entity.vy > 0));
  assert.ok(state.entities.some(entity => entity.kind === 'hazard' && entity.vy > 0));
  state.entities.push({ id: 999, kind: 'hazard', x: 0, y: 700, w: 30, h: 30, vx: 0, vy: 10 });
  engine.step(state, spec, {}, 1 / 60); assert.equal(state.entities.some(entity => entity.id === 999), false);
});

test('identical validated specs and inputs produce deterministic simulations', () => {
  const engine = createGameEngine();
  for (const genre of ['collect', 'dodge', 'platformer'] as const) {
    const spec = { ...defaultGameSpec(), genre }, a = engine.create(spec), b = engine.create(spec);
    const run = (state: GameState) => { for (let i = 0; i < 300; i++) engine.step(state, spec, { right: i < 80, jump: i % 90 < 30 }, 1 / 60); };
    run(a); run(b); assert.deepEqual(a, b);
  }
});

test('sandbox document contains fixed executable code, restrictive CSP, and nonce/source checked messages', () => {
  const nonce = 'a1234567890b1234567890c1234567890d';
  const document = gameSandboxDocument(nonce);
  assert.match(document, /default-src 'none'/); assert.match(document, /connect-src 'none'/); assert.match(document, /form-action 'none'/); assert.match(document, /script-src 'nonce-/);
  assert.match(document, /event\.source\s*!==\s*parent/); assert.match(document, /event\.data\.nonce\s*!==\s*nonce/);
  assert.doesNotMatch(document, /\beval\s*\(|new Function\s*\(|\.innerHTML\s*=|localStorage|sessionStorage|https?:\/\//);
  assert.doesNotMatch(document, /Axolotl Pond Adventure/);
  const script = document.match(/<script nonce="[^"]+">([\s\S]*)<\/script>/)![1];
  assert.doesNotThrow(() => new Script(script));
  const messages: unknown[] = [];
  const listeners: Record<string, (event: { source: unknown; data: unknown }) => void> = {};
  const elements = new Map<string, Record<string, unknown>>();
  const element = (id: string) => {
    if (!elements.has(id)) elements.set(id, { textContent: '', disabled: true, addEventListener() {}, setAttribute() {}, getContext: () => ({}), focus() {} });
    return elements.get(id)!;
  };
  const parent = { postMessage(value: unknown) { messages.push(value); } };
  new Script(script).runInNewContext({ document: { getElementById: element, querySelectorAll: () => [], addEventListener() {} }, window: { addEventListener(name: string, fn: typeof listeners[string]) { listeners[name] = fn; } }, parent, requestAnimationFrame() {} });
  assert.deepEqual(JSON.parse(JSON.stringify(messages)), [{ type: 'academy-game-ready', channel: 'academy-game-studio', contract: 'game-studio-data-v2', nonce }]);
  listeners.message({ source: {}, data: { type: 'academy-game-load', channel: 'academy-game-studio', contract: 'game-studio-data-v2', nonce, spec: defaultGameSpec() } });
  assert.equal(elements.get('start')!.disabled, true);
  // Structured clone from another realm is what postMessage provides in a real browser.
  listeners.message({ source: parent, data: { type: 'academy-game-load', channel: 'academy-game-studio', contract: 'game-studio-data-v2', nonce: 'wrong', spec: defaultGameSpec() } });
  assert.equal(elements.get('start')!.disabled, true);
  assert.throws(() => gameSandboxDocument('bad\"><script>'), /safe random/);
});

function campaign(genre: GameSpec['genre'] = 'collect'): GameSpec {
  const spec = defaultGameSpec();
  return { ...spec, version: 2, genre, player: { ...spec.player, jump: 6, speed: 2 }, progression: 'Learn the controls, then practice with more obstacles.', levels: [
    { id: 'level-1', title: 'Getting started', brief: 'Find three treasures.', theme: 'pond', rules: { goal: 3, timeLimit: 60, lives: 5, difficulty: 1 }, seed: 1001 },
    { id: 'level-2', title: 'A little challenge', brief: 'Find six treasures with more obstacles.', theme: 'garden', rules: { goal: 6, timeLimit: 90, lives: 3, difficulty: 3 }, seed: 2027 },
  ] };
}

test('saved seeds reproduce every archetype and changing a seed changes its layout or spawn sequence', () => {
  const engine = createGameEngine();
  for (const genre of ['collect', 'dodge', 'platformer'] as const) {
    const spec = campaign(genre), different = structuredClone(spec); different.levels![0].seed++;
    const a = engine.create(spec), b = engine.create(spec), c = engine.create(different);
    for (let i = 0; i < 90; i++) for (const [state, source] of [[a, spec], [b, spec], [c, different]] as const) engine.step(state, source, { right: i < 30 }, 1 / 60);
    assert.deepEqual(a, b);
    assert.notDeepEqual(a.entities, c.entities);
    if (genre === 'platformer') assert.notDeepEqual(a.platforms, c.platforms);
  }
});

test('platform layouts remain reachable with minimum speed and jump across many seeds', () => {
  const engine = createGameEngine(), spec = campaign('platformer');
  for (let seed = 1; seed <= 100; seed++) {
    spec.levels![0].seed = seed;
    const initial = engine.create(spec);
    for (let i = 1; i < initial.platforms.length; i++) {
      const below = initial.platforms[i - 1], above = initial.platforms[i];
      assert.equal(below.y - above.y, 75);
      const left = Math.max(below.x, above.x), right = Math.min(below.x + below.w, above.x + above.w);
      assert.ok(right - left >= 50, `seed ${seed} has a safe overlap`);
      // Exercise the actual jump/collision solver, including its maximum step.
      for (const dt of [1 / 60, 1 / 20]) {
        const state = engine.create(spec); state.entities = [];
        state.player.x = left + (right - left - state.player.w) / 2;
        state.player.y = below.y - state.player.h; state.player.grounded = true;
        engine.step(state, spec, { jump: true }, dt);
        for (let frame = 0; frame < 100 && !state.player.grounded; frame++) engine.step(state, spec, {}, dt);
        assert.equal(state.player.y + state.player.h, above.y, `seed ${seed}, step ${dt}, platform ${i}`);
      }
    }
    for (const entity of initial.entities) assert.equal(engine.overlaps(initial.player, entity), false);
    for (let i = 0; i < initial.entities.length; i++) for (let j = i + 1; j < initial.entities.length; j++) assert.equal(engine.overlaps(initial.entities[i], initial.entities[j]), false);
  }
});

test('level transitions require winning and reset local score, time, and lives to that level rules', () => {
  const engine = createGameEngine(), spec = campaign(), first = engine.create(spec);
  assert.equal(first.lives, 5); assert.equal(engine.nextLevel(first, spec), undefined);
  first.outcome = 'lost'; assert.equal(engine.nextLevel(first, spec), undefined);
  first.outcome = 'won'; first.score = 3; first.lives = 1; first.elapsed = 40;
  const second = engine.nextLevel(first, spec)!;
  assert.equal(second.levelId, 'level-2'); assert.equal(second.levelIndex, 1);
  assert.equal(second.lives, 3); assert.equal(second.score, 0); assert.equal(second.elapsed, 0);
  second.outcome = 'won'; assert.equal(engine.nextLevel(second, spec), undefined);
  assert.deepEqual(engine.create(spec, 'level-2'), engine.create(spec, 1));
  assert.throws(() => engine.create(spec, 'level-8')); assert.throws(() => engine.create(spec, -1));
});

test('each level uses its own goals, lives, theme and time rather than the top-level defaults', () => {
  const engine = createGameEngine(), spec = campaign(), state = engine.create(spec, 1);
  assert.equal(engine.forLevel(spec, 1).theme, 'garden');
  state.score = 5; state.entities = [{ id: 100, kind: 'snack', x: state.player.x, y: state.player.y, w: 22, h: 22, vx: 0, vy: 0 }];
  engine.step(state, spec, {}, 1 / 60); assert.equal(state.outcome, 'won'); assert.equal(state.score, 6);
  const timeout = engine.create(spec, 1); timeout.entities = []; timeout.elapsed = 89.99;
  engine.step(timeout, spec, {}, 1 / 20); assert.equal(timeout.elapsed, 90); assert.equal(timeout.outcome, 'lost');
});

test('legacy games retain their original single-level seed and platform geometry', () => {
  const engine = createGameEngine(), spec = { ...defaultGameSpec(), genre: 'platformer' as const }, state = engine.create(spec);
  assert.deepEqual(state.platforms, [{ x: 0, y: 500, w: 960, h: 40 }, { x: 40, y: 420, w: 270, h: 16 }, { x: 250, y: 340, w: 270, h: 16 }, { x: 460, y: 260, w: 270, h: 16 }, { x: 650, y: 180, w: 270, h: 16 }, { x: 430, y: 100, w: 270, h: 16 }]);
  assert.deepEqual(engine.getLevels(spec)[0].seed, 17031);
  state.outcome = 'won'; assert.equal(engine.nextLevel(state, spec), undefined);
});

test('parent result validation bounds aggregate values and prevents isolated or partial campaign wins', () => {
  const spec = campaign();
  const result: GameResult = { outcome: 'won', elapsed: 150, score: 9, reviewedLevelIds: ['level-1', 'level-2'] };
  assert.deepEqual(validateGameResult(result, spec), result);
  for (const change of [
    { elapsed: 151 }, { elapsed: NaN }, { score: 10 }, { score: 8 }, { score: 1.5 }, { score: -1 }, { outcome: 'level-complete' },
    { reviewedLevelIds: [] }, { reviewedLevelIds: ['level-1'] }, { reviewedLevelIds: ['level-1', 'level-1'] }, { reviewedLevelIds: ['level-2', 'level-1'] }, { reviewedLevelIds: ['level-9'] },
  ]) assert.equal(validateGameResult({ ...result, ...change }, spec), undefined);
  const selected = { outcome: 'level-complete', elapsed: 90, score: 6, reviewedLevelIds: ['level-2'] };
  assert.deepEqual(validateGameResult(selected, spec, 'level-2'), selected);
  assert.equal(validateGameResult({ ...selected, outcome: 'won' }, spec, 'level-2'), undefined);
  assert.equal(validateGameResult({ ...selected, reviewedLevelIds: ['level-1'] }, spec, 'level-2'), undefined);
  assert.equal(validateGameResult({ ...selected, elapsed: 91 }, spec, 'level-2'), undefined);
  assert.equal(validateGameResult(selected, spec), undefined);
});

// Round physics are exercised above. This harness isolates the browser's campaign
// orchestration so transitions, callbacks, and review totals do not need a timed game.
function sandboxHarness(spec: GameSpec, _levelId?: string, finish: 'won' | 'lost' = 'won') {
  type Element = { textContent: string; disabled: boolean; hidden: boolean; handlers: Record<string, () => void>; addEventListener: (name: string, fn: () => void) => void; setAttribute(): void; getContext(): unknown; focus(): void };
  const elements = new Map<string, Element>(), messages: { type: string; result?: GameResult }[] = [], frames: ((now: number) => void)[] = [];
  const getElement = (id: string) => {
    if (!elements.has(id)) { const item: Element = { textContent: '', disabled: true, hidden: ['next', 'restart'].includes(id), handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; }, setAttribute() {}, getContext: () => new Proxy({}, { get: () => () => {} }), focus() {} }; elements.set(id, item); }
    return elements.get(id)!;
  };
  const listeners: Record<string, (event: unknown) => void> = {}, parent = { postMessage(value: { type: string; result?: GameResult }) { messages.push(JSON.parse(JSON.stringify(value))); } };
  const actual = createGameEngine(), engine = { ...actual, step(state: ReturnType<typeof actual.create>, source: GameSpec) { state.elapsed = 3; state.score = actual.forLevel(source, state.levelIndex).rules.goal; state.outcome = finish; return state; } };
  new Script(`const __name = fn => fn; (${startGameSandbox.toString()})(engine, validate, '1234567890abcdef');`).runInNewContext({
    engine, validate: (value: GameSpec) => value, parent,
    document: { getElementById: getElement, querySelectorAll: () => [], addEventListener() {} },
    window: { addEventListener(name: string, fn: typeof listeners[string]) { listeners[name] = fn; } },
    requestAnimationFrame(fn: (now: number) => void) { frames.push(fn); },
  });
  listeners.message({ source: parent, data: { type: 'academy-game-load', channel: 'academy-game-studio', contract: 'game-studio-data-v2', nonce: '1234567890abcdef', spec } });
  let clock = 1000;
  const tick = () => { const frame = frames.shift(); assert.ok(frame); frame!(clock += 1000 / 30); };
  const click = (id: string) => getElement(id).handlers.click();
  const complete = () => { const count = messages.filter(message => message.result).length; for (let i = 0; i < 5 && messages.filter(message => message.result).length === count; i++) tick(); };
  return { elements, messages, tick, click, complete };
}

test('campaign player emits per-level reviews, advances explicitly, totals results, and restarts cleanly', () => {
  const harness = sandboxHarness(campaign());
  harness.click('start'); harness.complete();
  let results = harness.messages.filter(message => message.result).map(message => message.result!);
  assert.deepEqual(results, [{ outcome: 'level-complete', elapsed: 3, score: 3, reviewedLevelIds: ['level-1'] }]);
  assert.equal(harness.elements.get('next')!.hidden, false);
  harness.click('next'); harness.complete();
  results = harness.messages.filter(message => message.result).map(message => message.result!);
  assert.deepEqual(results[1], { outcome: 'won', elapsed: 6, score: 9, reviewedLevelIds: ['level-1', 'level-2'] });
  assert.equal(harness.elements.get('next')!.hidden, true);
  harness.click('restart'); harness.complete();
  assert.deepEqual(harness.messages.at(-1)!.result, results[0]);
});

test('losing a level reports its review but leaves the next level locked', () => {
  const harness = sandboxHarness(campaign(), undefined, 'lost');
  harness.click('start'); harness.complete();
  assert.equal(harness.messages.at(-1)!.result!.outcome, 'lost');
  assert.equal(harness.elements.get('next')!.hidden, true);
});

test('serialized multi-level player remains fixed and valid JavaScript with the restrictive CSP', () => {
  const html = gameSandboxDocument('1234567890abcdef');
  assert.match(html, /connect-src 'none'/); assert.match(html, /default-src 'none'/);
  assert.doesNotMatch(html, /Getting started|A little challenge|Learn the controls/);
  const script = html.match(/<script nonce="[^"]+">([\s\S]*)<\/script>/)![1];
  assert.doesNotThrow(() => new Script(script));
});


test('level instructions retain authored briefs and always state the actual current objective', () => {
  const spec = campaign(); spec.levels![0].brief = 'Collect five treasures to learn the controls.';
  spec.levels![0].rules.goal = 7;
  const harness = sandboxHarness(spec);
  const objective = 'Objective: Collect 7 water snacks in 60 seconds. 5 lives. Avoid rocks.';
  assert.equal(harness.elements.get('instruction')!.textContent, `${spec.levels![0].brief} ${objective}`);
  harness.click('start'); assert.match(harness.elements.get('instruction')!.textContent, /Collect 7 water snacks/);
  harness.complete(); harness.click('next');
  assert.match(harness.elements.get('instruction')!.textContent, /Objective: Collect 6 water snacks in 90 seconds. 3 lives. Avoid rocks./);
  const legacy = defaultGameSpec(); legacy.instructions = 'My original instructions, exactly as written.';
  const old = sandboxHarness(legacy); assert.equal(old.elements.get('instruction')!.textContent, legacy.instructions);
  old.click('start'); assert.equal(old.elements.get('instruction')!.textContent, legacy.instructions);
});

function arcade(change: Partial<NonNullable<GameSpec['arcade']>> = {}): GameSpec {
  return { ...defaultGameSpec(), genre: 'dodge', theme: 'space', arcade: { rotation: true, shooting: true, splitAsteroids: true, scoring: true, highScores: true, ...change } };
}
function clear(state: GameState) { state.entities = []; state.nextSnack = 999; state.nextHazard = 999; state.invulnerable = 99; }
const rock = (id = 10) => ({ id, kind: 'hazard' as const, asteroid: 'large' as const, x: 350, y: 290, w: 42, h: 42, vx: 0, vy: 0 });

test('rotation turns freely through a full circle, with forward and reverse thrust along its nose', () => {
  const engine = createGameEngine(), spec = arcade(), state = engine.create(spec); clear(state);
  const initial = { ...state.player };
  for (let i = 0; i < 180; i++) engine.step(state, spec, { right: true }, 1 / 60);
  assert.deepEqual(state.player, initial);
  const expected = ((-Math.PI / 2 + 180 * 2.8 / 60) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
  assert.ok(Math.abs(state.angle! - expected) < 1e-10);
  assert.ok(state.angle! >= 0 && state.angle! < Math.PI * 2);
  state.angle = 0;
  engine.step(state, spec, { up: true }, 1 / 20); assert.ok(state.player.x > initial.x); assert.equal(state.player.y, initial.y);
  engine.step(state, spec, { down: true }, 1 / 20); assert.ok(Math.abs(state.player.x - initial.x) < 1e-10);
  const oldAngle = state.angle!; engine.step(state, spec, { left: true }, 1 / 20); assert.ok(state.angle! > oldAngle);
});

test('shooting and rotation are independent options and disabled movement remains direct', () => {
  const engine = createGameEngine(), spec = arcade({ rotation: false }), state = engine.create(spec); clear(state);
  const x = state.player.x; engine.step(state, spec, { right: true, fire: true }, 1 / 60);
  assert.ok(state.player.x > x); assert.equal(state.bullets!.length, 1); assert.ok(Math.abs(state.bullets![0].vx) < 1e-10); assert.ok(state.bullets![0].vy < 0);
  const noShots = arcade({ shooting: false, splitAsteroids: false }), peaceful = engine.create(noShots); clear(peaceful);
  engine.step(peaceful, noShots, { fire: true }, 1 / 60); assert.equal(peaceful.bullets, undefined);
});

test('one swept shot splits a large asteroid into exactly two small pieces and each takes one further shot', () => {
  const engine = createGameEngine(), spec = arcade(), state = engine.create(spec); clear(state); state.nextId = 100;
  state.entities = [rock()]; state.bullets = [{ id: 50, x: 300, y: 310, vx: 2000, vy: 0, ttl: 1 }];
  engine.step(state, spec, {}, 1 / 20);
  assert.equal(state.entities.length, 2); assert.ok(state.entities.every(entity => entity.asteroid === 'small' && entity.w === 20));
  assert.equal(state.bullets.length, 0); assert.equal(state.score, 0); assert.equal(state.points, 20);
  assert.ok(state.entities.every(entity => entity.grace! > 0)); assert.notEqual(state.entities[0].vx, state.entities[1].vx);
  state.bullets = state.entities.map((entity, index) => ({ id: 60 + index, x: entity.x + entity.w / 2, y: entity.y + entity.h / 2, vx: 0, vy: 0, ttl: 1 }));
  engine.step(state, spec, {}, 1 / 60);
  assert.equal(state.entities.length, 0); assert.equal(state.points, 120); assert.equal(state.score, 0);
});

test('unsplit asteroids take one shot; overlapping bullets never award the same hit twice', () => {
  const engine = createGameEngine(), spec = arcade({ splitAsteroids: false }), state = engine.create(spec); clear(state);
  state.entities = [rock()]; state.bullets = [1, 2].map(id => ({ id, x: 300, y: 310, vx: 2000, vy: 0, ttl: 1 }));
  engine.step(state, spec, {}, 1 / 20);
  assert.equal(state.entities.length, 0); assert.equal(state.points, 20); assert.equal(state.bullets.length, 1);
  const noPoints = arcade({ scoring: false }), disabled = engine.create(noPoints); clear(disabled);
  disabled.entities = [rock()]; disabled.bullets = [{ id: 99, x: 300, y: 310, vx: 2000, vy: 0, ttl: 1 }];
  engine.step(disabled, noPoints, {}, 1 / 20); assert.equal(disabled.points, undefined); assert.equal(disabled.entities.length, 2);
});

test('swept collisions use relative asteroid motion and choose the nearest obstacle without tunneling', () => {
  const engine = createGameEngine(), spec = arcade({ splitAsteroids: false }), state = engine.create(spec); clear(state);
  state.entities = [{ ...rock(10), x: 380, w: 5, vx: -1800 }, { ...rock(11), x: 430 }];
  state.bullets = [{ id: 99, x: 300, y: 310, vx: 2000, vy: 0, ttl: 1 }];
  engine.step(state, spec, {}, 1 / 20);
  assert.deepEqual(state.entities.map(entity => entity.id), [11]); assert.equal(state.points, 20);
  assert.equal(engine.sweptHit(0, 0, 100, 0, { ...rock(), x: 50, y: 20 }), undefined);
});

test('point tracking keeps collection goals separate and prevents excess last-frame collectibles inflating points', () => {
  const engine = createGameEngine(), spec = arcade(), state = engine.create(spec); clear(state); state.score = spec.rules.goal - 1;
  state.entities = [1, 2, 3].map(id => ({ id, kind: 'snack', x: state.player.x, y: state.player.y, w: 22, h: 22, vx: 0, vy: 0 }));
  engine.step(state, spec, {}, 1 / 60);
  assert.equal(state.score, spec.rules.goal); assert.equal(state.points, 10); assert.equal(state.outcome, 'won');
  const before = structuredClone(state); engine.step(state, spec, { fire: true }, 1 / 60); assert.deepEqual(state, before);
});

test('bullets have a firing cooldown and bounded lifetime, and seeded fragments are repeatable', () => {
  const engine = createGameEngine(), spec = arcade(), state = engine.create(spec); clear(state);
  let peak = 0;
  for (let i = 0; i < 1200; i++) { engine.step(state, spec, { fire: true, right: true }, 1 / 60); peak = Math.max(peak, state.bullets!.length); }
  assert.ok(peak > 1 && peak <= 8); assert.ok(state.nextId < 110);
  for (let i = 0; i < 120; i++) engine.step(state, spec, {}, 1 / 60);
  assert.equal(state.bullets!.length, 0);
  const a = engine.create(spec), b = engine.create(spec);
  for (const item of [a, b]) { clear(item); item.entities = [rock()]; item.bullets = [{ id: 99, x: 300, y: 310, vx: 2000, vy: 0, ttl: 1 }]; engine.step(item, spec, {}, 1 / 20); }
  assert.deepEqual(a, b);
});

test('arcade reserves fragment capacity and keeps long-running spawn/entity counts bounded', () => {
  const engine = createGameEngine(), spec = arcade(), state = engine.create(spec); clear(state);
  state.entities = Array.from({ length: 63 }, (_, i) => ({ ...rock(i + 100), x: 100 + i, y: 10, vx: 0, vy: 0 }));
  state.entities.push({ id: 999, kind: 'snack', x: 10, y: 10, w: 22, h: 22, vx: 0, vy: 0 });
  state.nextHazard = 0; engine.step(state, spec, {}, 1 / 60);
  assert.equal(state.entities.length, 64); // 127 weighted slots leaves no room for a large rock.
  state.bullets = [{ id: 1000, x: 90, y: 20, vx: 2000, vy: 0, ttl: 1 }];
  engine.step(state, spec, {}, 1 / 20);
  assert.equal(state.entities.length, 65); assert.equal(state.entities.filter(entity => entity.asteroid === 'small').length, 2);
  assert.ok(state.entities.reduce((sum, entity) => sum + (entity.kind === 'hazard' && entity.asteroid !== 'small' ? 2 : 1), 0) <= 128);
});

test('result validation bounds points per reviewed level and legacy games never acquire optional score fields', () => {
  const engine = createGameEngine(), spec = arcade();
  const result = { outcome: 'won', elapsed: 5, score: spec.rules.goal, reviewedLevelIds: ['level-1'], points: 120 };
  assert.deepEqual(validateGameResult(result, spec), result);
  const cap = spec.rules.goal * 10 + (Math.ceil(spec.rules.timeLimit / 0.5) + 8) * 120;
  assert.equal(engine.maxPoints(spec), cap);
  for (const points of [undefined, -1, 0.1, Infinity, NaN, cap + 1]) assert.equal(validateGameResult({ ...result, points }, spec), undefined);
  assert.equal(validateGameResult({ ...result, points: cap }, spec)?.points, cap);
  const legacy = defaultGameSpec(), plain = { ...result }; delete (plain as { points?: number }).points;
  assert.equal(engine.maxPoints(legacy), 0); assert.deepEqual(validateGameResult(plain, legacy), plain); assert.equal(validateGameResult(result, legacy), undefined);
  const state = engine.create(legacy); assert.equal('points' in state, false); assert.equal('bullets' in state, false); assert.equal('angle' in state, false);
  const campaign: GameSpec = { ...spec, version: 2, progression: 'Learn then explore.', levels: [1, 2].map((id) => ({ id: `level-${id}`, title: `Level ${id}`, brief: 'Collect snacks.', theme: 'space', rules: { ...spec.rules, timeLimit: id * 30 }, seed: id })) };
  const selected = { ...result, outcome: 'level-complete', reviewedLevelIds: ['level-2'] };
  assert.equal(engine.maxPoints(campaign, ['level-2']), cap);
  assert.deepEqual(validateGameResult(selected, campaign, 'level-2'), selected);
  assert.equal(validateGameResult({ ...selected, points: cap + 1 }, campaign, 'level-2'), undefined);
  assert.equal(validateGameResult({ ...selected, outcome: 'won' }, campaign, 'level-2'), undefined);
});

function harness(spec: GameSpec) {
  type Handler = (event: Record<string, unknown>) => void;
  type Element = { textContent: string; disabled: boolean; hidden: boolean; dataset: { control?: string }; attributes: Record<string, string>; handlers: Record<string, Handler>; addEventListener(name: string, fn: Handler): void; setAttribute(name: string, value: string): void; getContext(): unknown; focus(): void; setPointerCapture(): void };
  const elements = new Map<string, Element>(), messages: { type: string; result?: GameResult }[] = [], frames: ((now: number) => void)[] = [], documentHandlers: Record<string, Handler> = {}, windowHandlers: Record<string, Handler> = {}, recorded: unknown[] = [];
  const element = (id: string) => {
    if (!elements.has(id)) elements.set(id, { textContent: '', disabled: true, hidden: false, dataset: ['left', 'right', 'up', 'down', 'fire', 'jump'].includes(id) ? { control: id } : {}, attributes: {}, handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; }, setAttribute(name, value) { this.attributes[name] = value; }, getContext: () => new Proxy({}, { get: () => () => {} }), focus() {}, setPointerCapture() {} });
    return elements.get(id)!;
  };
  const controls = ['left', 'right', 'up', 'down', 'fire', 'jump'].map(element), parent = { postMessage(value: { type: string; result?: GameResult }) { messages.push(JSON.parse(JSON.stringify(value))); } };
  const actual = createGameEngine(), engine = { ...actual, step(state: GameState, source: GameSpec, input: unknown) { recorded.push(input); state.elapsed = 3; state.score = actual.forLevel(source, state.levelIndex).rules.goal; state.points = (state.levelIndex + 1) * 120; state.outcome = 'won'; return state; } };
  new Script(`const __name = fn => fn; (${startGameSandbox.toString()})(engine, validate, '1234567890abcdef');`).runInNewContext({ engine, validate: (value: GameSpec) => value, parent, document: { getElementById: element, querySelectorAll: () => controls, addEventListener(name: string, fn: Handler) { documentHandlers[name] = fn; } }, window: { addEventListener(name: string, fn: Handler) { windowHandlers[name] = fn; } }, requestAnimationFrame(fn: (now: number) => void) { frames.push(fn); } });
  windowHandlers.message({ source: parent, data: { type: 'academy-game-load', channel: 'academy-game-studio', contract: 'game-studio-data-v2', nonce: '1234567890abcdef', spec } });
  let now = 1000; const tick = () => frames.shift()!(now += 1000 / 30);
  const click = (id: string) => element(id).handlers.click({});
  const complete = () => { const count = messages.filter(message => message.result).length; for (let i = 0; i < 5 && count === messages.filter(message => message.result).length; i++) tick(); };
  return { element, messages, tick, click, complete, documentHandlers, windowHandlers, recorded };
}

test('campaign points replace restarted levels instead of accumulating and all-game restart clears totals', () => {
  const single = arcade(), spec: GameSpec = { ...single, version: 2, progression: 'Learn then explore.', levels: [1, 2].map((id) => ({ id: `level-${id}`, title: `Level ${id}`, brief: 'Collect snacks.', theme: 'space', rules: { ...single.rules }, seed: id })) };
  const game = harness(spec); game.click('start'); game.complete();
  assert.equal(game.messages.at(-1)!.result!.points, 120);
  game.click('start'); game.complete(); assert.equal(game.messages.at(-1)!.result!.points, 120);
  game.click('next'); game.complete(); assert.equal(game.messages.at(-1)!.result!.points, 360);
  game.click('start'); game.complete(); assert.equal(game.messages.at(-1)!.result!.points, 360);
  game.click('restart'); game.complete(); assert.equal(game.messages.at(-1)!.result!.points, 120);
});

test('sandbox renders safe fixed Fire controls and appropriate rotation, shooting, and scoring help', () => {
  const game = harness(arcade());
  assert.equal(game.element('fire').hidden, false); assert.equal(game.element('jump').hidden, true);
  assert.match(game.element('controls-help').textContent, /360°/); assert.match(game.element('controls-help').textContent, /Space or Z/); assert.match(game.element('controls-help').textContent, /two small pieces/);
  assert.equal(game.element('left').attributes['aria-label'], 'Turn left'); assert.equal(game.element('up').attributes['aria-label'], 'Thrust forward');
  game.click('start');
  game.documentHandlers.keydown({ key: 'z', target: {}, preventDefault() {} }); game.complete();
  assert.equal((game.recorded[0] as { fire: boolean }).fire, true);
  game.click('start'); game.complete(); assert.equal((game.recorded.at(-1) as { fire: boolean }).fire, false);
  const html = gameSandboxDocument('1234567890abcdef');
  assert.match(html, /data-control="fire"/); assert.match(html, /connect-src 'none'/);
  assert.doesNotMatch(html, /localStorage|sessionStorage|https?:\/\//);
  assert.doesNotThrow(() => new Script(html.match(/<script nonce="[^"]+">([\s\S]*)<\/script>/)![1]));
});

test('pause, window blur, and touch cancellation release firing input before resuming', () => {
  const game = harness(arcade()); game.click('start');
  game.documentHandlers.keydown({ key: 'z', target: {}, preventDefault() {} });
  game.click('pause'); game.click('pause'); game.complete();
  assert.equal((game.recorded.at(-1) as { fire: boolean }).fire, false);
  game.click('start');
  game.documentHandlers.keydown({ key: ' ', target: {}, preventDefault() {} });
  game.windowHandlers.blur({}); assert.equal(game.element('pause').textContent, 'Resume');
  game.click('pause'); game.complete(); assert.equal((game.recorded.at(-1) as { fire: boolean }).fire, false);
  game.click('start');
  game.element('fire').handlers.pointerdown({ pointerId: 1, preventDefault() {} }); game.complete();
  assert.equal((game.recorded.at(-1) as { fire: boolean }).fire, true);
  game.click('start');
  game.element('fire').handlers.pointerdown({ pointerId: 2, preventDefault() {} }); game.element('fire').handlers.pointercancel({}); game.complete();
  assert.equal((game.recorded.at(-1) as { fire: boolean }).fire, false);
});

test('import retains authored text, shuffled level identifiers, layout seeds and inactive highScores intent exactly', () => {
  const spec = campaign('dodge');
  spec.arcade = { rotation: true, shooting: true, splitAsteroids: true, scoring: true, highScores: true };
  spec.title = 'Authored lesson title'; spec.instructions = 'The creator’s original instructions.';
  spec.levels![0].id = 'level-7'; spec.levels![1].id = 'level-2';
  const clean = validateGameSpec(spec);
  assert.deepEqual(clean, spec);
  assert.deepEqual(createGameEngine().getLevels(clean).map(level => [level.id, level.seed]), [['level-7', 1001], ['level-2', 2027]]);
  clean.levels![0].rules.goal = 5;
  assert.equal(spec.levels![0].rules.goal, 3);
});

test('v2 data rejects unexpected code, invalid seeds, oversized campaigns, duplicated levels and arcade combinations', () => {
  const spec = campaign('dodge');
  for (const change of [
    { levels: [...spec.levels!, spec.levels![0]] },
    { levels: Array.from({ length: 9 }, (_, index) => ({ ...spec.levels![0], id: `level-${index + 1}` })) },
    { levels: [{ ...spec.levels![0], seed: 0 }] },
    { levels: [{ ...spec.levels![0], seed: 2147483648 }] },
    { levels: [{ ...spec.levels![0], seed: 1.5 }] },
    { levels: [{ ...spec.levels![0], brief: 'import(secret)' }] },
    { levels: [{ ...spec.levels![0], title: 'https://example.invalid' }] },
    { levels: [{ ...spec.levels![0], html: '<iframe>' }] },
    { arcade: { rotation: true, shooting: false, splitAsteroids: true, scoring: true, highScores: true } },
    { arcade: { rotation: true, shooting: true, splitAsteroids: true, scoring: false, highScores: true } },
  ]) assert.throws(() => validateGameSpec({ ...spec, ...change }));
  assert.throws(() => validateGameSpec(Object.create({ ...defaultGameSpec() })));
});

test('opaque player messages require exact source, null origin, nonce, channel, contract and shape', () => {
  const spec = defaultGameSpec(), source = {}, nonce = '1234567890abcdef';
  const data = { type: 'academy-game-ready', channel: 'academy-game-studio', contract: GAME_STUDIO_RUNTIME_CONTRACT, nonce };
  const event = { source, origin: 'null', data };
  assert.deepEqual(readGameStudioMessage(event, source, nonce, spec), { type: data.type });
  for (const change of [
    { source: {} }, { origin: 'https://game.example' }, { origin: '' },
    { data: { ...data, nonce: 'stale-instance' } }, { data: { ...data, channel: 'another-app' } },
    { data: { ...data, contract: 'game-studio-data-v9' } }, { data: { ...data, access_token: 'forbidden' } },
    { data: { ...data, type: 'academy-game-grade' } }, { data: [] }, { data: null },
  ]) assert.equal(readGameStudioMessage({ ...event, ...change }, source, nonce, spec), undefined);
  assert.equal(readGameStudioMessage({ ...event, source: undefined }, undefined, nonce, spec), undefined);
  const result = { outcome: 'won', elapsed: 4, score: spec.rules.goal, reviewedLevelIds: ['level-1'] };
  assert.deepEqual(readGameStudioMessage({ ...event, data: { ...data, type: 'academy-game-result', result } }, source, nonce, spec), { type: 'academy-game-result', result });
  for (const extra of [{ nickname: 'secret' }, { grade: 100 }, { account: 'secret' }, { points: 10 }]) {
    assert.equal(readGameStudioMessage({ ...event, data: { ...data, type: 'academy-game-result', result: { ...result, ...extra } } }, source, nonce, spec), undefined);
  }
});

test('rendered player allows scripts only and has no service URL, login, score board or persisted progress', () => {
  const html = renderToStaticMarkup(<GameStudioPlayer spec={defaultGameSpec()} />);
  assert.match(html, /sandbox="allow-scripts"/);
  assert.match(html, /referrerPolicy="no-referrer"/);
  assert.match(html, /camera &#x27;none&#x27;/);
  assert.match(html, /does not submit grades or save progress/);
  assert.doesNotMatch(html, /allow-same-origin|allow-forms|allow-popups|allow-top-navigation|localStorage|sessionStorage|nickname|leaderboard|https?:\/\//);
  assert.match(html, new RegExp(GAME_STUDIO_RUNTIME_DIGEST));
  const invalid = { ...defaultGameSpec(), title: '<script>alert(1)</script>' };
  const rejected = renderToStaticMarkup(<GameStudioPlayer spec={invalid} />);
  assert.match(rejected, /role="alert"/);
  assert.doesNotMatch(rejected, /<iframe/);
});

test('runtime build identity records exact standalone source and bounded fixed implementation inventory', () => {
  expect(provenance.source_commit).toBe('f09860b9973b0359ea36684aad66618bf7a67bbf');
  expect(provenance.contract).toBe(GAME_STUDIO_RUNTIME_CONTRACT);
  expect(provenance.implementation_sha256).toBe(GAME_STUDIO_RUNTIME_DIGEST);
  expect(Object.keys(provenance.implementation_files)).toHaveLength(5);
  expect(provenance.source_files['src/lib/game-runtime.ts'].git_blob).toBe('bc5b6a7e0f1695b22beac5485ebff5c90d2b6586');
});


test('serialized sandbox admits one exact parent snapshot and rejects extra fields, other channels and replacement data', () => {
  const nonce = '1234567890abcdef', messages: unknown[] = [], listeners: Record<string, (event: unknown) => void> = {};
  const elements = new Map<string, { textContent: string; disabled: boolean; addEventListener(): void; setAttribute(): void; getContext(): unknown; focus(): void }>();
  const element = (id: string) => {
    if (!elements.has(id)) elements.set(id, { textContent: '', disabled: true, addEventListener() {}, setAttribute() {}, getContext: () => ({}), focus() {} });
    return elements.get(id)!;
  };
  const parent = { postMessage(value: unknown) { messages.push(value); } };
  const context = createContext({ document: { getElementById: element, querySelectorAll: () => [], addEventListener() {} }, window: { addEventListener(name: string, fn: typeof listeners[string]) { listeners[name] = fn; } }, parent, requestAnimationFrame() {} });
  const script = gameSandboxDocument(nonce).match(/<script nonce="[^"]+">([\s\S]*)<\/script>/)![1];
  new Script(script).runInContext(context);
  const inFrame = (value: unknown) => new Script(`JSON.parse(${JSON.stringify(JSON.stringify(value))})`).runInContext(context);
  const data = { type: 'academy-game-load', channel: 'academy-game-studio', contract: 'game-studio-data-v2', nonce, spec: defaultGameSpec() };
  for (const bad of [{ ...data, levelId: 'level-1' }, { ...data, nonce: 'wrong' }, { ...data, contract: 'other' }, { ...data, channel: 'other' }, { ...data, spec: { ...defaultGameSpec(), title: 'fetch(secret)' } }]) {
    listeners.message({ source: parent, data: inFrame(bad) });
    assert.equal(element('start').disabled, true);
  }
  listeners.message({ source: {}, data: inFrame(data) });
  assert.equal(element('start').disabled, true);
  listeners.message({ source: parent, data: inFrame(data) });
  assert.equal(element('start').disabled, false);
  assert.equal(element('title').textContent, data.spec.title);
  listeners.message({ source: parent, data: inFrame({ ...data, spec: { ...data.spec, title: 'Replacement' } }) });
  assert.equal(element('title').textContent, data.spec.title);
  assert.equal(messages.length, 2);
});

test('approval runtime digest is recomputed from the exact fixed implementation bytes', () => {
  const hash = (value: string | Uint8Array) => createHash('sha256').update(value).digest('hex');
  const inventory = Object.entries(provenance.implementation_files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  for (const [path, expected] of inventory) expect(hash(readFileSync(new URL(path, import.meta.url)))).toBe(expected);
  expect(hash(inventory.map(([path, digest]) => `${path}\0${digest}\n`).join(''))).toBe(GAME_STUDIO_RUNTIME_DIGEST);
});
