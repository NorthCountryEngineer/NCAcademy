import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPlayerDisplay, type PlayerDisplayState } from './game-display-controller';
import './game-display.css';

/** Presentation only: the approved fixed player remains the same mounted child. */
export function AcademyGameDisplay({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null), exitButton = useRef<HTMLButtonElement>(null);
  const controller = useRef<ReturnType<typeof createPlayerDisplay> | null>(null);
  const [display, setDisplay] = useState<PlayerDisplayState>({ mode: 'windowed', pending: false, notice: '' });
  useEffect(() => {
    if (!root.current) return;
    const active = createPlayerDisplay({ element: root.current, frame: () => root.current?.querySelector('iframe') ?? null, exitButton: () => exitButton.current, onChange: setDisplay });
    controller.current = active;
    return () => { controller.current = null; active.destroy(); };
  }, []);
  const expanded = display.mode !== 'windowed';
  return <div ref={root} className="academy-game-display" data-display-mode={display.mode}>
    <div className="academy-game-display-toolbar" role="group" aria-label="Game display controls">
      <button type="button" className="button secondary" ref={exitButton} disabled={display.pending} aria-pressed={expanded} aria-keyshortcuts="F Alt+Enter" title="Full screen: use this button, or F / Alt+Enter while the toolbar has focus. Escape exits. Keyboard shortcuts inside the game keep their existing behavior." onClick={() => controller.current?.toggle()}>{display.pending ? 'Opening full screen…' : expanded ? 'Exit full screen' : 'Full screen'}</button>
      <span className="help-text">{expanded ? 'Escape exits full screen.' : 'Use the button for full screen. F / Alt+Enter also works while this toolbar has focus.'}</span>
    </div>
    {display.notice && <p className="academy-game-display-notice" role="status">{display.notice}</p>}
    <div className="academy-game-display-content">{children}</div>
  </div>;
}
