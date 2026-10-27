import { useState, type FormEvent } from 'react';
import { errorText } from './api';
import { Badge, Notice } from './components';
import { awardCourseGameAttempt, saveCourseGameScoring, scoringPolicyPayload, type CourseGame, type GameAttempt, type GameAttemptIndex, type ScoringPolicy } from './game-studio-api';

export const defaultGameScoringPolicy = (): ScoringPolicy => ({ version: 1, max_points: 25, method: 'proportional', rounding: 'hundredth', attempt_policy: 'best', max_attempts: 3, reveal_policy: 'zero' });

/** Import a reusable, bounded configuration; the API independently validates it. */
export function parseGameScoringPolicy(text: string): ScoringPolicy {
  if (text.length > 5000) throw new Error('A scoring configuration must be at most 5,000 characters.');
  const value = JSON.parse(text) as Record<string, unknown>;
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Use a scoring policy JSON object.');
  if (!Object.prototype.hasOwnProperty.call(value, 'reveal_policy')) value.reveal_policy = 'zero';
  const keys = ['version', 'max_points', 'method', 'rounding', 'attempt_policy', 'max_attempts', 'reveal_policy', ...(value.method === 'arcade_target' ? ['arcade_target'] : [])];
  if (Object.keys(value).length !== keys.length || Object.keys(value).some(key => !keys.includes(key)) || value.version !== 1) throw new Error('This configuration has an unsupported version or unexpected fields.');
  const integer = (key: string, min: number, max: number) => typeof value[key] === 'number' && Number.isInteger(value[key]) && (value[key] as number) >= min && (value[key] as number) <= max;
  if (!integer('max_points', 1, 10000) || !integer('max_attempts', 1, 100) || !['proportional', 'completion', 'arcade_target'].includes(String(value.method)) || !['whole', 'hundredth'].includes(String(value.rounding)) || !['first', 'latest', 'best'].includes(String(value.attempt_policy)) || !['zero', 'earned'].includes(String(value.reveal_policy)) || (value.method === 'arcade_target' && !integer('arcade_target', 1, 1000000))) throw new Error('Choose valid scoring limits, methods and attempt rules.');
  return scoringPolicyPayload(value as unknown as ScoringPolicy)!;
}

export function GameScoringFields({ value, change, disabled = false }: { value: ScoringPolicy | null; change: (value: ScoringPolicy | null) => void; disabled?: boolean }) {
  const update = (patch: Partial<ScoringPolicy>) => change(scoringPolicyPayload({ ...(value || defaultGameScoringPolicy()), ...patch }));
  return <fieldset className="game-scoring-fields" disabled={disabled}>
    <legend>Assignment points</legend>
    <label className="checkbox-label"><input type="checkbox" checked={!!value} onChange={event => change(event.target.checked ? defaultGameScoringPolicy() : null)} />Track points for this class game</label>
    {!value ? <p className="help-text">Practice only. Individual results are not saved.</p> : <>
      <div className="game-studio-actions" aria-label="Point scale presets"><button type="button" className="button secondary" onClick={() => update({ max_points: 25 })}>25 points</button><button type="button" className="button secondary" onClick={() => update({ max_points: 100 })}>100 points</button></div>
      <div className="game-scoring-grid">
        <label>Maximum assignment points<input type="number" required min={1} max={10000} step={1} value={value.max_points} onChange={event => update({ max_points: Number(event.target.value) })} /></label>
        <label>Scoring method<select value={value.method} onChange={event => update({ method: event.target.value as ScoringPolicy['method'], ...(event.target.value === 'arcade_target' ? { arcade_target: value.arcade_target || 1000 } : {}) })}><option value="proportional">Proportion of full game objectives</option><option value="completion">All points for completing the full game</option><option value="arcade_target">Raw arcade points against a chosen target</option></select></label>
        {value.method === 'arcade_target' && <label>Raw arcade point target<input type="number" required min={1} max={1000000} step={1} value={value.arcade_target || ''} onChange={event => update({ arcade_target: Number(event.target.value) })} /></label>}
        <label>Rounding<select value={value.rounding} onChange={event => update({ rounding: event.target.value as ScoringPolicy['rounding'] })}><option value="hundredth">Two decimal places</option><option value="whole">Whole points</option></select></label>
        <label>Repeat attempts<select value={value.attempt_policy} onChange={event => update({ attempt_policy: event.target.value as ScoringPolicy['attempt_policy'] })}><option value="best">Best completed result</option><option value="latest">Latest completed result</option><option value="first">First completed result</option></select></label>
        <label>Maximum attempts<input type="number" required min={1} max={100} step={1} value={value.max_attempts} onChange={event => update({ max_attempts: Number(event.target.value) })} /></label>
        <label>Answer reveal policy<select value={value.reveal_policy} onChange={event => update({ reveal_policy: event.target.value as ScoringPolicy['reveal_policy'] })}><option value="zero">Zero points after revealing answers</option><option value="earned">Keep points earned before a reveal</option></select></label>
      </div>
      <p className="help-text">Proportional scoring uses the objectives across the entire saved game, including every level. Arcade targets use raw arcade points and are available only for games with arcade scoring enabled. Results are capped at this assignment’s maximum; engine safety limits never become the grading denominator.</p>
      <p className="help-text">Browser observations produce provisional points. A human teacher or administrator must review an attempt and explicitly award credit. The reveal rule is retained for compatible games; Academy’s current arcade player has no answer-reveal control.</p>
    </>}
  </fieldset>;
}

