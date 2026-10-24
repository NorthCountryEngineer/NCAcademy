import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { setCsrf, type Connection } from './api';
import { TeacherModelPicker, TeacherModelSummary, TeacherModels } from './TeacherModels';
import { TeacherTutorPolicyFields, TeacherTutorPolicySummary } from './TeacherTutorPolicy';
import { approveTeacherModels, blankTeacherModelPolicy, previewTeacherModels, saveTeacherModels, teacherModelPolicyPayload, type TeacherModelDescriptor } from './teacher-model-api';
import { blankBehavior, blankTutorModel, type TeacherProfile } from './teacher-api';

const teacher=():TeacherProfile=>({id:'river',school_id:'school-a',kind:'ai',name:'River',title:'Science teacher',pronouns:'',biography:'PERSONALITY_BIOGRAPHY',contact_email:null,expertise:['Science'],picture:null,ai_behavior:{...blankBehavior(),instructions:'PERSONALITY_INSTRUCTIONS'},tutor_model:{...blankTutorModel(),connection_id:'integration-a',model:'existing-selector',allow_lesson_curation:true,allow_assignment_images:true,max_turns:4},principal_id:null,agent_placement_id:null,status:'ready',version:3,course_ids:['earth-science'],assigned_courses:[],can_manage:true,authority_granted:false,approved_revision_id:'profile-review',approved_version:3,approval_current:true,activation:{identity_linked:false,identity_eligible:false,agent_admission_enabled:false,live_models_enabled:false,authority_granted:false,runtime_active:false}});
const descriptor=():TeacherModelDescriptor=>({version:2,draft:{revision_id:'model-draft',sha256:'d'.repeat(64),policy:blankTeacherModelPolicy(teacher())},active:null,draft_pending:true,can_manage:true,automatic_routing_enabled:false,homework_images:'school_models_only',pricing_status:'unknown'});
afterEach(()=>{vi.unstubAllGlobals();setCsrf('');});

describe('independent teacher model policy requests',()=>{
 it('starts from existing lesson limits without inheriting image capability attestation',()=>{
  const profile=teacher(),before=JSON.stringify(profile),value=blankTeacherModelPolicy(profile);
  expect(value.primary).toEqual({connection_id:'integration-a',model:'existing-selector'});expect(value.lesson_policy.max_turns).toBe(4);expect(value.lesson_policy.allow_lesson_curation).toBe(true);expect(value.lesson_policy.allow_assignment_images).toBe(false);expect(value.assignment_images_reviewed).toBe(false);expect(value.routing.mode).toBe('fixed');expect(JSON.stringify(profile)).toBe(before);
 });
 it('saves only whitelisted model policy fields against its own optimistic version',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(descriptor())));vi.stubGlobal('fetch',fetcher);setCsrf('synthetic');
  const profile=teacher(),before=JSON.stringify(profile),policy={...blankTeacherModelPolicy(profile),biography:'PRIVATE_BIOGRAPHY',ai_behavior:{instructions:'UNAPPROVED_SYSTEM'},course_ids:['PRIVATE_COURSE'],api_key:'PRIVATE_KEY',endpoint:'https://PRIVATE_ENDPOINT.example',primary:{connection_id:'integration-b',model:'new-model',api_key:'PRIVATE_KEY'},routing:{mode:'preview' as const,max_question_chars:3000,candidates:[{id:'reasoner',label:' Reasoning model ',connection_id:'integration-b',model:'new-model',tasks:['reasoning' as const],api_key:'PRIVATE_KEY'}],rules:[{task:'reasoning' as const,candidate_id:'reasoner',prompt:'PRIVATE_PROMPT'}]}};
  await saveTeacherModels('school a','river/name',policy,2);
  const [path,options]=fetcher.mock.calls[0];expect(path).toBe('/api/schools/school%20a/teachers/river%2Fname/models');expect(options.method).toBe('PUT');const body=JSON.parse(options.body);expect(body.expected_version).toBe(2);expect(body.policy.primary).toEqual({connection_id:'integration-b',model:'new-model'});expect(body.policy.routing.candidates[0].label).toBe('Reasoning model');expect((options.headers as Headers).get('X-CSRF-Token')).toBe('synthetic');
  for(const forbidden of ['PRIVATE_BIOGRAPHY','UNAPPROVED_SYSTEM','PRIVATE_COURSE','PRIVATE_KEY','PRIVATE_ENDPOINT','PRIVATE_PROMPT','approved_revision_id','PERSONALITY_INSTRUCTIONS'])expect(options.body).not.toContain(forbidden);expect(JSON.stringify(profile)).toBe(before);
 });
 it('approves the exact saved model revision and previews without a provider request payload',async()=>{
  const fetcher=vi.fn().mockImplementation(async()=>new Response(JSON.stringify(descriptor())));vi.stubGlobal('fetch',fetcher);
  await approveTeacherModels('school-a','river',descriptor());expect(fetcher.mock.calls[0][0]).toBe('/api/schools/school-a/teachers/river/models/approve');expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({expected_version:2,revision_id:'model-draft'});
  await previewTeacherModels('school-a','river','  Explain the water cycle  ');expect(fetcher.mock.calls[1][0]).toBe('/api/schools/school-a/teachers/river/models/preview');expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({text:'Explain the water cycle',has_images:false});expect(fetcher).toHaveBeenCalledTimes(2);
 });
 it('does not retain image permission when private curation is off or arbitrary candidate tasks',()=>{
  const value=blankTeacherModelPolicy(teacher());value.lesson_policy.allow_lesson_curation=false;value.lesson_policy.allow_assignment_images=true;value.routing.candidates=[{id:'candidate',label:'Candidate',connection_id:'integration-a',model:'model',tasks:['simple','simple','UNAPPROVED_TASK' as 'simple']}];
  const body=teacherModelPolicyPayload(value);expect(body.lesson_policy.allow_assignment_images).toBe(false);expect(body.routing.candidates[0].tasks).toEqual(['simple']);
 });
});

