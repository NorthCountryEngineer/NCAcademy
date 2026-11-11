import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { HomeworkAI, HomeworkAIMessageView, HomeworkTaskPrivacyNotice, homeworkResponder } from './HomeworkAI';
import { HomeworkAIConfigurationReview, HomeworkOpenAIStudentControls } from './HomeworkAISettings';
import { blankHomeworkAIPolicy, homeworkAIConfigurationPayload, homeworkAIRequestProblem, homeworkLocalTaskProcessing, homeworkNeedsTaskClarification, homeworkAIPayload, type HomeworkAIConfiguration, type HomeworkAIInput, type HomeworkAIMessage, type HomeworkAIStudentConfiguration } from './homework-ai-api';
const policy=()=>({...blankHomeworkAIPolicy(),enabled:true,allow_local_students:true,allow_images:true});
const configuration=():HomeworkAIConfiguration=>({school_id:'school',version:1,revision_id:'revision',approved_revision_id:'revision',approved:true,available:true,live_models_enabled:true,configuration:policy(),configuration_sha256:'a'.repeat(64),approval_required:true});
const assistant=():HomeworkAIStudentConfiguration=>({...configuration(),configuration:policy(),privacy_mode:'deidentified',local_only_images:true,image_policy_sha256:'b'.repeat(64),photo_model_labels:null});
const input=():HomeworkAIInput=>({message:'Question 7. Look at the sheet.',course_id:null,images:[],image_use_confirmed:false});
const photo={name:'worksheet.jpg',data_url:'data:image/jpeg;base64,AAE=',sha256:'c'.repeat(64),width:300,height:300};
const clarification=():HomeworkAIMessage=>({id:'local-clarification',message:'Help with this worksheet.',created_at:'2026-11-11',idempotency_key:'question-key',status:'clarify',error:null,answer:'Please type the equation or choose a learning topic so I can help.',privacy_mode:'deidentified',deidentification:{status:'clarify',reason:'unsupported_task',task_summary:null,image_read:false,action:'INTERNAL_DSL_NOT_CHILD_UI' as never,stage:0,local_only_images:true},triage:{method:'local_task',course_id:null,course_name:null,prompt:null,candidates:[]},citations:[],references:[],images:[],usage:{},resume_available:false});
afterEach(()=>vi.unstubAllGlobals());

describe('private homework processing contracts',()=>{
 it('defaults newly saved policy to learning details only and preserves explicit historical original-data mode',()=>{
  expect(blankHomeworkAIPolicy().privacy_mode).toBe('deidentified');expect(homeworkAIConfigurationPayload(policy(),0).privacy_mode).toBe('deidentified');
  const historic={...policy()};delete historic.privacy_mode;expect(homeworkAIConfigurationPayload(historic,1).privacy_mode).toBe('raw_zdr');expect(homeworkAIConfigurationPayload({...policy(),privacy_mode:'raw_zdr'},1).privacy_mode).toBe('raw_zdr');
 });
 it('keeps local photo consent and current policy binding without requiring a remote image provider',()=>{
  const local=assistant();expect(homeworkLocalTaskProcessing(local)).toBe(true);expect(homeworkAIRequestProblem({...input(),images:[photo]},local)).toContain('read your homework photos privately');expect(homeworkAIRequestProblem({...input(),images:[photo],image_use_confirmed:true},local)).toBeNull();
  const retained={...input(),previous_image_message_id:'source',previous_image_count:1,image_use_confirmed:true,expected_image_policy_sha256:'b'.repeat(64)};
  expect(homeworkAIRequestProblem(retained,local)).toBeNull();expect(homeworkAIRequestProblem({...retained,expected_image_policy_sha256:'d'.repeat(64)},local)).toContain('Refresh');
  expect(homeworkLocalTaskProcessing({...local,privacy_mode:'raw_zdr',configuration:{...local.configuration!,privacy_mode:'raw_zdr'}})).toBe(false);
 });
 it('keeps every private input inside the existing Academy request and excludes caller-supplied compiler/provider overrides',()=>{
  const payload=homeworkAIPayload({...input(),images:[photo],image_use_confirmed:true,privacy_mode:'raw_zdr',task:{unreviewed:'PRIVATE_DSL'},deidentified_text:'PRIVATE_OVERRIDE',history:'PRIVATE_HISTORY'} as HomeworkAIInput,'request-key');
  expect(payload.images).toEqual([{name:photo.name,data_url:photo.data_url}]);for(const forbidden of ['PRIVATE_DSL','PRIVATE_OVERRIDE','PRIVATE_HISTORY','raw_zdr'])expect(JSON.stringify(payload)).not.toContain(forbidden);
 });
 it('recognizes only a local audited clarification as a draft-preserving response',()=>{
  const value=clarification();expect(homeworkNeedsTaskClarification(value)).toBe(true);expect(homeworkNeedsTaskClarification({...value,status:'released'})).toBe(false);expect(homeworkNeedsTaskClarification({...value,privacy_mode:'raw_zdr'})).toBe(false);expect(homeworkNeedsTaskClarification({...value,deidentification:undefined})).toBe(false);
 });
});

describe('child and school privacy presentation',()=>{
 it('states what stays in Academy without a redaction approval, raw forwarding switch or internal task language',()=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);const html=renderToStaticMarkup(<HomeworkAI schoolId="school" userId="student" index={{assistant:assistant(),courses:[],sessions:[]}} openCourse={()=>{}}/>);
  for(const text of ['Your original photos and chat stay in Academy','Only supported learning details go to AI','type an equation or topic','We prepare supported learning details privately','Send'])expect(html).toContain(text);
  for(const forbidden of ['redaction approval','Approve redacted','Forward raw','INTERNAL_DSL','Zero Data Retention','OpenAI','bring in a relevant approved AI teacher'])expect(html).not.toContain(forbidden);expect(fetcher).not.toHaveBeenCalled();expect(renderToStaticMarkup(<HomeworkTaskPrivacyNotice/>)).not.toContain('<input');
 });
 it('identifies local clarification truthfully and keeps safety reporting available without rendering internal metadata',()=>{
  const value=clarification(),html=renderToStaticMarkup(<HomeworkAIMessageView item={value} sessionId="session" safetyScope="school:student" choose={()=>{}} openCourse={()=>{}}/>);expect(homeworkResponder(value,'AI teacher')).toEqual({name:'Homework Help',description:'Private question check'});expect(html).toContain('Private question check');expect(html).toContain('Help us read your question');expect(html).toContain('Report a concern');expect(html).not.toContain('INTERNAL_DSL_NOT_CHILD_UI');expect(html).not.toContain('AI clarification');
 });
 it('makes the school learning-details policy usable without claiming provider ZDR approval',()=>{
  const state=configuration(),html=renderToStaticMarkup(<HomeworkOpenAIStudentControls state={{...state,openai_student_controls:{required:false,verified:false,reason:null,expires_at:null}}}/>);expect(html).toContain('Original homework stays in Academy');expect(html).toContain('does not require provider Zero Data Retention');expect(html).not.toContain('Operator verification required');expect(html).not.toContain('OpenAI student data controls verified');
  const review=renderToStaticMarkup(<HomeworkAIConfigurationReview state={state}/>);expect(review).toContain('supported learning details only');expect(review).toContain('Read locally with student confirmation');expect(review).not.toContain('Provider data control evidence');
 });
});
