import { request, send } from './api';

export type ClassroomStatus={enabled:boolean;connected:boolean;requires_reauth:boolean;linked:boolean;link_id:string|null;connection_version:string|null;reason?:string|null};
export type GoogleClassroomCourse={id:string;name:string;section:string|null;alternate_link:string|null};
export type GoogleClassroomAssignment={id:string;course_id:string;title:string;description:string;due_date:{year:number;month:number;day:number}|null;due_time:{hours?:number;minutes?:number;seconds?:number;nanos?:number}|null;alternate_link:string|null;work_type:string};
export type GoogleClassroomCourses={courses:GoogleClassroomCourse[];next_page_token:string|null};
export type GoogleClassroomWork={assignments:GoogleClassroomAssignment[];course:GoogleClassroomCourse;next_page_token:string|null};
export type ClassroomDisconnect=ClassroomStatus&{revoked:boolean;uncertain:boolean};
const path=(courseId:string)=>`/courses/${encodeURIComponent(courseId)}/classroom`;
const page=(token?:string|null)=>token?`?page_token=${encodeURIComponent(token)}`:'';
export const classroomStatus=(courseId:string)=>request<ClassroomStatus>(path(courseId));
export const connectClassroom=(courseId:string,key:string)=>send<{authorization_url:string}>(`${path(courseId)}/connect`,{idempotency_key:key});
export const disconnectClassroom=(courseId:string)=>send<ClassroomDisconnect>(`${path(courseId)}/disconnect`,{});
export const googleClassroomCourses=(courseId:string,token?:string|null)=>request<GoogleClassroomCourses>(`${path(courseId)}/google-courses${page(token)}`);
export const linkClassroom=(courseId:string,googleCourseId:string,version:string|null,key:string)=>send<ClassroomStatus>(`${path(courseId)}/link`,{google_course_id:googleCourseId,expected_connection_version:version,idempotency_key:key});
export const unlinkClassroom=(courseId:string)=>send<ClassroomStatus>(`${path(courseId)}/unlink`,{});
export const googleClassroomWork=(courseId:string,token?:string|null)=>request<GoogleClassroomWork>(`${path(courseId)}/coursework${page(token)}`);

export function nativeClassroomUrl(value:string|null|undefined):string|null{
 if(typeof value!=='string'||value.length>2048||/[^\x00-\x7f]/.test(value)||/[\x00-\x20\x7f\\]/.test(value)||/%(?:0[0-9a-f]|1[0-9a-f]|7f|5c|2f)/i.test(value))return null;
 try{const url=new URL(value);return url.protocol==='https:'&&value.startsWith('https://classroom.google.com/')&&url.host==='classroom.google.com'&&!url.username&&!url.password&&!url.port&&url.pathname.startsWith('/')&&!url.pathname.startsWith('//')&&!value.includes('?')&&!value.includes('#')?value:null;}catch{return null;}
}
export function classroomAuthorizationUrl(value:string):string|null{
 try{const url=new URL(value);return url.protocol==='https:'&&url.hostname==='accounts.google.com'&&!url.username&&!url.password&&!url.port&&url.pathname==='/o/oauth2/v2/auth'?url.href:null;}catch{return null;}
}
export function classroomDue(assignment:GoogleClassroomAssignment):string{
 const date=assignment.due_date;
 if(!date||![date.year,date.month,date.day].every(Number.isInteger)||date.year<2000||date.year>2200||date.month<1||date.month>12||date.day<1||date.day>31)return 'No due date provided';
 const exact=new Date(Date.UTC(date.year,date.month-1,date.day));
 if(exact.getUTCMonth()!==date.month-1||exact.getUTCDate()!==date.day)return 'No due date provided';
 const formatted=`${date.year}-${String(date.month).padStart(2,'0')}-${String(date.day).padStart(2,'0')}`,time=assignment.due_time;
 if(!time)return `Due ${formatted}`;
 const hours=time.hours??0,minutes=time.minutes??0;
 return Number.isInteger(hours)&&Number.isInteger(minutes)&&hours>=0&&hours<24&&minutes>=0&&minutes<60?`Due ${formatted} at ${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')} UTC`:`Due ${formatted}`;
}
/** Invalidate in-flight private reads before changing an account or class link. */
export class ClassroomAuthorityGuard{
 private generation=0;
 capture(){return this.generation;}
 current(value:number){return value===this.generation;}
 invalidate(){this.generation+=1;return this.generation;}
}
export function classroomCallbackTarget(search:string):{courseId:string;status:'connected'|'error'}|null{
 const query=new URLSearchParams(search),ids=query.getAll('classroom_course'),statuses=query.getAll('classroom_status');
 if(ids.length!==1||statuses.length!==1||!ids[0]||ids[0].length>200||!['connected','error'].includes(statuses[0])||!/^[a-zA-Z0-9_-]+$/.test(ids[0]))return null;
 return {courseId:ids[0],status:statuses[0] as 'connected'|'error'};
}

export type ClassroomOwnConnection={id:string;school_id:string;connected:boolean;requires_reauth:boolean;revoked:boolean;uncertain:boolean};
export const ownClassroomConnections=()=>request<{connections:ClassroomOwnConnection[]}>('/me/google-classroom/connections');
export async function disconnectOwnClassroomConnection(id:string,schoolId:string):Promise<ClassroomOwnConnection>{
 announceClassroomConnectionChange(schoolId,'disconnecting');
 try{const result=await send<ClassroomOwnConnection>(`/me/google-classroom/connections/${encodeURIComponent(id)}/disconnect`,{});announceClassroomConnectionChange(schoolId,'disconnected');return result;}
 catch(error){announceClassroomConnectionChange(schoolId,'uncertain');throw error;}
}


export type ClassroomConnectionChange={schoolId:string;phase:'disconnecting'|'disconnected'|'uncertain'};
const connectionListeners=new Set<(change:ClassroomConnectionChange)=>void>();
/** Local UI invalidation only: carries no Google identity, content or credentials. */
export function announceClassroomConnectionChange(schoolId:string,phase:ClassroomConnectionChange['phase']){
 for(const listener of connectionListeners)listener({schoolId,phase});
}
export function onClassroomConnectionChange(listener:(change:ClassroomConnectionChange)=>void){
 connectionListeners.add(listener);return ()=>{connectionListeners.delete(listener);};
}
