import { request, send } from './api';
import { blankTutorModel, teacherPath, type TeacherProfile } from './teacher-api';

export const teacherModelTasks = ['simple','reasoning','long_context','image'] as const;
export type TeacherModelTask = typeof teacherModelTasks[number];
export type TeacherModelReference = {connection_id:string;model:string};
export type TeacherModelCandidate = TeacherModelReference & {id:string;label:string;tasks:TeacherModelTask[]};
export type TeacherLessonModelPolicy = {max_turns:number;max_input_chars:number;max_output_tokens:number;max_total_tokens:number;allow_lesson_curation:boolean;allow_assignment_images:boolean;curation_max_output_tokens:number};
export type TeacherModelPolicy = {
 enabled:boolean;primary:TeacherModelReference;lesson_enabled:boolean;homework_enabled:boolean;homework_max_output_tokens:number;
 assignment_images_reviewed:boolean;lesson_policy:TeacherLessonModelPolicy;
 routing:{mode:'fixed'|'preview';candidates:TeacherModelCandidate[];rules:{task:TeacherModelTask;candidate_id:string}[];max_question_chars:number};
};
export type TeacherModelRevision = {revision_id:string;sha256:string;policy:TeacherModelPolicy};
export type TeacherModelConnection = {id:string;name:string;models:string[];enabled:boolean;version?:number};
export type TeacherModelDescriptor = {version:number;draft:TeacherModelRevision|null;active:(TeacherModelRevision & {approval_id:string;connections:TeacherModelConnection[]})|null;draft_pending:boolean;active_available?:boolean;unavailable_reason?:string|null;can_manage:boolean;automatic_routing_enabled:false;homework_images:'school_models_only';pricing_status:'unknown'};
export type TeacherModelPreview = {task:TeacherModelTask;reason_code:string;executed:TeacherModelReference & {label:string;selection_reason:string};suggested:(TeacherModelReference & {label:string;candidate_id:string})|null;automatic_routing_enabled:false;no_provider_calls:true;pricing_status:'unknown'};

export const teacherModelsPath=(schoolId:string,teacherId:string)=>`${teacherPath(schoolId,teacherId)}/models`;
export const getTeacherModels=(schoolId:string,teacherId:string)=>request<TeacherModelDescriptor>(teacherModelsPath(schoolId,teacherId));
export function blankTeacherModelPolicy(profile:Pick<TeacherProfile,'tutor_model'>):TeacherModelPolicy {
 const legacy=profile.tutor_model||blankTutorModel();
 return {enabled:true,primary:{connection_id:legacy.connection_id,model:legacy.model},lesson_enabled:true,homework_enabled:true,homework_max_output_tokens:1500,assignment_images_reviewed:false,
  lesson_policy:{max_turns:legacy.max_turns,max_input_chars:legacy.max_input_chars,max_output_tokens:legacy.max_output_tokens,max_total_tokens:legacy.max_total_tokens,allow_lesson_curation:legacy.allow_lesson_curation===true,allow_assignment_images:false,curation_max_output_tokens:legacy.curation_max_output_tokens??700},
  routing:{mode:'fixed',candidates:[],rules:[],max_question_chars:10000}};
}
export function teacherModelPolicyPayload(value:TeacherModelPolicy):TeacherModelPolicy {
 const reference=(model:TeacherModelReference)=>({connection_id:model.connection_id,model:model.model});
 const lesson=value.lesson_policy;
 return {enabled:value.enabled,primary:reference(value.primary),lesson_enabled:value.lesson_enabled,homework_enabled:value.homework_enabled,homework_max_output_tokens:value.homework_max_output_tokens,assignment_images_reviewed:value.assignment_images_reviewed,
  lesson_policy:{max_turns:lesson.max_turns,max_input_chars:lesson.max_input_chars,max_output_tokens:lesson.max_output_tokens,max_total_tokens:lesson.max_total_tokens,allow_lesson_curation:lesson.allow_lesson_curation,allow_assignment_images:lesson.allow_lesson_curation&&lesson.allow_assignment_images,curation_max_output_tokens:lesson.curation_max_output_tokens},
  routing:{mode:value.routing.mode,max_question_chars:value.routing.max_question_chars,candidates:value.routing.candidates.map(candidate=>({...reference(candidate),id:candidate.id,label:candidate.label.trim(),tasks:teacherModelTasks.filter(task=>candidate.tasks.includes(task))})),rules:value.routing.rules.map(rule=>({task:rule.task,candidate_id:rule.candidate_id}))}};
}
export const saveTeacherModels=(schoolId:string,teacherId:string,policy:TeacherModelPolicy,version:number)=>send<TeacherModelDescriptor>(teacherModelsPath(schoolId,teacherId),{expected_version:version,policy:teacherModelPolicyPayload(policy)},'PUT');
export const approveTeacherModels=(schoolId:string,teacherId:string,descriptor:TeacherModelDescriptor)=>send<TeacherModelDescriptor>(`${teacherModelsPath(schoolId,teacherId)}/approve`,{expected_version:descriptor.version,revision_id:descriptor.draft?.revision_id});
export const previewTeacherModels=(schoolId:string,teacherId:string,text:string)=>send<TeacherModelPreview>(`${teacherModelsPath(schoolId,teacherId)}/preview`,{text:text.trim(),has_images:false});
export const teacherModelTaskLabel=(task:TeacherModelTask)=>({simple:'Short explanations',reasoning:'Reasoning questions',long_context:'Long questions',image:'Homework photos'}[task]);
