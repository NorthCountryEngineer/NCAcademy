import { request, send, type School, type User } from './api';
import type { TeacherCorpusRun } from './school-curriculum-api';

export type Curriculum = {state:string;subject:string;grades:number[];academic_year:string;standards_version:string|null};
export type StandardsSubject = {code:string;name:string;grades:number[];academic_years:string[];standards_version:string;mapping_version?:string};
export type StandardsCatalog = {states:{code:string;name:string;subjects:StandardsSubject[]}[]};
export type CorpusDocument = {id:string;kind:'standard'|'instructional_supplement'|'source_reference';title:string;standard_code?:string;summary?:string;content?:string;content_sha256:string;source_id:string;source_url?:string;source_page?:string|number};
export type CorpusDescriptor = {id:string|null;course_id:string;school_id:string;status:'aligned'|'unverified'|'stale';coverage:'partial'|'none';curriculum:Curriculum|null;standards_version:string|null;mapping_version:string|null;snapshot_sha256:string|null;configuration_sha256:string;created_at?:string;reviewed_at?:string;documents:CorpusDocument[];sources:{id:string;title:string;url?:string;content_sha256?:string;retrieved_at?:string;reviewed_at?:string}[];gaps:{code:string;message:string}[];approval:{status:'pending'|'approved'|'stale';approved_by?:string;approved_at?:string};ready_for_teaching:boolean;can_refresh:boolean;can_approve:boolean;can_retrieve:boolean;instructional_material_count?:number;source_reference_count?:number;local_material_count?:number};
export type TeacherBehavior = {tone:'warm'|'neutral'|'formal';teaching_style:'socratic'|'guided_practice'|'direct'|'adaptive';detail:'concise'|'balanced'|'detailed';question_frequency:'low'|'balanced'|'high';encouragement:'restrained'|'balanced'|'frequent';boundaries:string[];instructions?:string};
export type TeacherPicture = {media_type:'image/png';data_base64:string;sha256?:string;width?:number;height?:number};
export type TeacherTutorModel = {connection_id:string;model:string;max_turns:number;max_input_chars:number;max_output_tokens:number;max_total_tokens:number;allow_lesson_curation?:boolean;allow_assignment_images?:boolean;curation_max_output_tokens?:number};
export type TeacherCard = {id:string;kind:'human'|'ai';name:string;title:string;pronouns:string;biography:string;picture:TeacherPicture|null;context_ready:boolean;available_for_tutoring:boolean;lesson_tutoring_available?:boolean;lesson_unavailable_reason?:string|null;lesson_mode?:'managed'|'core_agent'|null;lesson_max_input_chars?:number|null;lesson_max_turns?:number|null;curation_available?:boolean;curation_images_allowed?:boolean;curation_model_label?:string|null;curation_unavailable_reason?:string|null;curation_max_input_chars?:number|null};
export type TeacherCourse = {id:string;title:string;curriculum:Curriculum|null;corpus:Pick<CorpusDescriptor,'id'|'status'|'coverage'|'snapshot_sha256'|'configuration_sha256'|'standards_version'|'mapping_version'|'approval'|'ready_for_teaching'|'gaps'>|null;has_teaching_authority:boolean};
export type TeacherProfile = {id:string;school_id:string;kind:'human'|'ai';name:string;biography:string;pronouns:string;title:string;contact_email:string|null;expertise:string[];picture:TeacherPicture|null;ai_behavior:TeacherBehavior|null;tutor_model?:TeacherTutorModel|null;principal_id:string|null;agent_placement_id:string|null;status:'pending'|'draft'|'ready'|'archived';version:number;course_ids:string[];assigned_courses:TeacherCourse[];can_manage:boolean;authority_granted:false;latest_onboarding_run?:TeacherCorpusRun;onboarding_run?:TeacherCorpusRun;approved_revision_id?:string|null;approved_version?:number|null;approved_hash?:string|null;approval_current?:boolean;activation:{identity_linked:boolean;identity_eligible:boolean;agent_admission_enabled:boolean;live_models_enabled:boolean;authority_granted:false;runtime_active:false}};
export type TeacherMetadata = Pick<TeacherProfile,'name'|'biography'|'pronouns'|'title'|'contact_email'|'expertise'|'picture'|'ai_behavior'|'tutor_model'>;
export type TeacherDraft = TeacherMetadata & {kind:TeacherProfile['kind']};

