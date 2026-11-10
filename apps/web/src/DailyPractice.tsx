import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, ArrowRight, BookOpen, ClipboardCheck, Copy, MessageSquareText, Plus } from 'lucide-react';
import { ApiError, errorText, type Course } from './api';
import { Badge, Empty, Notice, Spinner } from './components';
import {
 createDailyPracticeDraft, dailyPracticeDetail, dailyPracticeWorkspace, practiceDateLabel,
 publishDailyPractice, submitDailyPractice, type PracticeDetail, type PracticeDraftInput,
 type PracticeItem, type PracticeLessonLink, type PracticeResult, type PracticeSummary,
 type PracticeTopic, type PracticeWorkspace,
} from './daily-practice-api';
import './daily-practice.css';

type Member = NonNullable<Course['members']>[number];
type DraftSelection = Omit<PracticeDraftInput, 'request_id'>;
type Props = {
 courseId: string;
 schoolId: string;
 members?: Member[];
 initialAssignmentId?: string | null;
 teacherName?: string;
 openEnrollment?: () => void;
 askTeacher?: (lesson: PracticeLessonLink, assignmentId: string) => void;
};
const unitNames = [
 'Equal groups', 'Multiplication and division', 'Area', 'Place value and arithmetic',
 'Two-step problems', 'Fractions', 'Time and measurement', 'Graphs and line plots', 'Shapes and perimeter',
];
const defaultQuestionCount = 6;
const questionCounts = [4, 6, 8, 10, 12, 16, 20];

function memberName(member?: Member) {
 return member?.display_name || member?.preferred_name || member?.name || member?.username || 'Student';
}
function memberOption(member: Member) {
 return memberName(member) + (member.username ? ' · @' + member.username : '');
}
function assignmentStatus(assignment: PracticeSummary, today: string) {
 return assignment.status === 'draft' ? 'Draft' : assignment.status === 'submitted' ? 'Completed'
  : assignment.date > today ? 'Scheduled' : 'Published';
}
function sourceLink(value?: string | null) {
 try {
  const url = new URL(value || '');
  return url.protocol === 'https:' && !url.username && !url.password &&
   ['nysed.gov', 'www.nysed.gov'].includes(url.hostname) ? url.href : null;
 } catch { return null; }
}

