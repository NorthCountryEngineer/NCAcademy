import { request, schoolPath, send } from './api';
import type { CuratedImage, CuratedImageMetadata, CuratedTurn } from './curated-api';

export type HomeworkTeacher={id:string;name:string;images_allowed:boolean;model_label:string|null;max_input_chars:number};
export type HomeworkCourse={id:string;name:string;available?:boolean;teachers:HomeworkTeacher[]};
export type HomeworkSessionSummary={id:string;created_at:string;idempotency_key?:string};
export type HomeworkReference={kind:'course'|'assignment'|'resource';id:string;title:string;course_id:string;url:string};
export type HomeworkTriage={method:string;course_id:string|null;course_name:string|null;teacher_profile_id:string|null;teacher_name:string|null;prompt:string;candidates:HomeworkCourse[]};
export type HomeworkMessage={id:string;message:string;created_at:string;status:'clarify'|'blocked'|'released'|'withheld'|'pending';triage:HomeworkTriage;turn:CuratedTurn|null;references:HomeworkReference[];images:CuratedImageMetadata[];idempotency_key?:string;error?:string|null;resume_available?:boolean};
export type HomeworkSession=HomeworkSessionSummary&{messages:HomeworkMessage[]};
export type HomeworkIndex={courses:HomeworkCourse[];sessions:HomeworkSessionSummary[]};
export type HomeworkInput={message:string;images:CuratedImage[];image_use_confirmed:boolean;course_id?:string;teacher_profile_id?:string};
export type HomeworkTarget={courseId:string;assignmentId?:string;activity?:'resources'|'classroom'};
export type HomeworkIdentity={key:string;digest:string};
const retainedIdentities=new Map<string,HomeworkIdentity>();
export async function retainedHomeworkIdentity(scope:string,input:HomeworkInput):Promise<HomeworkIdentity>{
 const signature=JSON.stringify([scope,input.message,input.course_id||'',input.teacher_profile_id||'',input.images.map(image=>[image.name,image.sha256]),input.image_use_confirmed]),bytes=new TextEncoder().encode(signature),digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),byte=>byte.toString(16).padStart(2,'0')).join('');
 const previous=retainedIdentities.get(digest);if(previous)return previous;
 const value={key:crypto.randomUUID(),digest};retainedIdentities.set(digest,value);if(retainedIdentities.size>100)retainedIdentities.delete(retainedIdentities.keys().next().value!);return value;
}
export function finishHomeworkIdentity(identity:HomeworkIdentity){if(retainedIdentities.get(identity.digest)?.key===identity.key)retainedIdentities.delete(identity.digest);}
export const homeworkIndex=(schoolId:string)=>request<HomeworkIndex>(schoolPath('/homework-help',schoolId));
export const homeworkSession=(id:string)=>request<HomeworkSession>(`/homework-help/sessions/${encodeURIComponent(id)}`);
export const createHomeworkSession=(schoolId:string,key:string)=>send<HomeworkSessionSummary>('/homework-help/sessions',{school_id:schoolId,idempotency_key:key});
export function homeworkPayload(input:HomeworkInput,key:string){
 return {message:input.message,images:input.images.map(image=>({name:image.name,data_url:image.data_url})),image_use_confirmed:input.images.length>0&&input.image_use_confirmed,idempotency_key:key,...(input.course_id?{course_id:input.course_id}:{}),...(input.teacher_profile_id?{teacher_profile_id:input.teacher_profile_id}:{})};
}
export const sendHomeworkMessage=(sessionId:string,input:HomeworkInput,key:string)=>send<HomeworkMessage>(`/homework-help/sessions/${encodeURIComponent(sessionId)}/messages`,homeworkPayload(input,key));
export const resumeHomeworkMessage=(sessionId:string,messageId:string)=>send<HomeworkMessage>(`/homework-help/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/resume`,{});
export function privateHomeworkImageUrl(messageId:string,value?:string|null):string|null{
 const match=value?.match(/^\/api\/homework-help\/sessions\/[A-Za-z0-9_-]{1,64}\/messages\/([A-Za-z0-9_-]{1,64})\/images\/[A-Za-z0-9_-]{1,64}$/);
 return match?.[1]===messageId?value!:null;
}
export function homeworkRequestProblem(input:HomeworkInput,course:HomeworkCourse|undefined,teacher:HomeworkTeacher|undefined):string|null{
 if(!input.message.trim()&&!input.images.length)return 'Explain your question or add a homework photo.';
 if(input.message.length>Math.min(10000,teacher?.max_input_chars||10000))return 'Shorten your question to the teacher’s character limit.';
 if(input.images.length>2)return 'Choose up to two homework photos.';
 if(input.teacher_profile_id&&(!course||!teacher))return 'Choose a current course teacher.';
 if(input.images.length&&teacher&&!teacher.images_allowed)return 'This teacher’s approved policy supports written questions only.';
 if(input.images.length&&teacher&&(!teacher.model_label||!input.image_use_confirmed))return 'Confirm photo use with the named school-approved model.';
 return null;
}
export function homeworkReferenceTarget(reference:HomeworkReference,courseId:string|null):HomeworkTarget|null{
 if(!courseId||reference.course_id!==courseId||!/^[A-Za-z0-9_-]{1,64}$/.test(courseId))return null;
 const course=`/courses/${courseId}`;
 const fragment=`/#course=${encodeURIComponent(courseId)}`;
 if(reference.kind==='course'&&reference.id===courseId&&[course,fragment].includes(reference.url))return {courseId};
 if(!/^[A-Za-z0-9_-]{1,64}$/.test(reference.id))return null;
 if(reference.kind==='assignment'&&[`${course}/assignments/${reference.id}`,`/assignments/${reference.id}`,`${fragment}&assignment=${encodeURIComponent(reference.id)}`].includes(reference.url))return {courseId,assignmentId:reference.id};
 if(reference.kind==='resource'&&[`${course}/resources/${reference.id}`,`${course}/resources`].includes(reference.url))return {courseId,activity:'resources'};
 return null;
}