export const blankBehavior = ():TeacherBehavior=>({tone:'warm',teaching_style:'socratic',detail:'balanced',question_frequency:'balanced',encouragement:'balanced',boundaries:[],instructions:''});
export const blankTutorModel = ():TeacherTutorModel=>({connection_id:'',model:'',max_turns:8,max_input_chars:3000,max_output_tokens:80,max_total_tokens:20000,allow_lesson_curation:false,allow_assignment_images:false,curation_max_output_tokens:700});
export const blankTeacher = ():TeacherDraft=>({kind:'human',name:'',biography:'',pronouns:'',title:'',contact_email:null,expertise:[],picture:null,ai_behavior:null,tutor_model:null});
export const canManageTeachers = (school:School,user:User)=>user.principal_kind!=='agent'&&['admin','school'].includes(school.effective_role)&&school.capabilities.manage_members&&school.capabilities.manage_school;
export const approvedTeacher = (teacher:TeacherProfile)=>teacher.approval_current!==false&&teacher.approved_version===teacher.version&&!!teacher.approved_revision_id;
export function teacherMetadata(value:TeacherDraft):TeacherMetadata{
 const behavior=value.ai_behavior||blankBehavior();
 return {name:value.name.trim(),biography:value.biography.trim(),pronouns:value.pronouns.trim(),title:value.title.trim(),contact_email:value.contact_email?.trim()||null,
  expertise:[...new Set(value.expertise.map(item=>item.trim()).filter(Boolean))],picture:value.picture?{media_type:'image/png',data_base64:value.picture.data_base64}:null,
  ai_behavior:value.kind==='ai'?{tone:behavior.tone,teaching_style:behavior.teaching_style,detail:behavior.detail,question_frequency:behavior.question_frequency,encouragement:behavior.encouragement,instructions:(behavior.instructions||'').trim(),boundaries:behavior.boundaries.map(item=>item.trim()).filter(Boolean)}:null,
  tutor_model:value.kind==='ai'&&value.tutor_model?{connection_id:value.tutor_model.connection_id,model:value.tutor_model.model,max_turns:value.tutor_model.max_turns,max_input_chars:value.tutor_model.max_input_chars,max_output_tokens:value.tutor_model.max_output_tokens,max_total_tokens:value.tutor_model.max_total_tokens,allow_lesson_curation:value.tutor_model.allow_lesson_curation===true,allow_assignment_images:value.tutor_model.allow_lesson_curation===true&&value.tutor_model.allow_assignment_images===true,curation_max_output_tokens:value.tutor_model.curation_max_output_tokens??700}:null};
}
export const teacherPath=(schoolId:string,id?:string)=>`/schools/${encodeURIComponent(schoolId)}/teachers${id?`/${encodeURIComponent(id)}`:''}`;
export const listTeachers=(schoolId:string)=>request<TeacherProfile[]>(teacherPath(schoolId));
export const saveTeacher=(schoolId:string,value:TeacherDraft,initial?:TeacherProfile)=>send<TeacherProfile>(teacherPath(schoolId,initial?.id),initial?{...teacherMetadata(value),expected_version:initial.version}:{...teacherMetadata(value),kind:value.kind},initial?'PUT':'POST');
export const approveTeacher=(schoolId:string,profile:TeacherProfile)=>send<TeacherProfile>(`${teacherPath(schoolId,profile.id)}/approve`,{expected_version:profile.version});

const MAX_PICTURE_BYTES=64*1024;
export async function prepareTeacherPicture(file:File):Promise<TeacherPicture>{
 if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw new Error('Choose a PNG, JPEG or WebP photo. Animated images and SVG are not supported.');
 if(!file.size||file.size>5*1024*1024)throw new Error('Choose a photo smaller than 5 MB.');
 const header=new Uint8Array(await file.slice(0,12).arrayBuffer());
 const png=[137,80,78,71,13,10,26,10].every((byte,index)=>header[index]===byte),jpeg=header[0]===255&&header[1]===216&&header[2]===255,webp=String.fromCharCode(...header.slice(0,4))==='RIFF'&&String.fromCharCode(...header.slice(8,12))==='WEBP';
 if(!(file.type==='image/png'&&png||file.type==='image/jpeg'&&jpeg||file.type==='image/webp'&&webp))throw new Error('Choose a valid PNG, JPEG or WebP photo. The file contents do not match its image type.');
 const bitmap=await createImageBitmap(file).catch(()=>{throw new Error('This photo could not be read. Choose another PNG, JPEG or WebP image.');});
 try{
  if(!bitmap.width||!bitmap.height||bitmap.width>8192||bitmap.height>8192||bitmap.width*bitmap.height>32000000)throw new Error('This photo is too large. Choose an image smaller than 8192 pixels on each side.');
  let size=Math.min(256,Math.max(bitmap.width,bitmap.height));
  for(let attempt=0;attempt<8;attempt++){
   const ratio=size/Math.max(bitmap.width,bitmap.height),canvas=document.createElement('canvas');
   canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
   const context=canvas.getContext('2d');if(!context)throw new Error('Photo preparation is unavailable in this browser.');
   context.drawImage(bitmap,0,0,canvas.width,canvas.height);
   const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('The photo could not be prepared.')),'image/png'));
   if(blob.size<=MAX_PICTURE_BYTES){const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);return {media_type:'image/png',data_base64:btoa(binary)};}
   size=Math.max(32,Math.floor(size*.75));
  }
  throw new Error('This photo cannot fit the picture limit. Try a simpler or smaller image.');
 }finally{bitmap.close();}
}