export function DailyPractice({ courseId, schoolId, members = [], initialAssignmentId = null, teacherName, openEnrollment, askTeacher }: Props) {
 const [workspace, setWorkspace] = useState<PracticeWorkspace | null>(null);
 const [offset, setOffset] = useState(0);
 const [reload, setReload] = useState(0);
 const [loading, setLoading] = useState(true);
 const [listError, setListError] = useState('');
 const [assignmentId, setAssignmentId] = useState<string | null>(initialAssignmentId);
 const [detail, setDetail] = useState<PracticeDetail | null>(null);
 const [detailLoading, setDetailLoading] = useState(false);
 const [detailError, setDetailError] = useState('');
 const [detailRetry, setDetailRetry] = useState(0);
 const [studentId, setStudentId] = useState('');
 const [date, setDate] = useState<string | null>(null);
 const [topicId, setTopicId] = useState('');
 const [count, setCount] = useState(defaultQuestionCount);
 const [reuse, setReuse] = useState<PracticeSummary | null>(null);
 const [answers, setAnswers] = useState<Record<string, string>>({});
 const [operation, setOperation] = useState<'draft' | 'publish' | 'submit' | null>(null);
 const [draftError, setDraftError] = useState('');
 const [actionError, setActionError] = useState('');
 const [success, setSuccess] = useState('');
 const actionInProgress = useRef(false);
 const draftIdentity = useRef<{ signature: string; id: string } | null>(null);
 const focusedDetail = useRef<HTMLHeadingElement>(null);
 const planHeading = useRef<HTMLHeadingElement>(null);
 const completedView = detail?.status === 'submitted';
 const rosterScope = JSON.stringify(members.filter(member => member.role === 'student' && member.principal_kind !== 'agent')
  .map(member => member.user_id).sort());

 useEffect(() => {
  const controller = new AbortController();
  let current = true;
  setLoading(true);
  setListError('');
  void dailyPracticeWorkspace(courseId, schoolId, offset, controller.signal)
   .then(value => { if (current) setWorkspace(value); })
   .catch(error => {
    if (!current) return;
    if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
     setWorkspace(null);
     setDetail(null);
     setAssignmentId(null);
     setAnswers({});
    }
    setListError(errorText(error));
   })
   .finally(() => { if (current) setLoading(false); });
  return () => { current = false; controller.abort(); };
 }, [courseId, schoolId, offset, reload, rosterScope]);

 useEffect(() => {
  if (!assignmentId || detail?.id === assignmentId) return;
  const controller = new AbortController();
  let current = true;
  setDetailLoading(true);
  setDetailError('');
  void dailyPracticeDetail(courseId, schoolId, assignmentId, controller.signal)
   .then(value => { if (current) setDetail(value); })
   .catch(error => { if (current) setDetailError(errorText(error)); })
   .finally(() => { if (current) setDetailLoading(false); });
  return () => { current = false; controller.abort(); };
 }, [courseId, schoolId, assignmentId, detail?.id, detailRetry]);

 useEffect(() => {
  if (detail?.id) focusedDetail.current?.focus();
 }, [detail?.id, completedView]);

 const canManage = workspace?.can_manage === true;
 const availableStudents = members.filter(member => member.role === 'student' && member.principal_kind !== 'agent'
  && (workspace?.eligible_student_ids?.includes(member.user_id) ?? false));
 const selectedStudent = availableStudents.find(member => member.user_id === studentId);
 const selectedTopic = workspace?.topics.find(topic => topic.id === topicId);
 const busy = operation !== null;
 const assignmentDate = date ?? workspace?.today ?? '';
 const activeDetail = detail?.id === assignmentId ? detail : null;
 const publishEligible = !!activeDetail?.can_publish && availableStudents.some(member => member.user_id === activeDetail.student_id);
 const completeAnswers = !!activeDetail?.items.length &&
  activeDetail.items.every(item => (answers[item.id] || '').trim());
 const currentPage = workspace?.offset === offset;
 const canCreate = !!workspace?.ready && !listError && !!selectedStudent
  && workspace.today <= assignmentDate && assignmentDate <= '2027-06-30' && (!!reuse || !!selectedTopic);

 function selectAssignment(id: string | null) {
  if (actionInProgress.current || id === assignmentId) return;
  setAssignmentId(id);
  setDetail(null);
  setDetailLoading(!!id);
  setDetailError('');
  setAnswers({});
  setActionError('');
  setSuccess('');
 }
 function updateAssignment(value: PracticeDetail) {
  setDetail(value);
  setAssignmentId(value.id);
  setDetailLoading(false);
  setDetailError('');
  setReload(previous => previous + 1);
 }
 async function generate(event: FormEvent) {
  event.preventDefault();
  if (actionInProgress.current || !canCreate || !selectedStudent || !canManage) return;
  const selection: DraftSelection = {
   student_id: selectedStudent.user_id, date: assignmentDate,
   ...(reuse ? { source_assignment_id: reuse.id } : { topic_id: topicId, count }),
  };
  const signature = JSON.stringify(selection);
  actionInProgress.current = true;
  setOperation('draft');
  setDraftError('');
  setActionError('');
  setSuccess('');
  try {
   if (draftIdentity.current?.signature !== signature) {
    draftIdentity.current = { signature, id: crypto.randomUUID() };
   }
   const body = { ...selection, request_id: draftIdentity.current.id } as PracticeDraftInput;
   updateAssignment(await createDailyPracticeDraft(courseId, schoolId, body));
   draftIdentity.current = null;
   setReuse(null);
  } catch (error) { setDraftError(errorText(error)); }
  finally { setOperation(null); actionInProgress.current = false; }
 }
 async function publish() {
  if (actionInProgress.current || !canManage || !workspace?.ready || listError || !publishEligible || activeDetail?.status !== 'draft') return;
  actionInProgress.current = true;
  setOperation('publish');
  setActionError('');
  setSuccess('');
  try {
   const value = await publishDailyPractice(courseId, schoolId, activeDetail.id);
   updateAssignment(value);
   setSuccess('Assignment published for ' + practiceDateLabel(value.date) + '.');
  } catch (error) { setActionError(errorText(error)); }
  finally { setOperation(null); actionInProgress.current = false; }
 }
 async function submit(event: FormEvent) {
  event.preventDefault();
  if (actionInProgress.current || canManage || !activeDetail?.can_submit || !completeAnswers) return;
  actionInProgress.current = true;
  setOperation('submit');
  setActionError('');
  setSuccess('');
  try {
   const exactAnswers = Object.fromEntries(activeDetail.items.map(item => [item.id, answers[item.id].trim()]));
   updateAssignment(await submitDailyPractice(courseId, schoolId, activeDetail.id, exactAnswers));
   setAnswers({});
  } catch (error) { setActionError(errorText(error)); }
  finally { setOperation(null); actionInProgress.current = false; }
 }
 function startReuse(value: PracticeSummary) {
  if (actionInProgress.current || !canManage) return;
  setReuse(value);
  setDate(null);
  setDraftError('');
  setActionError('');
  setSuccess('');
  const matchingStudent = availableStudents.find(member => member.user_id === value.student_id);
  if (matchingStudent) setStudentId(matchingStudent.user_id);
  planHeading.current?.focus();
  planHeading.current?.scrollIntoView({ block: 'start' });
 }

 if (!workspace && loading) return <Spinner text="Loading daily math practice…"/>;
 if (!workspace) return <section className="panel"><div className="panel-body">
  <Notice error={listError}/><button type="button" className="button secondary" onClick={() => setReload(previous => previous + 1)}>Retry daily practice</button>
 </div></section>;

 const todayAssignments = workspace.today_assignments;
 const studentLanding = !canManage && !assignmentId;
 const preview = canManage && activeDetail?.status === 'draft';
 const resultScore = activeDetail?.result;

 return <div className="daily-practice">
  {canManage && <section className="panel practice-planner">
   <div className="panel-head"><div><h2 tabIndex={-1} ref={planHeading}>Plan daily math</h2>
    <p>Choose an enrolled student and the topic they are working on at school.</p></div><ClipboardCheck size={20}/></div>
   <div className="panel-body">
    <Notice error={draftError}/>
    {!workspace.ready ? <Empty title="The teaching corpus needs review">
     Approve the current Grade 3 mathematics corpus under Coursework before preparing or publishing practice. Saved assignments remain in history.
    </Empty> : !availableStudents.length ? <div>
     <Empty title="No eligible students are enrolled">Enroll a student with an active NCAcademy account in this class before preparing practice.</Empty>
     {openEnrollment && <button className="button secondary" type="button" onClick={openEnrollment}>Open course enrollment</button>}
    </div> : <form onSubmit={generate} className="practice-plan-form">
     {reuse && <div className="practice-reuse">
      <div><strong>Reuse exact questions</strong><p>{reuse.title} · {practiceDateLabel(reuse.date)}</p>
       <small>The new draft keeps the original questions. It starts without answers or a score.</small></div>
      <button type="button" className="text-button" disabled={busy} onClick={() => { setReuse(null); setDraftError(''); }}>Choose a new topic instead</button>
     </div>}
     <div className="form-grid">
      <label>Student<select required value={selectedStudent?.user_id || ''} disabled={busy} onChange={event => setStudentId(event.target.value)}>
       <option value="">Choose an enrolled student</option>
       {availableStudents.map(member => <option key={member.user_id} value={member.user_id}>{memberOption(member)}</option>)}
      </select></label>
      <label>Assignment date<input type="date" required value={assignmentDate} min={workspace.today} max="2027-06-30" disabled={busy}
       onChange={event => setDate(event.target.value)}/><small>Dates follow New York time.</small></label>
     </div>
     {!reuse && <>
      <div className="form-grid">
       <label>Math topic<select required value={topicId} disabled={busy} onChange={event => setTopicId(event.target.value)}>
        <option value="">Choose a Grade 3 topic</option>
        {groupTopics(workspace.topics).map(([unit, topics]) => <optgroup key={unit} label={'Unit ' + unit + ' · ' + (unitNames[unit - 1] || 'Grade 3 mathematics')}>
         {topics.map(topic => <option key={topic.id} value={topic.id}>{topic.title}</option>)}
        </optgroup>)}
       </select></label>
       <label>Number of questions<select value={count} disabled={busy} onChange={event => setCount(Number(event.target.value))}>
        {questionCounts.map(value => <option key={value} value={value}>{value}</option>)}
       </select></label>
      </div>
      {selectedTopic && <p className="help-text">New York standards: {selectedTopic.standards.join(', ')}</p>}
     </>}
     <button className="button primary" disabled={busy || !canCreate}>
      {reuse ? <Copy size={16}/> : <Plus size={16}/>}
      {operation === 'draft' ? 'Preparing preview…' : reuse ? 'Preview reused assignment' : 'Generate assignment preview'}
     </button>
     <p className="help-text">The exact questions are saved as a draft. Review them before publishing to the student.</p>
    </form>}
   </div>
  </section>}

  {studentLanding && <section className="panel practice-today">
   <div className="panel-head"><div><div className="eyebrow">GRADE 3 MATHEMATICS</div>
    <h2>Today’s assignment</h2><p>{practiceDateLabel(workspace.today)}</p></div><ClipboardCheck size={20}/></div>
   <div className="panel-body">
    {!currentPage ? <button type="button" className="button secondary" onClick={() => setOffset(0)}>Return to today</button>
     : loading ? <Spinner text="Checking your assignments…"/>
     : todayAssignments.length ? todayAssignments.map(assignment => <div className="practice-today-row" key={assignment.id}>
      <div><strong>{assignment.title}</strong><p>{assignment.item_count} questions</p></div>
      <button type="button" className="button primary" onClick={() => selectAssignment(assignment.id)}>
       {assignment.status === 'submitted' ? 'Review today’s assignment' : 'Start today’s assignment'}<ArrowRight size={16}/>
      </button>
     </div>) : !listError && <Empty title="No assignment for today yet">
      When your teacher publishes today’s assignment, you’ll find it here. You can still open an earlier assignment below.
     </Empty>}
   </div>
  </section>}

  {assignmentId && <section className="panel practice-assignment">
   <div className="panel-head"><div>
    <button type="button" className="text-button practice-back" disabled={busy} onClick={() => selectAssignment(null)}>
     <ArrowLeft size={15}/>{canManage ? 'Close assignment' : 'Back to assignments'}
    </button>
    <h2 ref={focusedDetail} tabIndex={-1}>{activeDetail ? activeDetail.title : 'Daily math assignment'}</h2>
    {activeDetail && <p>{practiceDateLabel(activeDetail.date)} · {activeDetail.item_count} questions
     {canManage && ' · ' + memberName(members.find(member => member.user_id === activeDetail.student_id))}</p>}
   </div>{activeDetail && <Badge tone={preview ? 'amber' : resultScore ? 'green' : ''}>{assignmentStatus(activeDetail, workspace.today)}</Badge>}</div>
   <div className="panel-body">
    <Notice error={detailError || actionError} success={success}/>
    {detailError && <button type="button" className="button secondary" onClick={() => setDetailRetry(previous => previous + 1)}>Retry assignment</button>}
    {detailLoading && <Spinner text="Loading assignment…"/>}
    {activeDetail && <>
     {preview && <p className="practice-note">{publishEligible
      ? 'This is the exact draft. The student cannot see it until you publish.'
      : 'This draft cannot be published now. Check its date, current teaching corpus and student enrollment.'}</p>}
     {resultScore ? <PracticeResultView result={resultScore} items={activeDetail.items} staff={canManage} teacherName={teacherName}
      askTeacher={!canManage && askTeacher ? lesson => askTeacher(lesson, activeDetail.id) : undefined}/>
     : !canManage ? <form onSubmit={submit}>
      {!activeDetail.can_submit && <p className="practice-note">This assignment is read-only right now. Ask your teacher to check its lesson approval.</p>}
      <div className="practice-questions">
       {activeDetail.items.map((item, index) => <PracticeQuestion key={item.id} item={item} index={index} value={answers[item.id] || ''}
        disabled={busy || !activeDetail.can_submit} change={value => {
         if (!actionInProgress.current) setAnswers(previous => ({ ...previous, [item.id]: value }));
        }}/>)}
      </div>
      <p className="help-text">Answer each question before submitting. After you submit, your answers are saved and you’ll see which lessons to review.</p>
      <button className="button primary" disabled={busy || !activeDetail.can_submit || !completeAnswers}>
       <ClipboardCheck size={16}/>{operation === 'submit' ? 'Checking your work…' : 'Submit assignment'}
      </button>
     </form> : <ol className="practice-preview">
      {activeDetail.items.map(item => <li key={item.id}><strong>{item.prompt}</strong>
       {item.figure && <pre className="practice-figure">{item.figure}</pre>}
       {item.options.length > 0 && <ul>{item.options.map(option => <li key={option.id}>{option.label}</li>)}</ul>}
       <small className="help-text">{item.standards.join(', ')}</small>
       {item.practice && <p className="help-text">Optional reflection ({item.practice.code}): {item.practice.prompt}</p>}
      </li>)}
     </ol>}
     {preview && <button type="button" className="button primary" disabled={busy || !publishEligible || !workspace.ready || !!listError} onClick={() => void publish()}>
      {operation === 'publish' ? 'Publishing…' : 'Publish this assignment'}
     </button>}
     {canManage && !preview && workspace.ready && <button type="button" className="button secondary practice-copy"
      disabled={busy} onClick={() => startReuse(activeDetail)}><Copy size={15}/>Reuse exact questions</button>}
    </>}
   </div>
  </section>}

  {(canManage || studentLanding) && <section className="panel practice-history">
   <div className="panel-head"><div><h2>{canManage ? 'Assignment history' : 'Your assignments'}</h2>
    <p>{canManage ? 'Review saved drafts, published assignments and completed work.' : 'Open earlier work or revisit the lessons from a completed assignment.'}</p></div></div>
   <div className="panel-body">
    <Notice error={listError}/>
    {listError && <button type="button" className="button secondary" onClick={() => setReload(previous => previous + 1)}>Retry history</button>}
    {loading && <Spinner text="Updating assignments…"/>}
    {currentPage && !loading && !listError && (!workspace.assignments.length ? <Empty title="No saved assignments yet"/>
     : <div className="practice-history-list">
      {workspace.assignments.map(assignment => <article className="practice-history-row" key={assignment.id}>
       <div><strong>{assignment.title}</strong>
        <p>{practiceDateLabel(assignment.date)} · {assignment.item_count} questions
         {canManage && ' · ' + memberName(members.find(member => member.user_id === assignment.student_id))}</p>
       </div>
       <div className="practice-history-actions">
        <Badge tone={assignment.status === 'submitted' ? 'green' : ''}>{assignment.result
         ? assignment.result.correct + ' of ' + assignment.result.total + ' correct' : assignmentStatus(assignment, workspace.today)}</Badge>
        <button type="button" className="button secondary" disabled={busy} onClick={() => selectAssignment(assignment.id)}>
         {assignment.status === 'draft' ? 'Open preview' : assignment.status === 'submitted' ? 'Review work' : 'Open assignment'}
        </button>
       </div>
      </article>)}
     </div>)}
    {currentPage && workspace.total > workspace.limit && <div className="practice-pagination">
     <button type="button" className="button secondary" disabled={loading || busy || offset <= 0}
      onClick={() => setOffset(Math.max(0, offset - workspace.limit))}>Previous assignments</button>
     <span>{offset + 1}–{Math.min(offset + workspace.limit, workspace.total)} of {workspace.total}</span>
     <button type="button" className="button secondary" disabled={loading || busy || offset + workspace.limit >= workspace.total}
      onClick={() => setOffset(offset + workspace.limit)}>Next assignments</button>
    </div>}
   </div>
  </section>}
 </div>;
}

