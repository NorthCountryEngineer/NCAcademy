import { afterEach, expect, it, vi } from 'vitest';
import { createElement, isValidElement, type ReactElement, type ReactNode } from 'react';
// @ts-expect-error Node built-ins run in Vitest; this browser package has no Node type dependency.
import { createHash } from 'node:crypto';
// @ts-expect-error Node built-ins run in Vitest; this browser package has no Node type dependency.
import { readFileSync } from 'node:fs';
import { AcademyGameDisplay } from './AcademyGameDisplay';
import { createPlayerDisplay, playerDisplayShortcut, type PlayerDisplayState } from './game-display-controller';
import provenance from './game-studio-runtime/provenance.json';
import { GAME_STUDIO_RUNTIME_CONTRACT, GAME_STUDIO_RUNTIME_DIGEST } from './game-studio-runtime/runtime-build';

const hooks = vi.hoisted(() => ({ states: [] as unknown[], dependencies: [] as (readonly unknown[] | undefined)[], index: 0, effectIndex: 0, pending: [] as (() => void)[], cleanups: [] as ((() => void) | undefined)[] }));
vi.mock('react', async original => ({ ...await original<typeof import('react')>(),
  useState: (initial: unknown) => { const index = hooks.index++; if (index >= hooks.states.length) hooks.states.push(typeof initial === 'function' ? initial() : initial); return [hooks.states[index], (value: unknown) => { hooks.states[index] = typeof value === 'function' ? value(hooks.states[index]) : value; }]; },
  useRef: (initial: unknown) => { const index = hooks.index++; if (index >= hooks.states.length) hooks.states.push({ current: initial }); return hooks.states[index]; },
  useEffect: (effect: () => void | (() => void), dependencies?: readonly unknown[]) => { const index = hooks.effectIndex++, before = hooks.dependencies[index]; if (!before || !dependencies || dependencies.length !== before.length || dependencies.some((value, i) => value !== before[i])) { hooks.dependencies[index] = dependencies; hooks.pending.push(() => { hooks.cleanups[index]?.(); const cleanup = effect(); hooks.cleanups[index] = typeof cleanup === 'function' ? cleanup : undefined; }); } },
}));
afterEach(() => { hooks.cleanups.forEach(cleanup => cleanup?.()); hooks.states = []; hooks.dependencies = []; hooks.pending = []; hooks.cleanups = []; hooks.index = hooks.effectIndex = 0; });

function fixture(native = true) {
  const listeners = new Map<string, Set<(event: unknown) => void>>(), frameFocus = vi.fn(), returnFocus = vi.fn();
  const control = { isConnected: true, focus: returnFocus }, frame = { focus: frameFocus, contentWindow: { retainedGameState: { score: 18 } } };
  const doc = { body: { style: { overflow: 'scroll' } }, activeElement: control, fullscreenElement: null as unknown, fullscreenEnabled: native,
    addEventListener: (name: string, listener: (event: unknown) => void) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name)!.add(listener); },
    removeEventListener: (name: string, listener: (event: unknown) => void) => { listeners.get(name)?.delete(listener); },
    exitFullscreen: vi.fn(async () => { doc.fullscreenElement = null; emit('fullscreenchange', {}); }),
  };
  const root = { ownerDocument: doc, contains: (target: unknown) => target === control || target === frame, querySelector: () => frame,
    requestFullscreen: native ? vi.fn(async () => { doc.fullscreenElement = root; emit('fullscreenchange', {}); }) : undefined,
  };
  function emit(name: string, event: unknown) { for (const listener of listeners.get(name) ?? []) listener(event); }
  const key = (value: string, target: unknown = control) => { const event = { key: value, target, repeat: false, isComposing: false, ctrlKey: false, metaKey: false, altKey: false, defaultPrevented: false, preventDefault: vi.fn() }; emit('keydown', event); return event; };
  return { root: root as unknown as HTMLDivElement, frame: frame as unknown as HTMLIFrameElement, control: control as unknown as HTMLButtonElement, doc, emit, key, frameFocus, returnFocus, listeners };
}
type Node = ReactElement<{ children?: ReactNode; ref?: { current: unknown }; onClick?: () => void; disabled?: boolean; 'data-display-mode'?: string }>;
function nodes(node: ReactNode): Node[] { if (Array.isArray(node)) return node.flatMap(nodes); return isValidElement<{ children?: ReactNode }>(node) ? [node as Node, ...nodes(node.props.children)] : []; }
function text(node: ReactNode): string { if (Array.isArray(node)) return node.map(text).join(''); return isValidElement<{ children?: ReactNode }>(node) ? text(node.props.children) : typeof node === 'string' ? node : ''; }
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

