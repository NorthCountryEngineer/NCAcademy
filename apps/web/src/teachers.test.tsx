import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { navigationForRole } from './App';
import { CorpusReview, CourseCorpusPanel, academicContextPayload, catalogDefault } from './CourseCorpus';
import { CourseTeachingTeam, StudentTeacherChoices } from './Curriculum';
import { TeacherActivation, TeacherApproval, TeacherCourseRow, TeacherDetail, TeacherEditor, Teachers } from './Teachers';
import { tutorIdentity, type School, type User } from './api';
import { approvedTeacher, blankBehavior, canManageTeachers, prepareTeacherPicture, saveTeacher, teacherMetadata, type CorpusDescriptor, type TeacherCard, type TeacherProfile } from './teacher-api';

afterEach(()=>vi.unstubAllGlobals());
const school=(role:School['effective_role']='school'):School=>({id:'school-a',name:'North School',description:'',core_resource_id:null,role,effective_role:role,capabilities:{manage_school:['admin','school'].includes(role),manage_members:['admin','school'].includes(role),manage_agents:['admin','school'].includes(role),manage_resources:role!=='student',create_course:role!=='student',approve_changes:['admin','school'].includes(role),manage_connections:['admin','school'].includes(role)}});
const admin:User={id:'human-a',name:'Administrator',role:'school',principal_kind:'human'};
const curriculum={state:'US-NY',subject:'environmental_science',grades:[10],academic_year:'2026-2027',standards_version:null};
const teacher=():TeacherProfile=>({id:'profile-a',school_id:'school-a',kind:'ai',name:'River',title:'Environmental science teacher',pronouns:'they/them',biography:'Explore evidence from local ecosystems.',contact_email:'PRIVATE_CONTACT@example.test',expertise:['Environmental science','Fieldwork'],picture:null,ai_behavior:blankBehavior(),principal_id:'PRIVATE_PRINCIPAL',agent_placement_id:'PRIVATE_PLACEMENT',status:'ready',version:3,course_ids:[],assigned_courses:[],can_manage:true,authority_granted:false,approved_revision_id:'review-a',approved_version:3,activation:{identity_linked:false,identity_eligible:false,agent_admission_enabled:false,live_models_enabled:false,authority_granted:false,runtime_active:false}});
const corpus=():CorpusDescriptor=>({id:'snapshot-a',course_id:'class-a',school_id:'school-a',status:'aligned',coverage:'partial',curriculum,standards_version:'nyssls-2016',mapping_version:'reviewed-v1',snapshot_sha256:'a'.repeat(64),configuration_sha256:'b'.repeat(64),documents:[{id:'standard',kind:'standard',title:'Evaluate ecosystems',standard_code:'HS-LS2-7',summary:'Review evidence about ecosystem stability.',content_sha256:'c'.repeat(64),source_id:'nysed'},{id:'source',kind:'source_reference',title:'Carbon cycle',content:'Reviewed federal subject source.',content_sha256:'d'.repeat(64),source_id:'nasa',source_url:'https://science.nasa.gov/earth/'}],sources:[{id:'nysed',title:'New York State Science Learning Standards',url:'https://www.nysed.gov/'}],gaps:[{code:'partial',message:'This is a reviewed subset for high school grades 9–12.'}],approval:{status:'pending'},ready_for_teaching:false,can_refresh:true,can_approve:true,can_retrieve:false});

describe('teacher onboarding authority',()=>{
 it('limits teacher management to humans with contextual school administration capabilities',()=>{
  expect(canManageTeachers(school(),admin)).toBe(true);
  expect(canManageTeachers(school('teacher'),admin)).toBe(false);
  expect(canManageTeachers(school('student'),{...admin,role:'admin'})).toBe(false);
  expect(canManageTeachers(school(),{...admin,principal_kind:'agent'})).toBe(false);
  expect(navigationForRole('school',true,false,false).some(item=>item.id==='teachers')).toBe(false);
  expect(navigationForRole('teacher').some(item=>item.id==='teachers')).toBe(false);
 });
 it('renders no onboarding controls for students or agents',()=>{
  for(const [scope,user] of [[school('student'),admin],[school(),{...admin,principal_kind:'agent'}]] as const){const html=renderToStaticMarkup(<Teachers school={scope} user={user} openCourse={()=>{}}/>);expect(html).toContain('human school administrator');expect(html).not.toContain('Create teacher');expect(html).not.toContain('type="file"');}
 });
 it('does not confuse metadata readiness with current approval or Core activation',()=>{
  const profile={...teacher(),version:4};expect(approvedTeacher(profile)).toBe(false);
  expect(renderToStaticMarkup(<TeacherApproval profile={profile}/>)).toContain('Needs profile approval');
  const html=renderToStaticMarkup(<TeacherActivation profile={teacher()}/>);expect(html).toContain('External agent identity not linked');expect(html).toContain('without an external agent identity');expect(html).toContain('admission is currently disabled');expect(html).not.toContain('PRIVATE_PRINCIPAL');expect(html).not.toContain('PRIVATE_PLACEMENT');
 });
 it('offers normal profile reapproval after the corpus changes even when metadata versions still match',()=>{
  const profile=teacher(),currentCorpus={...corpus(),approval:{status:'approved' as const},ready_for_teaching:true};
  profile.approval_current=false;profile.course_ids=['class-a'];profile.assigned_courses=[{id:'class-a',title:'Environmental science',curriculum,corpus:currentCorpus,has_teaching_authority:false}];
  expect(profile.approved_version).toBe(profile.version);expect(approvedTeacher(profile)).toBe(false);
  const html=renderToStaticMarkup(<TeacherDetail profile={profile} school={school()} user={admin} edit={()=>{}} openCourse={()=>{}} changed={async()=>{}}/>);
  expect(html).toContain('Needs profile approval');expect(html).toContain('Approve current profile');expect(html).toContain('I reviewed this teacher profile');expect(html).toContain('teaching corpus require profile reapproval');expect(html).not.toContain('Approve teaching corpus');
  expect(approvedTeacher({...profile,approval_current:true})).toBe(true);expect(approvedTeacher({...profile,approval_current:undefined})).toBe(true);expect(approvedTeacher({...profile,approval_current:true,version:4})).toBe(false);
 });
});

