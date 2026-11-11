import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { HomeworkOpenAIStudentControls } from './HomeworkAISettings';
import { blankHomeworkAIPolicy, type HomeworkAIConfiguration } from './homework-ai-api';
const state=():HomeworkAIConfiguration=>({school_id:'school',version:1,revision_id:'revision',approved_revision_id:'revision',approved:true,available:false,live_models_enabled:true,configuration:{...blankHomeworkAIPolicy(),privacy_mode:'raw_zdr',allow_local_students:true},configuration_sha256:'a'.repeat(64),approval_required:true});
describe('operator-verified OpenAI student data controls',()=>{
 it('shows operator verification required even when school settings are approved and enabled',()=>{
  const html=renderToStaticMarkup(<HomeworkOpenAIStudentControls state={{...state(),openai_student_controls:{required:true,verified:false,reason:'Current provider approval has not been verified.',expires_at:null}}}/>);
  for(const required of ['Operator verification required','school note or school approval cannot enable provider Zero Data Retention','confirm OpenAI approval','read back the current provider data controls','including image input coverage','Student model requests remain unavailable','Current provider approval has not been verified.'])expect(html).toContain(required);
  expect(html).toContain('https://developers.openai.com/api/docs/guides/your-data');expect(html).not.toContain('<input');expect(html).not.toContain('<button');
 });
 it('shows verified controls and expiry using only the safe operator summary',()=>{
  const html=renderToStaticMarkup(<HomeworkOpenAIStudentControls state={{...state(),openai_student_controls:{required:true,verified:true,reason:null,expires_at:'2026-12-01'}}}/>);expect(html).toContain('OpenAI student data controls verified');expect(html).toContain('Verification expires 2026-12-01');expect(html).not.toContain('Operator verification required');
 });
 it('fails closed when metadata is missing and distinguishes student access not requested',()=>{
  expect(renderToStaticMarkup(<HomeworkOpenAIStudentControls state={state()}/>)).toContain('Operator verification required');
  const html=renderToStaticMarkup(<HomeworkOpenAIStudentControls state={{...state(),configuration:{...blankHomeworkAIPolicy(),privacy_mode:'raw_zdr'},openai_student_controls:{required:false,verified:false,reason:null,expires_at:null}}}/>);expect(html).toContain('OpenAI student access not requested');expect(html).not.toContain('OpenAI student data controls verified');
 });
});