it('changes wrapper display without replacing its child, iframe state, or fixed player', async () => {
  const screen = fixture(), child = createElement('iframe', { title: 'Existing approved game' });
  const render = () => { hooks.index = hooks.effectIndex = 0; return AcademyGameDisplay({ children: child }); };
  let tree = render();
  tree.props.ref.current = screen.root;
  nodes(tree).find(node => node.type === 'button')!.props.ref!.current = screen.control;
  hooks.pending.splice(0).forEach(effect => effect());
  expect(tree.props['data-display-mode']).toBe('windowed');
  nodes(tree).find(node => node.type === 'button')!.props.onClick!(); await settle(); tree = render();
  expect(tree.props['data-display-mode']).toBe('fullscreen'); expect(text(tree)).toContain('Exit full screen');
  expect(nodes(tree).find(node => node.type === 'iframe')).toBe(child);
  expect(screen.frame.contentWindow).toEqual({ retainedGameState: { score: 18 } });
  screen.doc.fullscreenElement = null; screen.emit('fullscreenchange', {}); tree = render();
  expect(tree.props['data-display-mode']).toBe('windowed'); expect(text(tree)).toContain('Full screen');
  expect(nodes(tree).find(node => node.type === 'iframe')).toBe(child); expect(screen.returnFocus).toHaveBeenCalledOnce();
});

it('offers a scoped expanded view and Escape exit when native fullscreen is unavailable', () => {
  const screen = fixture(false), states: PlayerDisplayState[] = [];
  const controller = createPlayerDisplay({ element: screen.root, frame: () => screen.frame, exitButton: () => screen.control, onChange: state => states.push(state) });
  screen.key('f', {}); expect(states).toEqual([]);
  screen.key('f'); expect(states.at(-1)?.mode).toBe('expanded'); expect(screen.doc.body.style.overflow).toBe('hidden');
  screen.key('Escape'); expect(states.at(-1)?.mode).toBe('windowed'); expect(screen.doc.body.style.overflow).toBe('scroll');
  controller.destroy(); expect([...screen.listeners.values()].every(set => set.size === 0)).toBe(true);
});

