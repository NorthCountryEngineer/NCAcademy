import { validateGameResult } from './game-runtime';
import { GAME_STUDIO_RUNTIME_CONTRACT } from './runtime-build';
import type { GameResult, GameSpec } from './game-types';

type PlayerMessage = { type: 'academy-game-ready' | 'academy-game-loaded' } | { type: 'academy-game-result'; result: GameResult };

/** Reject messages from other frames, external origins or a stale player instance. */
export function readGameStudioMessage(event: { source: unknown; origin: string; data: unknown }, source: unknown, nonce: string, spec: GameSpec): PlayerMessage | undefined {
  if (!source || event.source !== source || event.origin !== 'null' || !event.data || typeof event.data !== 'object' || Array.isArray(event.data)) return;
  const data = event.data as Record<string, unknown>;
  if (data.channel !== 'academy-game-studio' || data.contract !== GAME_STUDIO_RUNTIME_CONTRACT || data.nonce !== nonce) return;
  const resultMessage = data.type === 'academy-game-result';
  const keys = ['type', 'channel', 'contract', 'nonce', ...(resultMessage ? ['result'] : [])];
  if (Object.keys(data).length !== keys.length || Object.keys(data).some(key => !keys.includes(key))) return;
  if (data.type === 'academy-game-ready' || data.type === 'academy-game-loaded') return { type: data.type };
  if (resultMessage) {
    const result = validateGameResult(data.result, spec);
    if (result) return { type: 'academy-game-result', result };
  }
}