export function GameScoringSettings({ schoolId, courseId, game, saved }: { schoolId: string; courseId: string; game: CourseGame; saved: (value: CourseGame) => void }) {
  const [policy, setPolicy] = useState<ScoringPolicy | null>(game.scoring_policy || null);
  const [configuration, setConfiguration] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return; setBusy(true); setError('');
    try { const validated = policy ? parseGameScoringPolicy(JSON.stringify(policy)) : null; saved(await saveCourseGameScoring(schoolId, courseId, game, validated)); }
    catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <section className="game-scoring-settings"><h3>Configure assignment scoring</h3><Notice error={error} /><form onSubmit={submit}>
    <GameScoringFields value={policy} change={setPolicy} disabled={busy} />
    <details><summary>Reuse a scoring configuration</summary><p className="help-text">Copy this policy into another class game to repeat the same rules. Student records and game identifiers are not included.</p><label>Current scoring policy<textarea readOnly rows={6} value={policy ? JSON.stringify(scoringPolicyPayload(policy), null, 2) : ''} /></label><label>Import scoring policy<textarea rows={5} maxLength={5000} value={configuration} onChange={event => setConfiguration(event.target.value)} placeholder="Paste an exported scoring policy JSON object" /></label><button type="button" className="button secondary" disabled={busy || !configuration.trim()} onClick={() => { try { setPolicy(parseGameScoringPolicy(configuration)); setError(''); } catch (error) { setError(errorText(error)); } }}>Load configuration into form</button></details>
    <p className="help-text">Saving changed rules invalidates the current publication approval. A human School/Admin must approve the new exact snapshot. Earlier attempts keep their original policy and points.</p>
    <button className="button secondary" disabled={busy}>{busy ? 'Saving scoring…' : 'Save scoring and require reapproval'}</button>
  </form></section>;
}