describe('shared teacher profile editor',()=>{
 it('provides ordinary human profile and picture fields without Core identifier inputs',()=>{
  const html=renderToStaticMarkup(<TeacherEditor schoolId="school-a" close={()=>{}} saved={async()=>{}}/>);
  for(const text of ['Human teacher','AI teacher','Teacher picture','image/png,image/jpeg,image/webp','Professional title','Pronouns','Biography','Expertise','School contact email'])expect(html).toContain(text);
  expect(html).not.toContain('Core agent ID');expect(html).not.toContain('access_token');expect(html).not.toContain('system prompt');
 });
 it('shows structured AI preferences and enforced policy boundaries rather than an unrestricted prompt',()=>{
  const html=renderToStaticMarkup(<TeacherEditor initial={teacher()} schoolId="school-a" close={()=>{}} saved={async()=>{}}/>);
  for(const text of ['Tone','Teaching style','Question frequency','Encouragement','Additional teaching preferences','never authorize completing student work','external agent activation'])expect(html.toLowerCase()).toContain(text.toLowerCase());
  expect(html).not.toContain('PRIVATE_PRINCIPAL');expect(html).not.toContain('PRIVATE_PLACEMENT');
 });
 it('whitelists metadata and never submits copied authority, role, credentials or picture metadata',()=>{
  const value={...teacher(),name:'  River  ',picture:{media_type:'image/png' as const,data_base64:'ENCODED_PNG',sha256:'not-an-input'},role:'admin',api_key:'PRIVATE_KEY',status:'ready',expected_version:999};
  const payload=teacherMetadata(value);expect(payload.name).toBe('River');expect(payload.picture).toEqual({media_type:'image/png',data_base64:'ENCODED_PNG'});
  for(const forbidden of ['PRIVATE_KEY','PRIVATE_PRINCIPAL','PRIVATE_PLACEMENT','expected_version','approved_revision_id','authority_granted','role'])expect(JSON.stringify(payload)).not.toContain(forbidden);
  expect(teacherMetadata({...value,kind:'human'}).ai_behavior).toBeNull();
 });
 it('saves an optimistic version without changing kind or binding and retains server conflict errors',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Teacher profile changed; reload before saving'}),{status:409}));vi.stubGlobal('fetch',fetcher);
  await expect(saveTeacher('school-a',{...teacher(),name:'Edited name'},teacher())).rejects.toMatchObject({status:409});
  const [path,options]=fetcher.mock.calls[0];expect(path).toBe('/api/schools/school-a/teachers/profile-a');const body=JSON.parse(options.body);expect(body.expected_version).toBe(3);expect(body.name).toBe('Edited name');expect(body).not.toHaveProperty('kind');expect(body).not.toHaveProperty('principal_id');
 });
});