function groupTopics(topics: PracticeTopic[]) {
 const groups = new Map<number, PracticeTopic[]>();
 for (const topic of topics) groups.set(topic.unit, [...(groups.get(topic.unit) || []), topic]);
 return [...groups.entries()].sort(([first], [second]) => first - second);
}

export function PracticeQuestion({ item, index, value, disabled, change }: {
 item: PracticeItem; index: number; value: string; disabled: boolean; change: (value: string) => void;
}) {
 const number = index + 1;
 return <fieldset className="practice-question" disabled={disabled}>
  <legend><span className="practice-number">{number}.</span> {item.prompt}</legend>
  {item.figure && <pre className="practice-figure" tabIndex={0}>{item.figure}</pre>}
  {item.answer_type === 'choice' ? <div className="practice-options">
   {item.options.map(option => <label className="practice-option" key={option.id}>
    <input type="radio" name={'practice-' + item.id} value={option.id} required checked={value === option.id}
     onChange={() => change(option.id)}/>
    <span>{option.label}</span>
   </label>)}
  </div> : <label className="practice-answer">Your answer for question {number}
   <input type="text" autoComplete="off" maxLength={120} required value={value}
    onChange={event => change(event.target.value)}/>
  </label>}
 </fieldset>;
}

export function PracticeResultView({ result, items, staff = false, teacherName, askTeacher }: {
 result: PracticeResult; items: PracticeItem[]; staff?: boolean; teacherName?: string; askTeacher?: (lesson: PracticeLessonLink) => void;
}) {
 const missed = result.total - result.correct;
 const reflections = [...new Map(items.flatMap(item => item.practice ? [[item.practice.code, item.practice] as const] : [])).values()];
 return <div className="practice-results">
  <div className="practice-score" role="status">
   <div><h3>{result.correct} of {result.total} correct</h3>
    <p>{staff ? (missed ? 'The student’s work is saved. Review the lessons recommended below.' : 'The student answered every question correctly.')
     : missed ? 'Your work is saved. Start with a lesson for something you missed.' : 'You answered every question correctly. Your work is saved.'}</p></div>
   <Badge tone={missed ? 'amber' : 'green'}>{missed ? missed + ' to review' : 'All correct'}</Badge>
  </div>
  {!!missed && <>
   <h3>What to review next</h3>
   {result.lessons_withheld && <p className="practice-note">
    Your score is still available. Some lesson material is unavailable until your teacher checks its approval.
   </p>}
   {result.lessons.map((lesson, index) => {
    const questions = items.flatMap((item, i) => result.items.some(outcome => outcome.id === item.id
     && !outcome.correct && outcome.lesson_id === lesson.id) ? [i + 1] : []);
    const url = sourceLink(lesson.source_url);
    return <details className="practice-lesson" open={index === 0} key={lesson.id}>
     <summary><BookOpen size={18}/><span><strong>{lesson.title}</strong>
      <small>{questions.length ? (questions.length === 1 ? 'Question ' : 'Questions ') + questions.join(', ') + ' · ' : ''}{lesson.standards.join(', ')}</small>
     </span></summary>
     <div className="practice-lesson-body"><p className="prose">{lesson.text}</p>
      <div className="practice-lesson-actions">
       {askTeacher && <button type="button" className="button secondary" onClick={() => askTeacher({ id: lesson.id, title: lesson.title })}>
        <MessageSquareText size={16}/>Ask {teacherName || 'the lesson teacher'} about this lesson
       </button>}
       {url && <a href={url} target="_blank" rel="noopener noreferrer" className="text-button">New York standards source</a>}
      </div>
     </div>
    </details>;
   })}
  </>}
  {reflections.length > 0 && <details className="practice-answer-review">
   <summary>Explain your thinking</summary>
   <p className="help-text">These reflection questions are optional and do not change the score.</p>
   <ul>{reflections.map(reflection => <li key={reflection.code}>{reflection.prompt}</li>)}</ul>
  </details>}
  <details className="practice-answer-review"><summary>{staff ? 'Review submitted answers' : 'Review your answers'}</summary>
   <ol className="practice-preview">
    {items.map(item => {
     const outcome = result.items.find(answer => answer.id === item.id);
     const answer = item.answer_type === 'choice' ? item.options.find(option => option.id === outcome?.answer)?.label : outcome?.answer;
     return <li key={item.id}>
      <strong>{item.prompt}</strong>
      {item.figure && <pre className="practice-figure">{item.figure}</pre>}
      <p>{answer || 'No answer recorded'} · {outcome?.correct ? 'Correct' : 'Review this topic'}</p>
     </li>;
    })}
   </ol>
  </details>
 </div>;
}
