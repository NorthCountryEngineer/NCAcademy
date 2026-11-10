import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { setCsrf, type Course, type School, type User } from './api';
import { Empty, Notice, Spinner, Tabs } from './components';
import { CourseDetail } from './Curriculum';
import { DailyPractice, PracticeQuestion, PracticeResultView } from './DailyPractice';
import { LessonTutor } from './LessonTutor';
import { dailyPracticeDetail, isGrade3PracticeCourse, practiceDateLabel, type PracticeDetail, type PracticeWorkspace } from './daily-practice-api';

const hooks = vi.hoisted(() => ({
 values: [] as unknown[], dependencies: [] as (readonly unknown[] | undefined)[],
 cleanups: [] as ((() => void) | undefined)[], pending: [] as (() => void)[],
 stateIndex: 0, effectIndex: 0, dirty: false,
}));
vi.mock('react', async importOriginal => ({
 ...await importOriginal<typeof import('react')>(),
 useState(initial: unknown) {
  const index = hooks.stateIndex++;
  if (index >= hooks.values.length) hooks.values.push(typeof initial === 'function' ? initial() : initial);
  return [hooks.values[index], (value: unknown) => {
   const next = typeof value === 'function' ? value(hooks.values[index]) : value;
   if (!Object.is(next, hooks.values[index])) {
    hooks.values[index] = next;
    hooks.dirty = true;
   }
  }];
 },
 useRef(initial: unknown) {
  const index = hooks.stateIndex++;
  if (index >= hooks.values.length) hooks.values.push({ current: initial });
  return hooks.values[index];
 },
 useCallback(callback: unknown, dependencies: readonly unknown[]) {
  const index = hooks.stateIndex++, previous = hooks.values[index] as { callback: unknown; dependencies: readonly unknown[] } | undefined;
  if (!previous || dependencies.length !== previous.dependencies.length || dependencies.some((value, i) => !Object.is(value, previous.dependencies[i]))) {
   hooks.values[index] = { callback, dependencies };
   return callback;
  }
  return previous.callback;
 },
 useEffect(effect: () => void | (() => void), dependencies?: readonly unknown[]) {
  const index = hooks.effectIndex++, previous = hooks.dependencies[index];
  if (!previous || !dependencies || previous.length !== dependencies.length ||
      dependencies.some((value, i) => !Object.is(value, previous[i]))) {
   hooks.dependencies[index] = dependencies;
   hooks.pending.push(() => { hooks.cleanups[index]?.(); hooks.cleanups[index] = effect() || undefined; });
  }
 },
}));

