import { request, schoolPath, send } from './api';
export type LessonCitation = {document_id:string;title:string;source_url?:string|null;source_page?:string|number|null;content_sha256:string;excerpt_sha256:string;excerpt_start:number;excerpt_end:number};
export type LessonTurn = {id:string;session_id:string;teacher_profile_id:string;teacher_name:string;message:string;status:'pending'|'blocked'|'released'|'withheld';approved_text:string|null;citations:LessonCitation[];error:string|null;usage?:{prompt_tokens?:number;completion_tokens?:number;total_tokens?:number};created_at?:string};
export type LessonIdentity = {key:string;course_id:string;teacher_profile_id:string;message:string};
export function lessonIdentity(previous:LessonIdentity|null,courseId:string,teacherId:string,message:string,makeId:()=>string=()=>crypto.randomUUID()):LessonIdentity{
 return previous?.course_id===courseId&&previous.teacher_profile_id===teacherId&&previous.message===message?previous:{key:makeId(),course_id:courseId,teacher_profile_id:teacherId,message};
}
export const lessonPath=(courseId:string,schoolId:string)=>schoolPath(`/courses/${encodeURIComponent(courseId)}/teacher-tutor`,schoolId);
export const lessonHistory=(courseId:string,schoolId:string)=>request<LessonTurn[]>(lessonPath(courseId,schoolId));
export const requestLesson=(identity:LessonIdentity,schoolId:string)=>send<LessonTurn>(lessonPath(identity.course_id,schoolId),{teacher_profile_id:identity.teacher_profile_id,message:identity.message,idempotency_key:identity.key});
