import { useEffect, useMemo, useRef, useState } from 'react';
import { gameSandboxDocument } from './game-studio-runtime/game-runtime';
import { validateGameSpec } from './game-studio-runtime/game-spec';
import { readGameStudioMessage } from './game-studio-runtime/player-messages';
import { GAME_STUDIO_RUNTIME_CONTRACT, GAME_STUDIO_RUNTIME_DIGEST } from './game-studio-runtime/runtime-build';
import type { GameResult, GameSpec } from './game-studio-runtime/game-types';

export type { GameResult, GameSpec } from './game-studio-runtime/game-types';

/** Plays data supplied by the current authorized Academy snapshot endpoint only. */
export function GameStudioPlayer({ spec, onComplete }: { spec: GameSpec; onComplete?: (result: GameResult) => void }) {
  const instance = useMemo(() => {
    try {
      const validated = validateGameSpec(spec);
      const nonce = crypto.randomUUID().replaceAll('-', '');
      return { spec: validated, nonce, document: gameSandboxDocument(nonce), error: '' };
    } catch (error) {
      return { spec: undefined, nonce: '', document: '', error: error instanceof Error ? error.message : 'This game could not be loaded safely.' };
    }
  }, [spec]);
  const iframe = useRef<HTMLIFrameElement>(null);
  const completed = useRef(onComplete);
  const [readyNonce, setReadyNonce] = useState('');
  const [failedNonce, setFailedNonce] = useState('');
  useEffect(() => { completed.current = onComplete; }, [onComplete]);
  useEffect(() => {
    if (!instance.spec) return;
    // An opaque sandbox has no addressable origin. Source window, null origin,
    // per-instance random nonce and the exact fixed envelope bind its replies.
    const send = () => iframe.current?.contentWindow?.postMessage({ type: 'academy-game-load', channel: 'academy-game-studio', contract: GAME_STUDIO_RUNTIME_CONTRACT, nonce: instance.nonce, spec: instance.spec }, '*');
    const receive = (event: MessageEvent) => {
      const message = readGameStudioMessage(event, iframe.current?.contentWindow, instance.nonce, instance.spec!);
      if (!message) return;
      if (message.type === 'academy-game-ready') { setReadyNonce(instance.nonce); send(); }
      if (message.type === 'academy-game-loaded') setReadyNonce(instance.nonce);
      if (message.type === 'academy-game-result') completed.current?.(message.result);
    };
    window.addEventListener('message', receive);
    send();
    const timeout = window.setTimeout(() => setFailedNonce(instance.nonce), 12000);
    return () => { window.removeEventListener('message', receive); window.clearTimeout(timeout); };
  }, [instance]);
  if (instance.error) return <p role="alert">{instance.error}</p>;
  const ready = readyNonce === instance.nonce;
  return <div className="game-studio-player" data-runtime-contract={GAME_STUDIO_RUNTIME_CONTRACT} data-runtime-digest={GAME_STUDIO_RUNTIME_DIGEST}>
    {!ready && <p role="status">{failedNonce === instance.nonce ? 'The game is taking longer to load. Reload the page to try again.' : 'Preparing your game…'}</p>}
    <iframe key={instance.nonce} ref={iframe} title={`${instance.spec!.title} — isolated game player`} srcDoc={instance.document} sandbox="allow-scripts" referrerPolicy="no-referrer" allow="camera 'none'; microphone 'none'; geolocation 'none'; payment 'none'; clipboard-read 'none'; clipboard-write 'none'; fullscreen 'none'; display-capture 'none'; web-share 'none'" style={{ display: 'block', width: '100%', height: 'clamp(570px, 70vw, 850px)', border: 0, borderRadius: 12 }} onLoad={() => iframe.current?.contentWindow?.postMessage({ type: 'academy-game-load', channel: 'academy-game-studio', contract: GAME_STUDIO_RUNTIME_CONTRACT, nonce: instance.nonce, spec: instance.spec }, '*')} />
    <p className="game-studio-player-note">Use Start, Pause, Restart level and the touch buttons, or click the game to use your keyboard. Complete a level to unlock the next one. {onComplete ? 'This assignment saves the first completed browser observation privately for human review. Displayed points are provisional and are not verified grades.' : 'Gameplay is practice; this player does not submit grades or save progress.'}</p>
  </div>;
}
