import { request, schoolPath, send } from './api';
import type { Curriculum } from './teacher-api';

export type PracticeTopic = { id: string; title: string; unit: number; standards: string[] };
export type PracticeLesson = { id: string; title: string; text: string; standards: string[]; source_url?: string | null };
export type PracticeLessonLink = Pick<PracticeLesson, 'id' | 'title'>;
export type PracticeItem = {
 id: string; prompt: string; options: { id: string; label: string }[]; answer_type: 'choice' | 'text';
 standards: string[]; lesson_id: string; figure?: string | null; practice?: { code: string; prompt: string };
};
export type PracticeScore = { correct: number; total: number };
export type PracticeResult = PracticeScore & {
 items: { id: string; correct: boolean; answer: string; lesson_id: string }[];
 lessons: PracticeLesson[];
 lessons_withheld?: boolean;
};
export type PracticeSummary = {
 id: string; topic_id: string; title: string; date: string; student_id?: string; item_count: number;
 status: 'draft' | 'published' | 'submitted'; source_assignment_id?: string | null; result?: PracticeScore | null;
};
export type PracticeDetail = Omit<PracticeSummary, 'result'> & {
 items: PracticeItem[]; can_submit: boolean; can_publish: boolean; result?: PracticeResult | null;
};
export type PracticeWorkspace = {
 today: string; can_manage: boolean; ready: boolean; eligible_student_ids: string[]; topics: PracticeTopic[];
 assignments: PracticeSummary[]; today_assignments: PracticeSummary[]; offset: number; limit: number; total: number;
};
export type PracticeDraftInput = { request_id: string; student_id: string; date: string } & (
 { topic_id: string; count: number; source_assignment_id?: never } |
 { source_assignment_id: string; topic_id?: never; count?: never }
);

export const practiceHistoryLimit = 30;
export function isGrade3PracticeCourse(value?: (Curriculum & { mode?: string }) | null) {
 return value?.state === 'US-NY' && (value.mode ?? 'state') === 'state'
  && value.subject === 'mathematics' && value.academic_year === '2026-2027'
  && value.grades.length === 1 && value.grades[0] === 3
  && (value.standards_version == null || value.standards_version === 'nys-next-generation-mathematics-2017-updated-2019');
}

function scope(courseId: string, schoolId: string, suffix = '') {
 return schoolPath(`/courses/${encodeURIComponent(courseId)}/daily-practice${suffix}`, schoolId);
}
function assignmentPath(id: string) { return `/${encodeURIComponent(id)}`; }
export function dailyPracticeWorkspace(courseId: string, schoolId: string, offset: number, signal?: AbortSignal) {
 return request<PracticeWorkspace>(scope(courseId, schoolId, `?offset=${offset}&limit=${practiceHistoryLimit}`), { signal });
}
export function dailyPracticeDetail(courseId: string, schoolId: string, id: string, signal?: AbortSignal) {
 return request<PracticeDetail>(scope(courseId, schoolId, assignmentPath(id)), { signal });
}
export function createDailyPracticeDraft(courseId: string, schoolId: string, body: PracticeDraftInput) {
 return send<PracticeDetail>(scope(courseId, schoolId, '/drafts'), body);
}
export function publishDailyPractice(courseId: string, schoolId: string, id: string) {
 return send<PracticeDetail>(scope(courseId, schoolId, `${assignmentPath(id)}/publish`), {});
}
export function submitDailyPractice(courseId: string, schoolId: string, id: string, answers: Record<string, string>) {
 return send<PracticeDetail>(scope(courseId, schoolId, `${assignmentPath(id)}/submit`), { answers });
}

export function practiceDateLabel(date: string) {
 const value = new Date(`${date}T12:00:00Z`);
 return Number.isNaN(value.valueOf()) ? date : new Intl.DateTimeFormat('en-US', { dateStyle: 'long', timeZone: 'UTC' }).format(value);
}