export function GameAttemptHistory({ schoolId, courseId, gameId, value, refresh }: { schoolId: string; courseId: string; gameId: string; value: GameAttemptIndex; refresh: () => Promise<void> }) {
  return <section className="game-attempt-history"><h3>Attempt history and points</h3>
    <p className="help-text">Observations are supplied by the browser and cannot prove identity, learning or correctness. Provisional points and reviewed credit are recorded separately; no automatic gradebook submission occurs.</p>
    {value.summary && <div className="game-attempt-summary"><strong>Selected provisional points: {value.summary.max_points === null ? 'No active scoring policy' : value.summary.suggested_points === null ? 'No completed result' : `${value.summary.suggested_points} / ${value.summary.max_points}`}</strong><p>{value.summary.attempt_count} attempts started · {value.summary.remaining_attempts} remaining under the current scoring rules. Earlier scoring series remain below.</p></div>}
    {value.summaries?.map(summary => <div className="game-attempt-summary" key={summary.student_id}><strong>Student {summary.student_id}</strong><p>Selected provisional points: {summary.suggested_points === null ? 'No completed result' : `${summary.suggested_points} / ${summary.max_points}`} · {summary.remaining_attempts} attempts remaining</p></div>)}
    {!value.attempts.length && <p>No attempts have been started.</p>}
    {value.attempts.map(attempt => <article key={attempt.id}><div className="scope-line"><Badge>Attempt {attempt.ordinal}</Badge><Badge tone={attempt.status === 'observed' ? 'green' : 'amber'}>{attempt.status === 'observed' ? 'Result recorded' : 'Started; no result recorded'}</Badge><Badge>{attempt.scoring_policy.max_points} point scale</Badge>{value.summary?.selected_attempt_id === attempt.id && <Badge>Selected result</Badge>}</div>
      {value.can_award && <p className="help-text">Student: {attempt.student_id}</p>}<p className="help-text">Started {attempt.started_at} · class game version {attempt.resource_version} · {attempt.scoring_policy.attempt_policy} attempt rule</p>
      {attempt.result && <p>Observed {attempt.result.outcome} · {attempt.result.score} objectives · {attempt.result.elapsed} seconds{attempt.result.points === undefined ? '' : ` · ${attempt.result.points} raw arcade points`}</p>}
      {attempt.calculation && <p><strong>Provisional points: {attempt.calculation.earned_points} / {attempt.calculation.max_points}</strong> · {attempt.calculation.numerator} / {attempt.calculation.denominator} ({attempt.calculation.method})</p>}
      {attempt.award ? <div className="game-reviewed-credit"><strong>Human-reviewed credit: {attempt.award.points} / {attempt.scoring_policy.max_points}</strong><p>{attempt.award.reason}</p><small>Reviewed {attempt.award.awarded_at} by {attempt.award.awarded_by}</small></div> : <p className="help-text">Human-reviewed credit: not awarded.</p>}
      {value.can_award && attempt.status === 'observed' && !attempt.award && <GameAttemptAwardForm schoolId={schoolId} courseId={courseId} gameId={gameId} attempt={attempt} saved={refresh} />}
    </article>)}
  </section>;
}

export function GameAttemptAwardForm({ schoolId, courseId, gameId, attempt, saved }: { schoolId: string; courseId: string; gameId: string; attempt: GameAttempt; saved: () => Promise<void> }) {
  const [points, setPoints] = useState(String(attempt.calculation?.earned_points ?? 0)), [reason, setReason] = useState(''), [attested, setAttested] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [complete, setComplete] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy || complete || !attested || !reason.trim()) return; setBusy(true); setError('');
    try { await awardCourseGameAttempt(schoolId, courseId, gameId, attempt.id, Number(points), reason); setComplete(true); await saved(); }
    catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <form className="game-attempt-award" onSubmit={submit}><Notice error={error} /><label>Reviewed credit points<input type="number" required min={0} max={attempt.scoring_policy.max_points} step="0.01" value={points} disabled={busy || complete} onChange={event => setPoints(event.target.value)} /></label><label>Reason for credit decision<textarea required minLength={3} maxLength={2000} rows={2} value={reason} disabled={busy || complete} onChange={event => setReason(event.target.value)} /></label><label className="checkbox-label"><input type="checkbox" checked={attested} disabled={busy || complete} onChange={event => setAttested(event.target.checked)} />I reviewed the evidence and explicitly authorize these credit points.</label><button className="button secondary" disabled={busy || complete || !attested || reason.trim().length < 3}>{complete ? 'Reviewed credit recorded' : busy ? 'Recording credit…' : 'Record human-reviewed credit'}</button><small>This immutable decision is separate from the provisional browser result.</small></form>;
}
