import { request, schoolPath, type Course, type User } from './api';

export type EnrollmentCandidate = {id:string;name:string;display_name?:string;preferred_name?:string;username?:string;grade:string;grade_level:number|null;academic_year:string;principal_kind?:'human';role?:'student'};
export type EnrollmentCandidates = {course_id:string;school_id:string;course_grade_levels:number[];selected_grade_levels:number[]|null;grade_filter:string;candidates:EnrollmentCandidate[];total:number;offset:number;limit:number};
export type EnrollmentSearch = {grade:string;search:string;offset:number;limit:number};
export type EnrollmentMember = NonNullable<Course['members']>[number];
export const enrollmentPageSize=50;

export function searchEnrollmentCandidates(schoolId:string,courseId:string,query:EnrollmentSearch,signal?:AbortSignal){
 // Names belong in the protected request body, never in URLs or proxy access logs.
 return request<EnrollmentCandidates>(schoolPath(`/courses/${encodeURIComponent(courseId)}/enrollment-candidates/search`,schoolId),{method:'POST',body:JSON.stringify(query),signal});
}
export function enrollmentGradeLabel(grade:number){return grade===-1?'Pre-K':grade===0?'Kindergarten':`Grade ${grade}`;}
export function courseGradesLabel(grades:number[]){return grades.length?`Class grades · ${grades.map(enrollmentGradeLabel).join(', ')}`:'All grades · class has no grade set';}
export function enrollmentStudentName(student:Pick<EnrollmentCandidate,'display_name'|'preferred_name'|'name'|'username'>){return student.display_name?.trim()||student.preferred_name?.trim()||student.name?.trim()||student.username?.trim()||'Unnamed student';}
export function enrollmentStudentDetails(student:Pick<EnrollmentCandidate,'username'|'grade'|'grade_level'|'academic_year'>){
 const grade=student.grade_level!==null&&student.grade_level!==undefined?enrollmentGradeLabel(student.grade_level):student.grade?.trim()?student.grade.trim():'Grade not set';
 return [student.username?`@${student.username}`:'',grade,student.academic_year].filter(Boolean).join(' · ');
}
export function humanEnrollmentTeachers(users:User[],members:EnrollmentMember[],search:string){
 const enrolled=new Set(members.map(member=>member.user_id)),query=search.trim().toLocaleLowerCase();
 return users.filter(user=>user.principal_kind!=='agent'&&['teacher','instructor'].includes(user.role)&&!enrolled.has(user.id)&&(!query||user.name.toLocaleLowerCase().includes(query)));
}