describe('teacher model administration presentation',()=>{
 it('does not imply that missing legacy settings disable an independently assigned model',()=>{const html=renderToStaticMarkup(<TeacherTutorPolicySummary policy={null}/>);expect(html).toContain('No legacy lesson model configured.');expect(html).not.toContain('cannot answer lesson questions');const form=renderToStaticMarkup(<TeacherTutorPolicyFields schoolId="school-a" value={null} change={()=>{}}/>);expect(form).toContain('Without a legacy policy, Lesson Q&amp;A requires an approved teacher-specific model policy.');expect(form).not.toContain('this profile cannot answer lesson questions');});
 it('keeps the teacher identity independent and describes preview as non-executing',()=>{
  const policy=blankTeacherModelPolicy(teacher());policy.routing.mode='preview';const html=renderToStaticMarkup(<TeacherModelSummary policy={policy} connections={[{id:'integration-a',name:'School model integration'}]}/>);
  for(const text of ['existing-selector','School model integration','Lesson Q&amp;A','Homework Help teacher replies','Routing preview · primary model executes','1500 output tokens'])expect(html).toContain(text);expect(html).not.toContain('PERSONALITY_INSTRUCTIONS');expect(html).not.toContain('$0');
 });
 it('retains an unavailable selection without offering disabled integrations or secrets',()=>{
  const connections:Connection[]=[{id:'enabled',name:'School integration',endpoint:'https://PRIVATE_ENDPOINT.example',models:['available'],enabled:true},{id:'disabled',name:'DISABLED_CONNECTION',endpoint:'https://private.example',models:['DISABLED_MODEL'],enabled:false}];
  const html=renderToStaticMarkup(<TeacherModelPicker label="Primary integrated model" value={{connection_id:'old',model:'old-model'}} connections={connections} change={()=>{}}/>);
  expect(html).toContain('Current model unavailable · old-model');expect(html).toContain('School integration · available');for(const forbidden of ['DISABLED_CONNECTION','DISABLED_MODEL','PRIVATE_ENDPOINT'])expect(html).not.toContain(forbidden);
 });
 it('distinguishes explicitly restored legacy settings from disabled teacher activities',()=>{
  const policy=blankTeacherModelPolicy(teacher());const restored=renderToStaticMarkup(<TeacherModelSummary policy={{...policy,enabled:false}}/>);expect(restored).toContain('Use existing teaching policies');expect(restored).not.toContain('Primary model');
  const disabled=renderToStaticMarkup(<TeacherModelSummary policy={{...policy,lesson_enabled:false,homework_enabled:false}}/>);expect(disabled).toContain('Lesson activities disabled');expect(disabled).toContain('Homework Help replies disabled');expect(disabled).not.toContain('Use existing teaching policies');
 });
 it('does not display model administration for human teachers or non-managers',()=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);for(const [profile,manage] of [[{...teacher(),kind:'human' as const},true],[teacher(),false]] as const)expect(renderToStaticMarkup(<TeacherModels schoolId="school-a" profile={profile} manage={manage}/>)).toBe('');expect(fetcher).not.toHaveBeenCalled();
 });
});
