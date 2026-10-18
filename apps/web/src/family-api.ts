import { request, send } from './api';

export type FamilyKind='student'|'parent';
export type FamilyStatus='active'|'withdrawn'|'graduated'|'archived'|'suspended';
export type FamilyEnrollment={id:string;school_id:string;school_name:string;academic_year:string;grade:string;course_ids:string[];active:boolean};
export type FamilyProfile={username:string;name:string;preferred_name:string;date_of_birth:string|null;email:string;phone:string;notes:string};
export type FamilyAccount=FamilyProfile&{id:string;kind:FamilyKind;status:FamilyStatus;disabled:boolean;must_change_password:boolean;version:number;created_at:string;enrollments:FamilyEnrollment[];student_ids:string[];parent_ids:string[]};
export type PlacementInput={school_id:string;academic_year:string;grade:string;course_ids:string[]};
export type FamilyCreateInput=FamilyProfile&{kind:FamilyKind;password:string;must_change_password:boolean;school_id:string|null;academic_year:string|null;grade:string;course_ids:string[];student_ids:string[]};
export type FamilyHistoryEvent={id:string;event_type:string;created_at:string;actor_id:string;version:number;details:Record<string,unknown>};
export type ChildProgress={id:string;name:string;preferred_name:string;enrollments:FamilyEnrollment[];progress:{assignment_count:number;submitted_assignment_count:number;reviewed_submission_count:number;lesson_count:number}};
export type OwnFamily={account:FamilyAccount;children:ChildProgress[]};
export const familyPath=(id?:string)=>`/admin/family-accounts${id?`/${encodeURIComponent(id)}`:''}`;
export async function listFamilyAccounts(options:{kind?:FamilyKind;school_id?:string;include_inactive?:boolean}={}){const query=new URLSearchParams();if(options.kind)query.set('kind',options.kind);if(options.school_id)query.set('school_id',options.school_id);if(options.include_inactive)query.set('include_inactive','true');return (await request<{accounts:FamilyAccount[]}>(`${familyPath()}${query.size?`?${query}`:''}`)).accounts;}
export const createFamilyAccount=(value:FamilyCreateInput)=>send<FamilyAccount>(familyPath(),value);
export const getFamilyAccount=(id:string)=>request<FamilyAccount>(familyPath(id));
export function createFamilyPayload(profile:FamilyProfile,kind:FamilyKind,password:string,must_change_password:boolean,placement:PlacementInput,student_ids:string[]):FamilyCreateInput{const {expected_version:_,...fields}=profilePayload(profile,0);const enrolled=kind==='student'&&!!placement.school_id;return {...fields,kind,password,must_change_password,school_id:enrolled?placement.school_id:null,academic_year:enrolled?placement.academic_year:null,grade:enrolled?placement.grade:'',course_ids:enrolled?[...placement.course_ids]:[],student_ids:kind==='parent'?[...student_ids]:[]};}
export function profilePayload(profile:FamilyProfile,expected_version:number){return {username:profile.username.trim(),name:profile.name.trim(),preferred_name:profile.preferred_name.trim(),date_of_birth:profile.date_of_birth||null,email:profile.email.trim(),phone:profile.phone.trim(),notes:profile.notes,expected_version};}
export const updateFamilyProfile=(id:string,profile:FamilyProfile,version:number)=>send<FamilyAccount>(familyPath(id),profilePayload(profile,version),'PATCH');
export const resetFamilyPassword=(id:string,new_password:string,must_change_password:boolean,expected_version:number)=>send<FamilyAccount>(`${familyPath(id)}/password`,{new_password,must_change_password,expected_version},'PUT');
export const enrollFamilyStudent=(id:string,placement:PlacementInput,expected_version:number)=>send<FamilyAccount>(`${familyPath(id)}/enrollments`,{...placement,expected_version});
export const transitionFamilyStudent=(id:string,value:{status:FamilyStatus;academic_year:string;grade:string;school_id:string|null;note:string},expected_version:number)=>send<FamilyAccount>(`${familyPath(id)}/lifecycle`,{...value,expected_version});
export const linkFamilyStudents=(id:string,student_ids:string[],expected_version:number)=>send<FamilyAccount>(`${familyPath(id)}/students`,{student_ids,expected_version},'PUT');
export const familyHistory=(id:string)=>request<{events:FamilyHistoryEvent[]}>(`${familyPath(id)}/history`);
export const ownFamily=()=>request<OwnFamily>('/me/family');
export const changeOwnPassword=(current_password:string,new_password:string)=>send('/me/password',{current_password,new_password},'PUT');
export function eligibleParentStudents(accounts:FamilyAccount[]){return accounts.filter(account=>account.kind==='student'&&account.status==='active'&&!account.disabled&&account.enrollments.some(enrollment=>enrollment.active));}
export function hasEnrolledChild(accounts:FamilyAccount[],studentIds:string[]){return eligibleParentStudents(accounts).some(account=>studentIds.includes(account.id));}
export function blankFamilyProfile():FamilyProfile{return {username:'',name:'',preferred_name:'',date_of_birth:'',email:'',phone:'',notes:''};}
export const passwordProblem=(password:string)=>password.length<15?'Use at least 15 characters.':password.length>128?'Use no more than 128 characters.':'';
