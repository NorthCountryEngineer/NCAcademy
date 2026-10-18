import type { Curriculum, TeacherCard, TeacherPicture } from './teacher-api';
import type { SchoolCurriculumConfiguration } from './school-curriculum-api';
export type Role = 'admin'|'school'|'teacher'|'student';
export type PrincipalKind = 'human'|'agent';
export type User = {id:string; name:string; role:Role|'instructor'|'parent'; auth_provider?:'core'|'academy'|'local'; must_change_password?:boolean; principal_kind?:PrincipalKind; tenant_id?:string; csrf_token?:string;can_audit?:boolean;permissions?:string[];learning_preferences?:{status:'required'|'complete';version:number}};
export type SchoolCapabilities = {manage_school:boolean;manage_members:boolean;manage_agents:boolean;manage_resources:boolean;create_course:boolean;approve_changes:boolean;manage_connections:boolean};
export type School = {id:string;name:string;description:string;core_resource_id:string|null;role:Role;effective_role:Role;capabilities:SchoolCapabilities;version?:number;curriculum_revision?:number;curriculum?:SchoolCurriculumConfiguration;custom_metadata?:{key:string;value:string}[];logo?:TeacherPicture|null};
export type SchoolMember = {principal_id:string;name:string;role:'school'|'teacher'|'student';active:boolean;principal_kind:PrincipalKind};
export type AgentPlacement = {id:string;name:string;core_agent_id:string;core_binding_id:string;core_consumer_service_id:string;environment:string;task_id:string|null;active:boolean;purpose:string;core_resource_id:string|null;principal_id?:string};
export type SchoolResource = {id:string;school_id:string;can_manage?:boolean;name:string;kind:'text'|'core';content:string;core_resource_type:string|null;core_resource_id:string|null;permission:string;audience:'all'|'teachers';course_id:string|null};
export type ChangeRequest = {id:string;status:string;operation:string;course_id:string;author_id?:string;author_name?:string;author_principal_kind?:PrincipalKind;principal_kind?:PrincipalKind;payload:Record<string,unknown>;created_at?:string;reviewed_at?:string};
export type AuthConfig = {mode:string;demo_users?:User[];login_url?:string;password_login?:boolean};
export type TutorPolicy = {mode:'off'|'curated'|'socratic';connection_id:string|null;model:string|null;max_turns:number;max_input_chars:number;max_output_tokens:number;max_total_tokens:number;require_attempt_chars:number;hints:{id:string;text:string}[];questions:{id:string;text:string}[]};
export type Course = {id:string;school_id:string;teacher_id:string;teacher_name?:string;teacher_principal_kind?:PrincipalKind;course_role?:Role;can_manage?:boolean;title:string;description:string;curriculum?:Curriculum|null;teachers?:TeacherCard[];assignment_count?:number;members?:{user_id:string;name?:string;role:string;principal_kind?:PrincipalKind}[];assignments?:Assignment[]};
export type Assignment = {id:string;course_id:string;module?:string;title:string;instructions:string;tutor_policy:TutorPolicy;draft?:{content:string}|string|null;submissions?:Submission[]};
export type Submission = {id:string;student_id?:string;student_name?:string;content:string;created_at?:string;feedback?:string;review?:string};
export type Turn = {id:string;message?:string;student_message?:string;approved_text?:string;status:string;error?:string;created_at?:string;usage?:Record<string,unknown>;[key:string]:unknown};
export type Connection = {id:string;school_id?:string;name:string;endpoint:string;protocol?:string;models:string[];enabled:boolean;key_configured?:boolean;configured?:boolean;version?:number};
export type AuditSession = {session_id:string;student_id:string;student_name?:string;assignment_id:string|null;assignment_title?:string;session_kind?:'assignment'|'lesson';course_title?:string;turn_count:number;last_activity?:string};
export type Replay = {turns?:Turn[];events?:Record<string,unknown>[];session?:Record<string,unknown>;[key:string]:unknown};
let csrf='';
export function setCsrf(value:string){csrf=value;}
export class ApiError extends Error {constructor(message:string,public status:number){super(message);}}
export async function request<T>(path:string,options:RequestInit={}):Promise<T>{
 const method=(options.method||'GET').toUpperCase();
 const headers=new Headers(options.headers);
 if(options.body)headers.set('Content-Type','application/json');
 if(!['GET','HEAD','OPTIONS'].includes(method)&&csrf)headers.set('X-CSRF-Token',csrf);
 const response=await fetch(`/api${path}`,{...options,method,headers,credentials:'same-origin',cache:'no-store'});
 if(!response.ok){let message=`Request failed (${response.status})`;try{const body=await response.json();const detail=body.detail||body.error;message=typeof detail==='string'?detail:detail?JSON.stringify(detail):message;}catch{/* Empty errors have a status fallback. */}throw new ApiError(message,response.status);}
 if(response.status===204)return undefined as T;
 return response.json() as Promise<T>;
}
export const send=<T,>(path:string,value:unknown,method='POST')=>request<T>(path,{method,body:JSON.stringify(value)});
export const errorText=(error:unknown)=>error instanceof Error?error.message:'The request could not be completed.';
export const roleLabel=(role:string)=>({admin:'Admin',school:'School admin',teacher:'Teacher',instructor:'Teacher',student:'Student',parent:'Parent'}[role]||'No school access');
export const principalLabel=(kind?:PrincipalKind)=>kind==='agent'?'AI agent':'Human';
export function schoolPath(path:string,schoolId:string){return `${path}${path.includes('?')?'&':'?'}school_id=${encodeURIComponent(schoolId)}`;}
export function contextualUser(user:User,school:School):User{return {...user,role:school.effective_role,can_audit:user.can_audit!==false&&school.effective_role!=='student'};}
export function canManageCourse(user:User,course:Course){return course.can_manage??(['admin','school'].includes(user.role)||course.teacher_id===user.id);}
export function stamp(value?:unknown){if(typeof value!=='string'||!value)return 'Not recorded';if(/^\d{4}-\d{2}-\d{2}$/.test(value))return value;const date=new Date(value);return Number.isNaN(date.valueOf())?value:date.toLocaleString();}
export function pretty(value:unknown){return JSON.stringify(value,null,2);}
export function draftText(value:Assignment['draft']){return typeof value==='string'?value:value?.content||'';}
export const blankPolicy=():TutorPolicy=>({mode:'off',connection_id:null,model:null,max_turns:8,max_input_chars:3000,max_output_tokens:80,max_total_tokens:2000,require_attempt_chars:40,hints:[],questions:[]});
export function approvedItems(text:string,prefix:string){return text.split('\n').map(item=>item.trim()).filter(Boolean).map((text,index)=>({id:`${prefix}-${index+1}`,text}));}
export function integrationPayload(form:Connection & {api_key?:string;clear_key?:boolean}){
 return {name:form.name,endpoint:form.endpoint,models:form.models,enabled:form.enabled,...(form.version!==undefined?{version:form.version}:{}),...(form.clear_key?{clear_key:true}:form.api_key?.trim()?{api_key:form.api_key.trim()}:{})};
}
export type TutorIdentity={key:string;message:string;content:string;teacher_profile_id?:string|null};
export function tutorIdentity(previous:TutorIdentity|null,message:string,content:string,makeId:()=>string=()=>crypto.randomUUID(),teacherProfileId?:string|null):TutorIdentity{
 return previous?.message===message&&previous.content===content&&(previous.teacher_profile_id||null)===(teacherProfileId||null)?previous:{key:makeId(),message,content,teacher_profile_id:teacherProfileId||null};
}