type Element = ReactElement<{
 children?: ReactNode; onClick?: () => void; onSubmit?: (event: { preventDefault: () => void }) => Promise<void>;
 onChange?: (event: { target: { value: string } }) => void; disabled?: boolean; open?: boolean; value?: string;
 type?: string; checked?: boolean; href?: string;
}>;
const expanded = new Set([PracticeQuestion, PracticeResultView, Notice, Empty, Spinner]);
function children(element: Element): ReactNode {
 return expanded.has(element.type as typeof PracticeQuestion)
  ? (element.type as (props: unknown) => ReactNode)(element.props)
  : element.props.children;
}
function nodes(value: ReactNode): Element[] {
 if (Array.isArray(value)) return value.flatMap(nodes);
 if (!isValidElement(value)) return [];
 const element = value as Element;
 return [element, ...nodes(children(element))];
}
function visible(value: ReactNode): string {
 if (Array.isArray(value)) return value.map(visible).join('');
 if (isValidElement(value)) return visible(children(value as Element));
 return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

const courseId = 'math-three', schoolId = 'test-school', day = '2026-11-10';
const base = '/api/courses/math-three/daily-practice';
const owner = 'opaque-local-student';
const members: NonNullable<Course['members']> = [
 { user_id: owner, name: 'Alex Example', display_name: 'Lex (Alex Example)', username: 'lex-example', role: 'student', principal_kind: 'human' },
 { user_id: 'opaque-other', name: 'Not Eligible', role: 'student', principal_kind: 'human' },
 { user_id: 'opaque-agent', name: 'A Student Agent', role: 'student', principal_kind: 'agent' },
];
const topic = { id: 'equal-groups', title: 'Equal groups and products', unit: 1, standards: ['NY-3.OA.1'] };
const lessonOne = { id: 'reference:ny-g3-equal-groups', title: 'Equal groups tell a story',
 text: 'Draw a fresh model using equal groups.', standards: ['NY-3.OA.1'], source_url: 'https://www.nysed.gov/standards-instruction/mathematics' };
const lessonTwo = { id: 'reference:ny-g3-division', title: 'Meaning of division',
 text: 'Check which quantity is missing.', standards: ['NY-3.OA.2'], source_url: 'javascript:alert(1)' };
const items: PracticeDetail['items'] = [
 { id: 'item-one', prompt: 'There are 4 bags with 3 apples each. How many apples?', options: [], answer_type: 'text',
  standards: ['NY-3.OA.1'], lesson_id: lessonOne.id, practice: { code: 'MP.2', prompt: 'Explain what your numbers stand for.' } },
 { id: 'item-two', prompt: 'Choose the equal-group model.', options: [{ id: 'a', label: 'Same amount in each' }, { id: 'b', label: 'Different amounts' }],
  answer_type: 'choice', standards: ['NY-3.OA.1'], lesson_id: lessonOne.id, figure: 'Bag A: ***\nBag B: ***' },
 { id: 'item-three', prompt: 'Share 15 counters among 5 people.', options: [], answer_type: 'text',
  standards: ['NY-3.OA.2'], lesson_id: lessonTwo.id },
];
const previous: PracticeDetail = { id: 'opaque-past-assignment', topic_id: topic.id, title: 'Earlier group practice',
 date: '2026-11-09', student_id: owner, item_count: items.length, status: 'submitted', items,
 can_publish: false, can_submit: false,
 result: { correct: 1, total: 3, items: [
  { id: 'item-one', correct: true, answer: '12', lesson_id: lessonOne.id },
  { id: 'item-two', correct: false, answer: 'b', lesson_id: lessonOne.id },
  { id: 'item-three', correct: false, answer: '5', lesson_id: lessonTwo.id },
 ], lessons: [lessonOne, lessonTwo] },
};
const today: PracticeDetail = { id: 'opaque-today-assignment', topic_id: topic.id, title: topic.title,
 date: day, item_count: items.length, status: 'published', items, can_publish: false, can_submit: true };
const draft: PracticeDetail = { ...today, id: 'opaque-new-draft', status: 'draft', student_id: owner,
 can_submit: false, can_publish: true };
function workspace(can_manage: boolean, overrides: Partial<PracticeWorkspace> = {}): PracticeWorkspace {
 return { today: day, can_manage, ready: true, eligible_student_ids: [owner, 'opaque-agent'],
  topics: can_manage ? [topic] : [], assignments: can_manage ? [previous] : [today, previous],
  today_assignments: can_manage ? [] : [today], offset: 0, limit: 30, total: can_manage ? 1 : 2, ...overrides };
}
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
function mockApi(canManage: boolean, overrides: Partial<PracticeWorkspace> = {}) {
 let rows = workspace(canManage, overrides);
 let failDraft = false;
 const details = new Map([previous, today].map(item => [item.id, item]));
 const fetcher = vi.fn(async (path: string, options?: RequestInit): Promise<Response> => {
  const [url] = path.split('?');
  const method = options?.method || 'GET';
  if (method === 'GET' && url === base) return response(rows);
  if (method === 'POST' && url === base + '/drafts') {
   if (failDraft) { failDraft = false; return response({ detail: 'Connection interrupted' }, 503); }
   const body = JSON.parse(String(options?.body));
   const result = { ...draft, date: body.date, source_assignment_id: body.source_assignment_id || null,
    title: body.source_assignment_id ? previous.title : draft.title };
   details.set(result.id, result);
   rows = { ...rows, assignments: [result, ...rows.assignments] };
   return response(result, 201);
  }
  const match = /^\/([^/]+)(?:\/(publish|submit))?$/.exec(url.slice(base.length));
  const detail = match && details.get(match[1]);
  if (!detail) throw new Error('Unexpected synthetic URL ' + path);
  if (method === 'POST' && match?.[2] === 'publish') {
   const result = { ...detail, status: 'published' as const, can_publish: false };
   details.set(result.id, result);
   rows = { ...rows, assignments: rows.assignments.map(item => item.id === result.id ? result : item) };
   return response(result);
  }
  if (method === 'POST' && match?.[2] === 'submit') {
   const result = { ...detail, status: 'submitted' as const, can_submit: false, result: previous.result };
   details.set(result.id, result);
   return response(result);
  }
  return response(detail);
 });
 vi.stubGlobal('fetch', fetcher);
 return { fetcher, details, failNextDraft: () => { failDraft = true; },
  writes: () => fetcher.mock.calls.filter(([, options]) => options?.method === 'POST') };
}
function view(initialAssignmentId?: string | null, initialMembers = members) {
 const askTeacher = vi.fn(), openEnrollment = vi.fn();
 let currentMembers = initialMembers;
 function render() {
  let tree: ReactNode;
  do {
   hooks.dirty = false; hooks.stateIndex = 0; hooks.effectIndex = 0;
   tree = DailyPractice({ courseId, schoolId, members: currentMembers, initialAssignmentId, teacherName: 'Morgan', askTeacher, openEnrollment });
   hooks.pending.splice(0).forEach(effect => effect());
  } while (hooks.dirty);
  return tree!;
 }
 function button(label: string) {
  return nodes(render()).find(node => node.type === 'button' && visible(node).trim() === label)!;
 }
 function control(label: string) {
  const group = nodes(render()).find(node => node.type === 'label' && visible(node).startsWith(label));
  return nodes(group).find(node => node.type === 'input' || node.type === 'select')!;
 }
 return { render, askTeacher, openEnrollment, button, control,
  updateMembers(value: typeof members) { currentMembers = value; render(); },
  click(label: string) { button(label).props.onClick!(); render(); },
  change(label: string, value: string) { control(label).props.onChange!({ target: { value } }); render(); },
  submit() { return nodes(render()).find(node => node.type === 'form')!.props.onSubmit!({ preventDefault: vi.fn() }); },
 };
}
async function expectText(ui: ReturnType<typeof view>, value: string) {
 await vi.waitFor(() => expect(visible(ui.render())).toContain(value));
}
function courseView(auth_provider: User['auth_provider']) {
 const card = {kind:'ai' as const, title:'Grade 3 teacher', biography:'', pronouns:'', picture:null, context_ready:true, available_for_tutoring:false};
 const course: Course = {
  id: courseId, school_id: schoolId, teacher_id: 'course-staff', title: 'Grade 3 Mathematics', description: '',
  curriculum: { state: 'US-NY', subject: 'mathematics', academic_year: '2026-2027', grades: [3], standards_version: null },
  can_manage: false,
  teachers: [{...card,id:'bound-teacher',name:'External Teacher',lesson_mode:'core_agent',lesson_tutoring_available:true},
   {...card,id:'managed-teacher',name:'Morgan',lesson_mode:'managed',lesson_tutoring_available:false}],
 };
 const school = { id: schoolId } as School;
 const user: User = { id: owner, role: 'student', name: 'Synthetic student', auth_provider };
 vi.stubGlobal('fetch', vi.fn(async (path: string) => response(path.includes('/assignments?') ? [] : course)));
 function render() {
  let tree: ReactNode;
  do {
   hooks.dirty = false; hooks.stateIndex = 0; hooks.effectIndex = 0;
   tree = CourseDetail({ id: courseId, school, user, back: () => {} });
   hooks.pending.splice(0).forEach(effect => effect());
  } while (hooks.dirty);
  return tree!;
 }
 const find = <T,>(component: T) => nodes(render()).find(node => node.type === component)?.props as Record<string, unknown> | undefined;
 return { render, find };
}
beforeEach(() => setCsrf('synthetic-practice-csrf'));
afterEach(() => {
 hooks.cleanups.forEach(cleanup => cleanup?.());
 hooks.values = []; hooks.dependencies = []; hooks.cleanups = []; hooks.pending = [];
 hooks.stateIndex = 0; hooks.effectIndex = 0; hooks.dirty = false;
 setCsrf(''); vi.unstubAllGlobals();
});

describe('Grade 3 daily practice interactions', () => {
 it('uses readable enrolled names, allows selecting the school date and topic, previews, and separately publishes the exact draft', async () => {
  const server = mockApi(true), ui = view();
  await expectText(ui, 'Plan daily math');
  const roster = visible(ui.control('Student'));
  expect(roster).toContain('Lex (Alex Example) · @lex-example');
  for (const forbidden of [owner, 'Not Eligible', 'A Student Agent']) expect(roster).not.toContain(forbidden);
  expect(ui.control('Assignment date').props.value).toBe(day);
  expect(ui.button('Generate assignment preview').props.disabled).toBe(true);
  ui.change('Student', owner); ui.change('Math topic', topic.id); ui.change('Assignment date', '2026-11-11');
  ui.change('Number of questions', '4');
  await ui.submit();
  await expectText(ui, 'This is the exact draft.');
  const [draftUrl, draftRequest] = server.writes()[0];
  expect(draftUrl).toBe(base + '/drafts?school_id=' + schoolId);
  expect(JSON.parse(String(draftRequest?.body))).toEqual({
   student_id: owner, topic_id: topic.id, count: 4, date: '2026-11-11', request_id: expect.any(String),
  });
  expect((draftRequest?.headers as Headers).get('X-CSRF-Token')).toBe('synthetic-practice-csrf');
  expect(server.writes()).toHaveLength(1);
  expect(visible(ui.render())).toContain('There are 4 bags with 3 apples each.');
  ui.click('Publish this assignment');
  await expectText(ui, 'Assignment published for November 11, 2026.');
  expect(server.writes().at(-1)?.[0]).toBe(base + '/' + draft.id + '/publish?school_id=' + schoolId);
  expect(JSON.parse(String(server.writes().at(-1)?.[1]?.body))).toEqual({});
  expect(visible(ui.render())).toContain('Scheduled');
 });

 it('keeps the original request identity on interrupted draft creation and changes it for the next requested draft', async () => {
  const server = mockApi(true), ui = view();
  server.failNextDraft();
  await expectText(ui, 'Plan daily math');
  ui.change('Student', owner); ui.change('Math topic', topic.id);
  await ui.submit(); await expectText(ui, 'Connection interrupted');
  await ui.submit(); await expectText(ui, 'This is the exact draft.');
  await ui.submit();
  const identities = server.writes().map(([, options]) => JSON.parse(String(options?.body)).request_id);
  expect(identities[1]).toBe(identities[0]);
  expect(identities[2]).not.toBe(identities[0]);
 });

 it('uses archived questions as a new preview without copying the old result', async () => {
  const server = mockApi(true), ui = view();
  await expectText(ui, 'Assignment history');
  ui.click('Review work');
  await expectText(ui, 'Review submitted answers');
  ui.click('Reuse exact questions');
  expect(ui.control('Assignment date').props.value).toBe(day);
  expect(ui.control('Student').props.value).toBe(owner);
  await ui.submit(); await expectText(ui, 'This is the exact draft.');
  const body = JSON.parse(String(server.writes()[0][1]?.body));
  expect(body).toEqual({ request_id: expect.any(String), date: day, student_id: owner, source_assignment_id: previous.id });
  expect(visible(ui.render())).toContain('The student cannot see it until you publish.');
  expect(nodes(ui.render()).filter(node => node.type === PracticeResultView)).toHaveLength(0);
 });

 it('keeps today separate from a paged history, submits only learner-entered answers, then opens the first matching lesson and hands off its ID', async () => {
  const server = mockApi(false, { assignments: [previous] }), ui = view();
  await expectText(ui, 'Today’s assignment');
  expect(visible(ui.render())).toContain(topic.title);
  ui.click('Start today’s assignment'); await expectText(ui, items[0].prompt);
  expect(ui.button('Submit assignment').props.disabled).toBe(true);
  ui.change('Your answer for question 1', ' 12 ');
  const secondChoice = nodes(ui.render()).find(node => node.type === 'input' && node.props.type === 'radio' && node.props.value === 'b')!;
  secondChoice.props.onChange!({ target: { value: 'b' } });
  ui.change('Your answer for question 3', '5');
  expect(ui.button('Submit assignment').props.disabled).toBe(false);
  await ui.submit();
  await expectText(ui, '1 of 3 correct');
  expect(server.writes()).toHaveLength(1);
  expect(JSON.parse(String(server.writes()[0][1]?.body))).toEqual({
   answers: { 'item-one': '12', 'item-two': 'b', 'item-three': '5' },
  });
  const [first, second] = nodes(ui.render()).filter(node => node.type === 'details' && visible(node).includes('about this lesson'));
  expect(first.props.open).toBe(true); expect(second.props.open).toBe(false);
  expect(visible(first)).toContain('Question 2 · NY-3.OA.1');
  expect(visible(second)).toContain('Question 3 · NY-3.OA.2');
  expect(visible(ui.render())).toContain('These reflection questions are optional and do not change the score.');
  expect(nodes(ui.render()).filter(node => node.type === 'a').map(node => node.props.href)).toEqual([lessonOne.source_url]);
  ui.click('Ask Morgan about this lesson');
  expect(ui.askTeacher).toHaveBeenCalledWith({ id: lessonOne.id, title: lessonOne.title }, today.id);
  expect(ui.button('Submit assignment')).toBeUndefined();
 });

 it('resumes saved results after a chat handoff and withholds lessons if the approval changed', async () => {
  const server = mockApi(false), ui = view(previous.id);
  server.details.set(previous.id, { ...previous, result: { ...previous.result!, lessons: [], lessons_withheld: true } });
  await expectText(ui, '1 of 3 correct');
  expect(visible(ui.render())).toContain('Some lesson material is unavailable');
  expect(visible(ui.render())).not.toContain(lessonOne.text);
  expect(ui.button('Ask Morgan about this lesson')).toBeUndefined();
  ui.click('Back to assignments');
  await expectText(ui, 'Today’s assignment');
 });

 it('ignores an older detail response after another assignment is opened', async () => {
  const server = mockApi(true, { assignments: [draft, previous] }), ui = view();
  let resolveOlder!: (value: Response) => void;
  await expectText(ui, 'Assignment history');
  server.fetcher.mockImplementationOnce(() => new Promise(resolve => { resolveOlder = resolve; }));
  ui.click('Open preview');
  ui.click('Review work');
  await expectText(ui, 'Review submitted answers');
  resolveOlder(response(draft));
  await vi.waitFor(() => expect(visible(ui.render())).not.toContain('This is the exact draft.'));
  expect(visible(ui.render())).toContain('Review submitted answers');
 });

 it('fails closed for unavailable corpus, non-eligible roster, or expired draft', async () => {
  const server = mockApi(true, { ready: false, assignments: [draft] }), ui = view();
  server.details.set(draft.id, { ...draft, can_publish: false });
  await expectText(ui, 'The teaching corpus needs review');
  expect(ui.button('Generate assignment preview')).toBeUndefined();
  ui.click('Open preview'); await expectText(ui, 'This draft cannot be published now.');
  expect(ui.button('Publish this assignment').props.disabled).toBe(true);
  ui.click('Publish this assignment');
  expect(server.writes()).toHaveLength(0);
 });

 it('offers existing enrollment and refreshes eligibility when a newly enrolled student enters the course roster', async () => {
  const server = mockApi(true, { eligible_student_ids: [] });
  const ui = view(null, []);
  await expectText(ui, 'No eligible students are enrolled');
  ui.click('Open course enrollment');
  expect(ui.openEnrollment).toHaveBeenCalledOnce();
  server.fetcher.mockImplementationOnce(async () => response(workspace(true)));
  ui.updateMembers(members);
  await expectText(ui, 'Lex (Alex Example) · @lex-example');
  expect(ui.button('Generate assignment preview').props.disabled).toBe(true);
 });

 it('encodes class and school scope and displays only exact-year Grade 3 support', async () => {
  const server = vi.fn(async (_path: string) => response(today));
  vi.stubGlobal('fetch', server);
  await dailyPracticeDetail('course a/b', 'school & c', 'practice:id');
  expect(server).toHaveBeenCalledWith('/api/courses/course%20a%2Fb/daily-practice/practice%3Aid?school_id=school%20%26%20c',
   expect.objectContaining({ cache: 'no-store', credentials: 'same-origin' }));
  const context = { state: 'US-NY', subject: 'mathematics', grades: [3], academic_year: '2026-2027', standards_version: null };
  expect(isGrade3PracticeCourse(context)).toBe(true);
  expect(isGrade3PracticeCourse({ ...context, grades: [3, 4] })).toBe(false);
  expect(isGrade3PracticeCourse({ ...context, academic_year: '2025-2026' })).toBe(false);
  expect(isGrade3PracticeCourse({ ...context, mode: 'custom' })).toBe(false);
  expect(isGrade3PracticeCourse({ ...context, standards_version: 'wrong-edition' })).toBe(false);
  expect(practiceDateLabel('2026-11-10')).toBe('November 10, 2026');
 });

 it('renders practice material as escaped text and displays neither an answer key nor an unsafe source link', () => {
  const html = renderToStaticMarkup(<PracticeQuestion item={{ ...items[0], prompt: '<img onerror="example">', figure: '<script>figure</script>' }}
   index={0} value="" disabled={false} change={() => {}}/>);
  expect(html).toContain('&lt;img');
  expect(html).toContain('&lt;script&gt;figure&lt;/script&gt;');
  expect(html).not.toContain('<img');
  expect(html).not.toContain('<script');
  expect(html).not.toContain('answer_key');
  const results = renderToStaticMarkup(<PracticeResultView result={previous.result!} items={items}/>);
  expect(results).not.toContain('href="javascript:');
 });

 it('opens the exact Grade 3 local student course assignment-first and routes the selected lesson into the existing chat and back', async () => {
  const { render, find } = courseView('academy');
  await vi.waitFor(() => expect(find(DailyPractice)).toBeDefined());
  expect(find(DailyPractice)?.teacherName).toBe('Morgan');
  expect(find(Tabs)?.value).toBe('practice');
  expect(visible(render())).not.toContain('No assignments yet');
  const link = { id: lessonOne.id, title: lessonOne.title };
  (find(DailyPractice)?.askTeacher as (lesson: typeof link, assignmentId: string) => void)(link, today.id);
  expect(find(Tabs)?.value).toBe('lesson');
  expect(find(LessonTutor)).toMatchObject({ practiceLesson: link, localPractice: true, courseId });
  (find(LessonTutor)?.backToPractice as () => void)();
  expect(find(DailyPractice)?.initialAssignmentId).toBe(today.id);
  (find(Tabs)?.change as (id: string) => void)('coursework');
  expect(find(Tabs)?.value).toBe('coursework');
  expect(visible(render())).toContain('No assignments yet');
 });

 it('keeps legacy Core students on ordinary coursework and the original Lesson Q&A in the same Grade 3 class', async () => {
  const { render, find } = courseView('core');
  await vi.waitFor(() => expect(find(Tabs)?.value).toBe('coursework'));
  expect(find(Tabs)?.items).not.toContainEqual(expect.objectContaining({ id: 'practice' }));
  expect(find(DailyPractice)).toBeUndefined();
  expect(visible(render())).toContain('No assignments yet');
  (find(Tabs)?.change as (id: string) => void)('lesson');
  expect(find(LessonTutor)).toMatchObject({
   localPractice: false, practiceLesson: undefined, backToPractice: undefined, courseId,
  });
 });
});
