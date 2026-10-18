import { request, schoolPath, send } from './api';
import type { LessonCitation } from './lesson-api';
import type { LessonVisual } from './LessonMedia';

export type LessonPreferences = {pace:'gentle'|'standard';formats:('read'|'visual'|'listen')[]};
export type CuratedImage = {name:string;data_url:string;sha256:string;width:number;height:number};
export type CuratedImageMetadata = {id:string;name:string;sha256:string;width:number;height:number;url?:string|null};
export type CuratedLessonSection = {id:string;document_id:string;title:string;text:string;citation:LessonCitation;visual:LessonVisual|null;video?:{resource_id:string;course_id:string;title:string};reflection_prompt:string};
export type CuratedLessonBundle = {version:'curated-lesson-v1';title:string;teacher_name:string;sections:CuratedLessonSection[];takeaway_prompt:string;narration_text:string};
export type CuratedTurn = {id:string;session_id:string;activity:'curated_lesson';teacher_profile_id:string;teacher_name:string;message:string;status:'pending'|'blocked'|'released'|'withheld';approved_text:null;citations:LessonCitation[];lesson:CuratedLessonBundle|null;error:string|null;usage?:{total_tokens?:number};created_at?:string;budget_day?:string;preferences:LessonPreferences;images:CuratedImageMetadata[]};
export type CuratedIdentity = {key:string;signature:string};
export type CuratedRequest = {teacher_profile_id:string;message:string;images:CuratedImage[];image_use_confirmed:boolean;preferences:LessonPreferences};
export const defaultLessonPreferences = ():LessonPreferences=>({pace:'standard',formats:['read','visual']});
const retainedIdentities = new Map<string,CuratedIdentity>();
export function curatedIdentity(previous:CuratedIdentity|null,courseId:string,input:CuratedRequest,makeId:()=>string=()=>crypto.randomUUID()):CuratedIdentity{
 const signature=JSON.stringify([courseId,input.teacher_profile_id,input.message,input.preferences.pace,[...input.preferences.formats].sort(),input.images.map(image=>[image.name,image.sha256]),input.image_use_confirmed]);
 return previous?.signature===signature?previous:{key:makeId(),signature};
}
// Retain only an opaque digest and request key across component remounts, including StrictMode.
// Source text and photo bytes remain in the composing component, not this registry.
export async function retainedCuratedIdentity(scope:string,input:CuratedRequest):Promise<CuratedIdentity>{
 const candidate=curatedIdentity(null,scope,input),bytes=new TextEncoder().encode(candidate.signature);
 const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),byte=>byte.toString(16).padStart(2,'0')).join('');
 const prior=retainedIdentities.get(digest);if(prior)return prior;
 const identity={key:candidate.key,signature:digest};retainedIdentities.set(digest,identity);
 if(retainedIdentities.size>100)retainedIdentities.delete(retainedIdentities.keys().next().value!);
 return identity;
}
export function finishCuratedIdentity(identity:CuratedIdentity){if(retainedIdentities.get(identity.signature)?.key===identity.key)retainedIdentities.delete(identity.signature);}
export const curatedPath=(courseId:string,schoolId:string)=>schoolPath(`/courses/${encodeURIComponent(courseId)}/curated-lessons`,schoolId);
export const curatedHistory=(courseId:string,schoolId:string)=>request<CuratedTurn[]>(curatedPath(courseId,schoolId));
export const requestCuratedLesson=(courseId:string,schoolId:string,input:CuratedRequest,identity:CuratedIdentity)=>send<CuratedTurn>(curatedPath(courseId,schoolId),{teacher_profile_id:input.teacher_profile_id,message:input.message,images:input.images.map(image=>({name:image.name,data_url:image.data_url})),image_use_confirmed:input.image_use_confirmed,preferences:{pace:input.preferences.pace,formats:[...input.preferences.formats]},idempotency_key:identity.key});
export type LessonProgressEvent = 'viewed'|'read_aloud'|'reflection';
export const recordLessonProgress=(courseId:string,schoolId:string,lessonId:string,sectionId:string,event:LessonProgressEvent,response?:string)=>send(schoolPath(`/courses/${encodeURIComponent(courseId)}/curated-lessons/${encodeURIComponent(lessonId)}/progress`,schoolId),{section_id:sectionId,event,...(event==='reflection'?{response:response||''}:{})});
export function privateImageUrl(value?:string|null):string|null{
 return value&&/^\/api\/courses\/[A-Za-z0-9_-]{1,64}\/curated-lessons\/[A-Za-z0-9_-]{1,64}\/images\/[A-Za-z0-9_-]{1,64}$/.test(value)?value:null;
}
export function photoName(name:string):string{
 const base=(name.split(/[\\/]/).pop()||'assignment').replace(/[\u0000-\u001f\u007f]/g,'').replace(/\.[^.]*$/,'').trim().slice(0,100)||'assignment';return `${base}.jpg`;
}
export async function prepareAssignmentImage(file:File):Promise<CuratedImage>{
 if(!file.size||file.size>5*1024*1024)throw new Error('Choose a PNG, JPEG or WebP photo up to 5 MB.');
 const header=new Uint8Array(await file.slice(0,12).arrayBuffer()),png=[137,80,78,71,13,10,26,10].every((byte,index)=>header[index]===byte),jpeg=header[0]===255&&header[1]===216&&header[2]===255,webp=String.fromCharCode(...header.slice(0,4))==='RIFF'&&String.fromCharCode(...header.slice(8,12))==='WEBP';
 const actual=png?'image/png':jpeg?'image/jpeg':webp?'image/webp':'';
 if(!actual||file.type&&file.type!==actual)throw new Error('Choose a valid PNG, JPEG or WebP photo. PDFs, SVG and other files are not accepted.');
 const bitmap=await createImageBitmap(file).catch(()=>{throw new Error('This photo could not be read. Choose another image.');});
 try{
  if(!bitmap.width||!bitmap.height||bitmap.width>8192||bitmap.height>8192||bitmap.width*bitmap.height>32000000)throw new Error('This photo is too large to prepare. Choose an image under 8192 pixels on each side.');
  let edge=Math.min(1280,Math.max(bitmap.width,bitmap.height));
  for(let attempt=0;attempt<10;attempt++){
   const ratio=edge/Math.max(bitmap.width,bitmap.height),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
   const context=canvas.getContext('2d');if(!context)throw new Error('Photo preparation is unavailable in this browser.');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0,canvas.width,canvas.height);
   const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('This photo could not be prepared.')),'image/jpeg',Math.max(.48,.84-attempt*.06)));
   if(blob.type==='image/jpeg'&&blob.size<=96*1024){const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);const sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),byte=>byte.toString(16).padStart(2,'0')).join('');return {name:photoName(file.name),data_url:`data:image/jpeg;base64,${btoa(binary)}`,sha256,width:canvas.width,height:canvas.height};}
   edge=Math.max(256,Math.floor(edge*.8));
  }
  throw new Error('This photo cannot fit the upload limit. Try a closer crop or a simpler image.');
 }finally{bitmap.close();}
}
