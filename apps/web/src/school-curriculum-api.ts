import { request, send, schoolPath, type School } from './api';
import { prepareTeacherPicture, type Curriculum, type CurriculumReviewMetadata, type StandardsCatalog, type TeacherPicture } from './teacher-api';

export type SchoolCurriculumConfiguration={mode:'state'|'custom'|'none';state:string;academic_year:string;grades:number[];subjects:string[];custom_name?:string;custom_description?:string};
export type SchoolCurriculumCatalog=StandardsCatalog & {custom?:{ingestion_supported:boolean};modes?:unknown[]};
export type WorkflowStep={code:string;label:string;status:string;detail?:string};
export type CurriculumPlanCourse={key:string;title:string;grade:number;academic_year:string;subject:string;curriculum:Curriculum;units:{title:string;standard_codes:string[];planning_topics?:string[];source_ids?:string[];objectives?:{code:string;title:string;summary:string;source_id?:string;source_page?:string|number}[]}[];source_ids:string[];sources?:{id:string;title:string;url?:string;reviewed_at?:string}[];catalog_sha256:string;coverage:string;gaps:{code:string;message:string}[];planning_topics?:string[];placement?:string;limitations?:string[];tutoring_scope?:string[];alignment_mapping_available?:boolean;curriculum_review?:CurriculumReviewMetadata};
export type CurriculumRun={id:string;kind:'school_curriculum_run';status:string;current?:boolean;created_at?:string;agent:{name:string;mode:string;live_model_used:boolean};steps:WorkflowStep[];plan:{courses:CurriculumPlanCourse[];gaps:{code:string;message:string}[]};plan_sha256:string};
export type CorpusReceipt={id?:string|null;status?:string;coverage?:string;snapshot_sha256?:string|null;ready_for_teaching?:boolean;gaps?:{code:string;message:string}[];approval?:{status:string}};
export type CurriculumPublication={id:string;kind:'school_curriculum_publication';status?:string;created_at?:string;run_id:string;courses:{key:string;course_id:string;title:string;corpus:CorpusReceipt|null}[]};
export type TeacherCorpusRun={id:string;kind:string;status:string;current?:boolean;created_at?:string;teacher_profile_id?:string;teacher_name?:string;agent?:{name:string;mode?:string;live_model_used?:boolean};steps?:WorkflowStep[];courses?:{course_id:string;title?:string;corpus?:CorpusReceipt|null;corpus_id?:string|null;status?:string;gaps?:{code:string;message:string}[]}[];detail?:string};
export type SchoolWorkflow=CurriculumRun|CurriculumPublication|TeacherCorpusRun;
export type SchoolCurriculumState={school_id:string;school_version:number;curriculum_revision:number;configuration:SchoolCurriculumConfiguration;runs:CurriculumRun[];publications:CurriculumPublication[];capabilities?:Record<string,boolean>};
export const blankSchoolCurriculum=():SchoolCurriculumConfiguration=>({mode:'none',state:'US-NY',academic_year:'2026-2027',grades:[],subjects:[]});
export const MAX_CURRICULUM_SUBJECTS=80;
export const MAX_CURRICULUM_GRADES=14;
export const MAX_CURRICULUM_COMBINATIONS=1120;
export const MAX_PUBLICATION_COURSES=100;
export function allCurriculumSubjects(subjects:{code:string}[]){const codes=[...new Set(subjects.map(subject=>subject.code))];if(codes.length>MAX_CURRICULUM_SUBJECTS)throw new Error(`Choose at most ${MAX_CURRICULUM_SUBJECTS} subjects.`);return codes;}
export function allCurriculumGrades(grades:number[]){const selected=[...new Set(grades)].sort((left,right)=>left-right);if(selected.length>MAX_CURRICULUM_GRADES||selected.some(grade=>!Number.isInteger(grade)||grade< -1||grade>12))throw new Error('Choose grades from Pre-K through grade 12.');return selected;}
export function nextPublicationBatch(run:CurriculumRun,publishedKeys:string[]=[]){const published=new Set(publishedKeys);return run.plan.courses.filter(course=>!published.has(course.key)).slice(0,MAX_PUBLICATION_COURSES).map(course=>course.key);}
export function publicationSelection(run:CurriculumRun,keys:string[],publishedKeys:string[]=[]){const available=new Set(run.plan.courses.map(course=>course.key)),published=new Set(publishedKeys);return [...new Set(keys)].filter(key=>available.has(key)&&!published.has(key)).slice(0,MAX_PUBLICATION_COURSES);}
export const curriculumPath=(schoolId:string)=>`/schools/${encodeURIComponent(schoolId)}/curriculum`;
export const curriculumCatalog=(schoolId?:string)=>request<SchoolCurriculumCatalog>(schoolId?schoolPath('/school-curriculum/catalog',schoolId):'/school-curriculum/catalog');
export const getSchoolCurriculum=(schoolId:string)=>request<SchoolCurriculumState>(curriculumPath(schoolId));
export const getSchoolWorkflows=(schoolId:string)=>request<SchoolWorkflow[]>(`/schools/${encodeURIComponent(schoolId)}/onboarding/runs`);
export const runSchoolCurriculum=(schoolId:string,version:number,idempotencyKey:string)=>send<CurriculumRun>(`${curriculumPath(schoolId)}/runs`,{expected_school_version:version,idempotency_key:idempotencyKey});
export const publishSchoolCurriculum=async(schoolId:string,version:number,run:CurriculumRun,keys:string[],idempotencyKey:string)=>{const selected=[...new Set(keys)];if(!selected.length||selected.length>MAX_PUBLICATION_COURSES)throw new Error(`Select between 1 and ${MAX_PUBLICATION_COURSES} classes per batch.`);return send<CurriculumPublication>(`${curriculumPath(schoolId)}/publish`,{expected_school_version:version,run_id:run.id,plan_sha256:run.plan_sha256,course_keys:selected,idempotency_key:idempotencyKey});};
export function validateSchoolCurriculum(value:SchoolCurriculumConfiguration){
 if(value.mode==='state'&&(!value.state||!value.academic_year||!value.grades.length||!value.subjects.length))throw new Error('Choose state standards, an academic year, at least one subject and at least one grade.');
 if(value.mode==='custom'&&!value.custom_name?.trim())throw new Error('Name the planned custom curriculum. Document ingestion is not available yet.');
 if(value.subjects.length>MAX_CURRICULUM_SUBJECTS)throw new Error(`Choose at most ${MAX_CURRICULUM_SUBJECTS} subjects.`);
 if(value.grades.length>MAX_CURRICULUM_GRADES||value.grades.some(grade=>!Number.isInteger(grade)||grade< -1||grade>12))throw new Error('Choose grades from Pre-K through grade 12.');
 if(value.grades.length*value.subjects.length>MAX_CURRICULUM_COMBINATIONS)throw new Error(`Choose at most ${MAX_CURRICULUM_COMBINATIONS.toLocaleString('en-US')} grade and subject combinations.`);
 return value;
}
export function schoolDetailsPayload(school:School|undefined,value:{name:string;description:string;core_resource_id:string|null;custom_metadata:{key:string;value:string}[];logo:TeacherPicture|null;curriculum:SchoolCurriculumConfiguration},admin:boolean){
 const metadata=value.custom_metadata.map(item=>({key:item.key.trim(),value:item.value.trim()})).filter(item=>item.key||item.value);
 if(metadata.length>30||metadata.some(item=>!item.key||item.key.length>80||item.value.length>1000))throw new Error('Use up to 30 metadata fields with a name (80 characters) and value (1,000 characters).');
 if(new Set(metadata.map(item=>item.key.toLowerCase())).size!==metadata.length)throw new Error('Use a unique name for each metadata field.');
 validateSchoolCurriculum(value.curriculum);
 return {name:value.name.trim(),description:value.description.trim(),...(admin?{core_resource_id:value.core_resource_id?.trim()||null}:{}),...(school?.version!==undefined?{expected_version:school.version}:{}),custom_metadata:metadata,logo:value.logo?{media_type:'image/png',data_base64:value.logo.data_base64}:null,curriculum:{...value.curriculum,grades:[...new Set(value.curriculum.grades)].sort((a,b)=>a-b),subjects:[...new Set(value.curriculum.subjects)]}};
}
export async function prepareSchoolLogo(file:File){return prepareTeacherPicture(file);}