describe('local photo preparation',()=>{
 it('rejects SVG, GIF and oversize inputs before decoding or network access',async()=>{
  const decode=vi.fn(),fetcher=vi.fn();vi.stubGlobal('createImageBitmap',decode);vi.stubGlobal('fetch',fetcher);
  for(const type of ['image/svg+xml','image/gif'])await expect(prepareTeacherPicture(new File(['unsafe'],'photo',{type}))).rejects.toThrow('PNG, JPEG or WebP');
  await expect(prepareTeacherPicture(new File(['<svg><image href="https://external.example/photo"/></svg>'],'renamed.png',{type:'image/png'}))).rejects.toThrow('valid PNG, JPEG or WebP');
  await expect(prepareTeacherPicture(new File([new Uint8Array(5*1024*1024+1)],'large.png',{type:'image/png'}))).rejects.toThrow('smaller than 5 MB');
  expect(decode).not.toHaveBeenCalled();expect(fetcher).not.toHaveBeenCalled();
 });
 it('resizes a common photo and re-encodes a bounded PNG rather than sending original bytes',async()=>{
  const close=vi.fn(),drawImage=vi.fn(),sizes:number[]=[];vi.stubGlobal('createImageBitmap',vi.fn().mockResolvedValue({width:1200,height:800,close}));
  const canvas={width:0,height:0,getContext:()=>({drawImage}),toBlob:(callback:(blob:Blob)=>void,type:string)=>{expect(type).toBe('image/png');sizes.push(canvas.width);callback(new Blob([sizes.length===1?new Uint8Array(65537):new TextEncoder().encode('REENCODED_PNG')],{type:'image/png'}));}};
  vi.stubGlobal('document',{createElement:()=>canvas});vi.stubGlobal('fetch',vi.fn());
  const result=await prepareTeacherPicture(new File([new Uint8Array([255,216,255]),'ORIGINAL_JPEG_METADATA'],'photo.jpg',{type:'image/jpeg'}));expect(result.media_type).toBe('image/png');expect(atob(result.data_base64)).toBe('REENCODED_PNG');expect(sizes).toEqual([256,192]);expect(close).toHaveBeenCalledOnce();expect(fetch).not.toHaveBeenCalled();
 });
});

describe('academic context and reviewed corpus',()=>{
 it('uses server academic years and preserves an omitted legacy context',()=>{
  const value=catalogDefault({states:[{code:'US-NY',name:'New York',subjects:[{code:'environmental_science',name:'Environmental science',grades:[9,10,11,12],academic_years:['2032-2033'],standards_version:'reviewed'}]}]});expect(value?.academic_year).toBe('2032-2033');expect(value?.grades).toEqual([]);expect(academicContextPayload(false,curriculum)).toEqual({});expect(academicContextPayload(true,curriculum)).toEqual({curriculum});
 });
 it('shows partial grade-band evidence and bundled/local materials without claiming complete grade coverage',()=>{
  const value=corpus();value.sources[0].reviewed_at='2026-10-18';value.documents.push({id:'local',kind:'instructional_supplement',title:'Class field notes',content:'<script>unsafe()</script>',content_sha256:'e'.repeat(64),source_id:'local'});
  const html=renderToStaticMarkup(<CorpusReview corpus={value}/>);for(const text of ['Partial standards coverage','grades 9–12','Carbon cycle','Bundled subject source','Class field notes','School material','Awaiting corpus review'])expect(html).toContain(text);expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');expect(html).not.toContain('complete Grade 10');expect(html).toContain('Reviewed 2026-10-18');
 });
 it('never renders the private corpus panel for students',()=>{expect(renderToStaticMarkup(<CourseCorpusPanel courseId="class-a" school={school('student')} user={{...admin,role:'student'}}/>)).toBe('');});
 it('shows reuse across grades as separate class context and planning assignments',()=>{
  const profile=teacher(),make=(id:string,grades:number[])=>({id,title:id,curriculum:{...curriculum,grades},corpus:null,has_teaching_authority:false});
  const html=renderToStaticMarkup(<><TeacherCourseRow course={make('Environmental science',[10])} open={()=>{}}/><TeacherCourseRow course={make('Ecosystems seminar',[11,12])} open={()=>{}}/></>);expect(profile.id).toBe('profile-a');expect(html).toContain('Grade 10');expect(html).toContain('Grades 11, 12');expect(html).toContain('Profile assignment');expect(html).toContain('Corpus not prepared');
 });
});

describe('safe student teacher selection',()=>{
 const card=(id:string,available=true):TeacherCard=>({id,kind:'ai',name:id,title:'Science teacher',pronouns:'they/them',biography:'Explore evidence.',picture:null,context_ready:available,available_for_tutoring:available});
 it('shows only available AI guidance choices and no private teacher metadata',()=>{
  const available={...card('River'),contact_email:'PRIVATE_CONTACT',ai_behavior:{boundaries:['PRIVATE_BEHAVIOR']}};
  const selector=renderToStaticMarkup(<StudentTeacherChoices teachers={[available,card('Unapproved',false),{...card('Human'),kind:'human'}]} value="" change={()=>{}}/>);expect(selector).toContain('River');expect(selector).not.toContain('Unapproved');expect(selector).not.toContain('Human');
  const team=renderToStaticMarkup(<CourseTeachingTeam teachers={[available]}/>);expect(team).not.toContain('PRIVATE_CONTACT');expect(team).not.toContain('PRIVATE_BEHAVIOR');expect(team).toContain('AI teacher');
 });
 it('changes the idempotency key when the selected teacher changes and preserves it for a retry',()=>{
  const create=vi.fn().mockReturnValueOnce('river-request').mockReturnValueOnce('other-request');const first=tutorIdentity(null,'Question','My own attempt',create,'river');expect(tutorIdentity(first,'Question','My own attempt',create,'river')).toBe(first);expect(tutorIdentity(first,'Question','My own attempt',create,'other').key).toBe('other-request');expect(create).toHaveBeenCalledTimes(2);
 });
});
