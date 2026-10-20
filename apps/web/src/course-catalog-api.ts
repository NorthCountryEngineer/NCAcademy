import { request, schoolPath } from './api';
import type { Curriculum } from './teacher-api';

export type CourseCatalogLevel='secondary'|'prior_to_secondary'|'all_grades';
export type CourseCatalogEntry={course_code:string;title:string;description:string;level:CourseCatalogLevel;subject_area:string;cte:boolean;ap:boolean;ib:boolean;growth_core_indicator:boolean|null;source_row:number};
export type CourseCatalogReference={state:string;academic_year:string;course_code:string;catalog_sha256:string;title?:string;description?:string;level?:CourseCatalogLevel;subject_area?:string;source_url?:string;source_sha256?:string};
export type CourseCatalogPage={state:string;academic_year:string;catalog_sha256:string;title:string;items:CourseCatalogEntry[];total:number;offset:number;limit:number;sources:{title?:string;url?:string;[key:string]:unknown}[];subject_areas:string[];levels:string[];regents_course_codes?:string[];state_assessment_source?:{title?:string;url?:string;[key:string]:unknown}};
export type CourseCatalogQuery={q?:string;subject_area?:string;level?:CourseCatalogLevel|'';offset?:number;limit?:number;state?:string;academic_year?:string;regents_only?:boolean};
export function courseCatalogPath(schoolId:string,query:CourseCatalogQuery={}){
 const params=new URLSearchParams({state:query.state||'US-NY',academic_year:query.academic_year||'2026-2027',offset:String(query.offset??0),limit:String(query.limit??20)});
 if(query.q?.trim())params.set('q',query.q.trim());
 if(query.subject_area)params.set('subject_area',query.subject_area);
 if(query.level)params.set('level',query.level);
 if(query.regents_only)params.set('regents_only','true');
 return schoolPath(`/course-catalog?${params}`,schoolId);
}
export const getCourseCatalog=(schoolId:string,query:CourseCatalogQuery={})=>request<CourseCatalogPage>(courseCatalogPath(schoolId,query));
export function courseCatalogReference(page:CourseCatalogPage,entry:CourseCatalogEntry):CourseCatalogReference{return {state:page.state,academic_year:page.academic_year,course_code:entry.course_code,catalog_sha256:page.catalog_sha256,title:entry.title,description:entry.description,level:entry.level,subject_area:entry.subject_area};}
export function courseCatalogPayload(value:CourseCatalogReference|null){return value?{state:value.state,academic_year:value.academic_year,course_code:value.course_code,catalog_sha256:value.catalog_sha256}:null;}
export const courseCatalogLevelLabel=(level?:string)=>level==='prior_to_secondary'?'Prior to secondary':level==='secondary'?'Secondary':level==='all_grades'?'All grades':'All grade bands';
export function safeCatalogSource(value?:string){if(!value)return null;try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password?url.href:null;}catch{return null;}}

export function courseCreationContextPayload(mode:'catalog'|'custom',selection:CourseCatalogReference|null,configure:boolean,curriculum:Curriculum|null){
 if(mode==='catalog'&&!selection)throw new Error('Choose a New York catalog course, or select Custom course.');
 if(configure&&(!curriculum||!curriculum.grades.length||!curriculum.academic_year||!curriculum.state||!curriculum.subject))throw new Error('Select a state, subject, academic year and at least one grade for optional standards alignment.');
 return {course_catalog:mode==='catalog'?courseCatalogPayload(selection):null,curriculum:configure?curriculum:null};
}
