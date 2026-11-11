import { request, schoolPath, send } from './api';
import type { CuratedImage, CuratedImageMetadata } from './curated-api';
import type { HomeworkReference, HomeworkSessionSummary } from './homework-api';
import type { TeacherBehavior } from './teacher-api';

export type HomeworkAIModel = {connection_id:string;model:string};
export type HomeworkPrivacyMode = 'deidentified'|'raw_zdr';
export type HomeworkAIPolicy = {
 enabled:boolean;name:string;biography:string;behavior:TeacherBehavior;use_school_teacher_personas:boolean;allow_teacher_models?:boolean;
 allow_local_students?:boolean;local_student_privacy_confirmation?:string;processing_tier?:'standard'|'fast';privacy_mode?:HomeworkPrivacyMode;
 triage:HomeworkAIModel;tutor:HomeworkAIModel;safety:HomeworkAIModel;
 allow_images:boolean;triage_image_capable:boolean;tutor_image_capable:boolean;safety_image_capable:boolean;
 max_input_chars:number;max_output_tokens:number;max_daily_turns:number;max_daily_tokens:number;
 triage_confidence_threshold:number;
};
export type HomeworkAIConfiguration = {
 school_id:string;version:number;revision_id:string|null;approved_revision_id:string|null;
 approved:boolean;available:boolean;live_models_enabled:boolean;configuration:HomeworkAIPolicy|null;
 configuration_sha256:string|null;approval_required:true;boundary_version?:string;reapproval_required?:boolean;unavailable_reason?:string|null;
 global_live_models_enabled?:boolean;homework_models_enabled?:boolean;image_policy_sha256?:string|null;
 openai_student_controls?:{required:boolean;verified:boolean;reason:string|null;expires_at:string|null};
};
export type HomeworkAIStudentConfiguration = Omit<HomeworkAIConfiguration,'configuration'|'approval_required'|'openai_student_controls'> & {configuration:Pick<HomeworkAIPolicy,'enabled'|'name'|'biography'|'allow_images'|'max_input_chars'|'max_output_tokens'|'privacy_mode'>|null;approval_required:boolean;photo_model_labels?:{triage:string;tutor:string;safety:string}|null;private_mode?:boolean;privacy_mode?:HomeworkPrivacyMode;local_only_images?:boolean};
export type HomeworkPrivatePolicy = {enabled:boolean;version:number};
export type HomeworkAICourse = {id:string;name:string;school_id?:string;available?:boolean};
export type HomeworkAICitation = {document_id:string;title:string;course_id?:string;source_url?:string|null;content_sha256?:string;excerpt_sha256?:string};
export type HomeworkAITriage = {method:string;course_id:string|null;course_name:string|null;confidence?:number|null;prompt:string|null;teacher_name?:string|null;teacher_profile_id?:string|null;responder?:'school_assistant'|'teacher';topic?:string|null;candidates:HomeworkAICourse[]};
export type HomeworkAIExecution = {
 phases:{phase:string;model:string;connection_name:string;source:'teacher_policy'|'school_policy';policy_revision_id:string;usage:Record<string,number>}[];
 mode:'fixed'|'preview';selection_reason:string;preview:{task:string|null;reason_code:string;suggested_model:string|null;suggested_label:string|null}|null;
 automatic_routing_enabled:false;estimated_cost:null;pricing_status:'unknown';
};
export type HomeworkAIMessage = {
 id:string;message:string;created_at:string;idempotency_key:string;
 status:'released'|'clarify'|'blocked'|'withheld'|'pending';error:string|null;answer:string|null;
 triage:HomeworkAITriage;citations:HomeworkAICitation[];references:HomeworkReference[];images:CuratedImageMetadata[];
 usage:Record<string,unknown>;execution?:HomeworkAIExecution;resume_available:boolean;reconciliation?:{available:boolean;expected_sha256:string;status:'pending'|'terminal';send_marked:boolean};teacher_profile_id?:string|null;private_mode?:boolean;
 privacy_mode?:HomeworkPrivacyMode;deidentification?:{status:'clarify'|'ready'|'blocked';task_summary:string|null;reason:string;image_read:boolean;action:'hint'|'explain'|'example'|'check_method';stage:number;local_only_images:true};
};
export type HomeworkAIConversationSummary = HomeworkSessionSummary & {title?:string|null};
export type HomeworkAIIndex = {assistant:HomeworkAIStudentConfiguration;courses:HomeworkAICourse[];sessions:HomeworkAIConversationSummary[];next_cursor?:string|null};
export type HomeworkAISession = {session:HomeworkAIConversationSummary;messages:HomeworkAIMessage[]};
export type HomeworkAIInput = {message:string;course_id:string|null;routing_mode?:'auto'|'general';images:CuratedImage[];image_use_confirmed:boolean;previous_image_message_id?:string|null;previous_image_count?:number;expected_image_policy_sha256?:string|null};
export type HomeworkAIPending = {kind:'create'|'message';key:string;session_id:string|null};
export const blankHomeworkAIPolicy = ():HomeworkAIPolicy=>({enabled:false,name:'Homework assistant',biography:'',behavior:{tone:'warm',teaching_style:'socratic',detail:'balanced',question_frequency:'balanced',encouragement:'balanced',instructions:'',boundaries:[]},use_school_teacher_personas:true,allow_teacher_models:false,allow_local_students:false,local_student_privacy_confirmation:"",processing_tier:"standard",privacy_mode:"deidentified",triage:{connection_id:'',model:''},tutor:{connection_id:'',model:''},safety:{connection_id:'',model:''},allow_images:false,triage_image_capable:false,tutor_image_capable:false,safety_image_capable:false,max_input_chars:5000,max_output_tokens:1500,max_daily_turns:30,max_daily_tokens:200000,triage_confidence_threshold:.75});
export const homeworkAIIndex=(schoolId:string,cursor?:string)=>request<HomeworkAIIndex>(schoolPath('/homework-help/ai',schoolId)+(cursor?`&cursor=${encodeURIComponent(cursor)}`:''));
export function mergeHomeworkAIPages<T extends {id:string}>(previous:T[],next:T[]):T[]{return [...new Map([...previous,...next].map(item=>[item.id,item])).values()];}
export const homeworkAIConfiguration=(schoolId:string)=>request<HomeworkAIConfiguration>(schoolPath('/homework-help/configuration',schoolId));
export const homeworkPrivatePolicy=(schoolId:string)=>request<HomeworkPrivatePolicy>(schoolPath('/homework-help/private-policy',schoolId));
export const saveHomeworkPrivatePolicy=(schoolId:string,enabled:boolean,version:number)=>send<HomeworkPrivatePolicy>(schoolPath('/homework-help/private-policy',schoolId),{enabled,expected_version:version},'PUT');
export function homeworkAIConfigurationPayload(policy:HomeworkAIPolicy,version:number){
 const model=(value:HomeworkAIModel)=>({connection_id:value.connection_id,model:value.model}),behavior=policy.behavior;
 return {expected_version:version,enabled:policy.enabled,name:policy.name.trim(),biography:policy.biography.trim(),behavior:{tone:behavior.tone,teaching_style:behavior.teaching_style,detail:behavior.detail,question_frequency:behavior.question_frequency,encouragement:behavior.encouragement,instructions:behavior.instructions||'',boundaries:behavior.boundaries.map(item=>item.trim()).filter(Boolean)},use_school_teacher_personas:policy.use_school_teacher_personas,allow_teacher_models:!!policy.allow_teacher_models,processing_tier:policy.processing_tier||"standard",privacy_mode:policy.privacy_mode||"raw_zdr",allow_local_students:!!policy.allow_local_students,local_student_privacy_confirmation:(policy.local_student_privacy_confirmation||"").trim(),triage:model(policy.triage),tutor:model(policy.tutor),safety:model(policy.safety),allow_images:policy.allow_images,triage_image_capable:policy.triage_image_capable,tutor_image_capable:policy.tutor_image_capable,safety_image_capable:policy.safety_image_capable,max_input_chars:policy.max_input_chars,max_output_tokens:policy.max_output_tokens,max_daily_turns:policy.max_daily_turns,max_daily_tokens:policy.max_daily_tokens,triage_confidence_threshold:policy.triage_confidence_threshold};
}
export const saveHomeworkAIConfiguration=(schoolId:string,policy:HomeworkAIPolicy,version:number)=>send<HomeworkAIConfiguration>(schoolPath('/homework-help/configuration',schoolId),homeworkAIConfigurationPayload(policy,version),'PUT');
export const approveHomeworkAIConfiguration=(schoolId:string,revision:string)=>send<HomeworkAIConfiguration>(schoolPath('/homework-help/configuration/approve',schoolId),{revision_id:revision});
export const createHomeworkAISession=(schoolId:string,key:string)=>send<HomeworkAIConversationSummary>('/homework-help/ai/sessions',{school_id:schoolId,idempotency_key:key});
export const getHomeworkAISession=(id:string)=>request<HomeworkAISession>(`/homework-help/ai/sessions/${encodeURIComponent(id)}`);
export function homeworkAIPayload(input:HomeworkAIInput,key:string){
 return {
  message:input.message,course_id:input.course_id,routing_mode:input.routing_mode||'auto',
  images:input.images.map(image=>({name:image.name,data_url:image.data_url})),
  ...(input.previous_image_message_id?{previous_image_message_id:input.previous_image_message_id}:{}),
  ...(input.expected_image_policy_sha256?{expected_image_policy_sha256:input.expected_image_policy_sha256}:{}),
  image_use_confirmed:!!(input.images.length||input.previous_image_message_id)&&input.image_use_confirmed,
  idempotency_key:key,
 };
}
export const sendHomeworkAIMessage=(sessionId:string,input:HomeworkAIInput,key:string)=>send<HomeworkAIMessage>(`/homework-help/ai/sessions/${encodeURIComponent(sessionId)}/messages`,homeworkAIPayload(input,key));
export function homeworkLocalTaskProcessing(assistant:HomeworkAIStudentConfiguration){return !assistant.private_mode&&(assistant.privacy_mode||assistant.configuration?.privacy_mode)==='deidentified';}
export function homeworkNeedsTaskClarification(item:HomeworkAIMessage){return item.privacy_mode==='deidentified'&&item.status==='clarify'&&item.deidentification?.status==='clarify';}
export function homeworkAIRequestProblem(input:HomeworkAIInput,assistant:HomeworkAIStudentConfiguration){
 const photoCount=input.images.length+(input.previous_image_message_id?input.previous_image_count||1:0);
 if(!assistant.available)return assistant.private_mode?'Private Homework Help is not available yet.':'Your school’s Homework Help AI is not available yet.';
 if(!input.message.trim()&&!input.images.length)return 'Explain your question or add a homework photo.';
 if(input.message.length>(assistant.configuration?.max_input_chars||3000))return 'Shorten your question to the school’s character limit.';
 if(photoCount>2)return 'Choose up to two homework photos.';
 if(photoCount&&!assistant.configuration?.allow_images)return 'Your school’s approved Homework Help AI supports written questions only.';
 if(assistant.private_mode)return input.images.length&&!input.image_use_confirmed?'Confirm that Academy can read your homework photos.':null;
 if(input.previous_image_message_id&&(!assistant.image_policy_sha256||input.expected_image_policy_sha256!==assistant.image_policy_sha256))return 'Refresh Homework Help before using an earlier worksheet with the approved models.';
 if(homeworkLocalTaskProcessing(assistant))return photoCount&&!input.image_use_confirmed?'Confirm that Academy can read your homework photos privately.':null;
 if(photoCount&&(!assistant.photo_model_labels?.triage||!assistant.photo_model_labels?.tutor||!assistant.photo_model_labels?.safety||!input.image_use_confirmed))return 'Confirm photo use with the named school-approved model.';
 return null;
}
export function privateHomeworkAIImageUrl(messageId:string,value?:string|null){const match=value?.match(/^\/api\/homework-help\/ai\/sessions\/[A-Za-z0-9_-]{1,64}\/messages\/([A-Za-z0-9_-]{1,64})\/images\/[A-Za-z0-9_-]{1,64}$/);return match?.[1]===messageId?value!:null;}
export function retainedHomeworkAIRequest(scope:string):HomeworkAIPending|null {
 try{const value=JSON.parse(window.sessionStorage.getItem(`ncacademy:homework-ai:${scope}`)||'null');if(value&&['create','message'].includes(value.kind)&&typeof value.key==='string'&&value.key.length<=100&&(value.session_id===null||typeof value.session_id==='string'&&value.session_id.length<=64))return {kind:value.kind,key:value.key,session_id:value.session_id};}catch{/* Browser storage may be unavailable. In-memory lock still prevents repeat sends. */}return null;
}
export function retainHomeworkAIRequest(scope:string,value:HomeworkAIPending|null){try{const name=`ncacademy:homework-ai:${scope}`;if(value)window.sessionStorage.setItem(name,JSON.stringify({kind:value.kind,key:value.key,session_id:value.session_id}));else window.sessionStorage.removeItem(name);}catch{/* Keep the current in-memory request lock when browser storage is unavailable. */}}

export type HomeworkAuditDeliveryStatus={counts:Record<string,number>;pending_events:number;oldest_pending_at:string|null;last_delivery_at:string|null;mode:'metadata_only';network_from_api:false;archive_independent:true};
export const homeworkAuditDeliveryStatus=(schoolId:string)=>request<HomeworkAuditDeliveryStatus>(schoolPath('/homework-help/audit-delivery',schoolId));
export function homeworkAIContext(value:string):Pick<HomeworkAIInput,'course_id'|'routing_mode'>{return {course_id:value&&value!=='__general__'?value:null,routing_mode:value==='__general__'?'general':'auto'};}

export const closeUncertainHomeworkAIMessage=(sessionId:string,messageId:string,expectedSha256:string,reason:string)=>send<{id:string;status:'withheld';closed_at:string;closed_by:string;already_closed:boolean;uncertainty:string}>(`/homework-help/ai/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/close-uncertain`,{expected_sha256:expectedSha256,reason:reason.trim()});