it('preserves letter entry and rejects repeated, composed, modified or prevented shortcuts', () => {
  const base = { key: 'f', repeat: false, isComposing: false, ctrlKey: false, metaKey: false, altKey: false, defaultPrevented: false, target: null };
  expect(playerDisplayShortcut(base)).toBe('toggle'); expect(playerDisplayShortcut({ ...base, key: 'Enter', altKey: true })).toBe('toggle');
  for (const change of [{ repeat: true }, { isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { metaKey: true }, { altKey: true }, { defaultPrevented: true }, { target: { isContentEditable: true } }, { target: { closest: () => ({}) } }]) expect(playerDisplayShortcut({ ...base, ...change } as unknown as KeyboardEvent)).toBeUndefined();
});

it('cleans a pending display request without late state updates or retained body locks', async () => {
  const screen = fixture(), states: PlayerDisplayState[] = [];
  let resolve!: () => void;
  screen.root.requestFullscreen = () => new Promise<void>(finish => { resolve = finish; });
  const controller = createPlayerDisplay({ element: screen.root, frame: () => screen.frame, exitButton: () => screen.control, onChange: state => states.push(state) });
  controller.toggle(); expect(states.at(-1)?.pending).toBe(true); controller.destroy();
  const count = states.length; screen.doc.fullscreenElement = screen.root; resolve(); await settle();
  expect(states).toHaveLength(count); expect(screen.doc.exitFullscreen).toHaveBeenCalledOnce(); expect(screen.doc.body.style.overflow).toBe('scroll');
});

it('reserves expanded display across roots with one focus owner and balanced scrolling', () => {
  const screen = fixture(false), firstStates: PlayerDisplayState[] = [], secondStates: PlayerDisplayState[] = [], secondFocus = vi.fn();
  const secondRoot = { ...screen.root } as HTMLDivElement;
  const first = createPlayerDisplay({ element: screen.root, frame: () => screen.frame, exitButton: () => screen.control, onChange: state => firstStates.push(state) });
  const second = createPlayerDisplay({ element: secondRoot, frame: () => null, exitButton: () => ({ focus: secondFocus }) as unknown as HTMLButtonElement, onChange: state => secondStates.push(state) });
  first.toggle(); second.toggle();
  expect(firstStates.at(-1)?.mode).toBe('expanded'); expect(secondStates.at(-1)?.mode).toBe('windowed'); expect(secondStates.at(-1)?.notice).toContain('other player display');
  expect(screen.doc.body.style.overflow).toBe('hidden');
  first.exit(); second.toggle(); expect(secondStates.at(-1)?.mode).toBe('expanded');
  screen.emit('focusin', { target: {} }); expect(secondFocus).toHaveBeenCalledOnce();
  first.destroy(); expect(screen.doc.body.style.overflow).toBe('hidden');
  second.destroy(); expect(screen.doc.body.style.overflow).toBe('scroll');
});

it('retains a pending reservation until cancellation settles and fences stale cleanup', async () => {
  const screen = fixture(), states: PlayerDisplayState[] = [], secondStates: PlayerDisplayState[] = [];
  let reject!: (error: Error) => void;
  screen.root.requestFullscreen = () => new Promise<void>((_resolve, fail) => { reject = fail; });
  const first = createPlayerDisplay({ element: screen.root, frame: () => screen.frame, exitButton: () => screen.control, onChange: state => states.push(state) });
  const secondRoot = { ...screen.root, requestFullscreen: undefined } as unknown as HTMLDivElement;
  const second = createPlayerDisplay({ element: secondRoot, frame: () => null, exitButton: () => null, onChange: state => secondStates.push(state) });
  first.toggle(); first.exit(); second.toggle(); expect(secondStates.at(-1)?.mode).toBe('windowed'); expect(states.at(-1)?.pending).toBe(true);
  reject(new Error('Synthetic cancelled request')); await settle(); second.toggle();
  expect(secondStates.at(-1)?.mode).toBe('expanded'); expect(screen.doc.body.style.overflow).toBe('hidden');
  first.destroy(); first.destroy(); expect(screen.doc.body.style.overflow).toBe('hidden');
  second.exit(); expect(screen.doc.body.style.overflow).toBe('scroll'); second.destroy();
});

it('preserves every attested runtime byte and the exact approval contract digest', () => {
  const hash = (value: string | Uint8Array) => createHash('sha256').update(value).digest('hex');
  const inventory = Object.entries(provenance.implementation_files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  expect(inventory).toHaveLength(5);
  for (const [path, expected] of inventory) expect(hash(readFileSync(new URL(path, import.meta.url)))).toBe(expected);
  expect(hash(inventory.map(([path, digest]) => `${path}\0${digest}\n`).join(''))).toBe('8a95fdf521faa262ff8ef2b59d5311dac1c556713e679404621cb2d1324cd176');
  expect(GAME_STUDIO_RUNTIME_DIGEST).toBe(provenance.implementation_sha256); expect(GAME_STUDIO_RUNTIME_CONTRACT).toBe('game-studio-data-v2');
});
