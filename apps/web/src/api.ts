export type User = {id:string; name:string; role:'admin'|'instructor'|'student'; tenant_id?:string; csrf_token?:string;can_audit?:boolean;permissions?:string[]};
export type AuthConfig = {mode:string;demo_users?:User[];login_url?:string};
export type TutorPolicy = {mode:'off'|'curated'|'socratic';connection_id:string|null;model:string|null;max_turns:number;max_input_chars:number;max_output_tokens:number;max_total_tokens:number;require_attempt_chars:number;hints:{id:string;text:string}[];questions:{id:string;text:string}[]};
export type Course = {id:string;title:string;description:string;assignment_count?:number;members?:{user_id:string;name?:string;role:string}[];assignments?:Assignment[]};
export type Assignment = {id:string;course_id:string;module?:string;title:string;instructions:string;tutor_policy:TutorPolicy;draft?:{content:string}|string|null;submissions?:Submission[]};
export type Submission = {id:string;student_id?:string;student_name?:string;content:string;created_at?:string;feedback?:string;review?:string};
export type Turn = {id:string;message?:string;student_message?:string;approved_text?:string;status:string;error?:string;created_at?:string;usage?:Record<string,unknown>;[key:string]:unknown};
export type Connection = {id:string;name:string;endpoint:string;protocol?:string;models:string[];enabled:boolean;key_configured?:boolean;configured?:boolean;version?:number};
export type AuditSession = {session_id:string;student_id:string;student_name?:string;assignment_id:string;assignment_title?:string;turn_count:number;last_activity?:string};
export type Replay = {turns?:Turn[];events?:Record<string,unknown>[];session?:Record<string,unknown>;[key:string]:unknown};
let csrf='';
export function setCsrf(value:string){csrf=value;}
export class ApiError extends Error {constructor(message:string,public status:number){super(message);}}
export async function request<T>(path:string,options:RequestInit={}):Promise<T>{
 const method=(options.method||'GET').toUpperCase();
 const headers=new Headers(options.headers);
 if(options.body)headers.set('Content-Type','application/json');
 if(!['GET','HEAD','OPTIONS'].includes(method)&&csrf)headers.set('X-CSRF-Token',csrf);
 const response=await fetch(`/api${path}`,{...options,method,headers,credentials:'same-origin'});
 if(!response.ok){let message=`Request failed (${response.status})`;try{const body=await response.json();const detail=body.detail||body.error;message=typeof detail==='string'?detail:detail?JSON.stringify(detail):message;}catch{/* Empty errors have a status fallback. */}throw new ApiError(message,response.status);}
 if(response.status===204)return undefined as T;
 return response.json() as Promise<T>;
}
export const send=<T,>(path:string,value:unknown,method='POST')=>request<T>(path,{method,body:JSON.stringify(value)});
export const errorText=(error:unknown)=>error instanceof Error?error.message:'The request could not be completed.';
export function stamp(value?:unknown){if(typeof value!=='string'||!value)return 'Not recorded';const date=new Date(value);return Number.isNaN(date.valueOf())?value:date.toLocaleString();}
export function pretty(value:unknown){return JSON.stringify(value,null,2);}
export function draftText(value:Assignment['draft']){return typeof value==='string'?value:value?.content||'';}
export const blankPolicy=():TutorPolicy=>({mode:'off',connection_id:null,model:null,max_turns:8,max_input_chars:3000,max_output_tokens:80,max_total_tokens:2000,require_attempt_chars:40,hints:[],questions:[]});
export function approvedItems(text:string,prefix:string){return text.split('\n').map(item=>item.trim()).filter(Boolean).map((text,index)=>({id:`${prefix}-${index+1}`,text}));}
export function integrationPayload(form:Connection & {api_key?:string;clear_key?:boolean}){
 return {name:form.name,endpoint:form.endpoint,models:form.models,enabled:form.enabled,...(form.version!==undefined?{version:form.version}:{}),...(form.clear_key?{clear_key:true}:form.api_key?.trim()?{api_key:form.api_key.trim()}:{})};
}
export type TutorIdentity={key:string;message:string;content:string};
export function tutorIdentity(previous:TutorIdentity|null,message:string,content:string,makeId:()=>string=()=>crypto.randomUUID()):TutorIdentity{
 return previous?.message===message&&previous.content===content?previous:{key:makeId(),message,content};
}
