import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { HomeworkAI, HomeworkAIMessageView, homeworkQuestionKeyDown, homeworkReportControlId, resizeHomeworkQuestion } from './HomeworkAI';
import type { HomeworkAIIndex, HomeworkAIMessage } from './homework-ai-api';
const index:HomeworkAIIndex={assistant:{school_id:'school',version:1,revision_id:'revision',approved_revision_id:'revision',approved:true,available:true,live_models_enabled:true,configuration_sha256:'a'.repeat(64),approval_required:false,privacy_mode:'deidentified',local_only_images:true,configuration:{enabled:true,name:'Study helper',biography:'',allow_images:true,max_input_chars:5000,max_output_tokens:500,privacy_mode:'deidentified'}},courses:[],sessions:[]};
afterEach(()=>vi.unstubAllGlobals());
describe('compact Homework Help interactions and access',()=>{
 it('starts with a one-row editable question, explicit non-submit expansion and a focusable message history',()=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);const html=renderToStaticMarkup(<HomeworkAI schoolId="school" userId="student" index={index} openCourse={()=>{}}/>);
  expect(html).toMatch(/<textarea[^>]*rows="1"/);expect(html).toContain('Expand question');expect(html).toMatch(/type="button"[^>]*class="text-button homework-question-expand"[^>]*aria-expanded="false"/);
  expect(html).toMatch(/aria-label="Conversation messages"[^>]*tabindex="0"/);expect(html).not.toMatch(/aria-label="Question and attachments"[^>]*tabindex/);expect(fetcher).not.toHaveBeenCalled();
 });
 it('keeps compact safety/review disclosure and a report shortcut while making the longer guidance explicitly expandable',()=>{
  const html=renderToStaticMarkup(<HomeworkAI schoolId="school" userId="student" index={index} openCourse={()=>{}}/>);expect(html).toContain('AI can make mistakes');expect(html).toContain('Staff can review.');expect(html).toContain('Report a concern about the latest message');expect(html).toMatch(/<details class="homework-guidance-details"><summary>Privacy &amp; safety<\/summary>/);expect(html).toContain('tell a trusted adult');expect(html).toContain('Your original photos and chat stay in Academy');
 });
 it('retains labelled conversation controls and a closed mobile drawer without adding a history request',()=>{
  const html=renderToStaticMarkup(<HomeworkAI schoolId="school" userId="student" index={index} openCourse={()=>{}}/>);expect(html).toContain('aria-label="Show conversation history"');expect(html).toContain('aria-controls="homework-history-main"');expect(html).toContain('id="homework-history-main"');expect(html).not.toContain('homework-history-open');
 });
 it('grows to a bounded height, explicitly expands and collapses without changing the question',()=>{
  const input={style:{height:''},scrollHeight:35};resizeHomeworkQuestion(input as never,false);expect(input.style.height).toBe('38px');input.scrollHeight=500;resizeHomeworkQuestion(input as never,false);expect(input.style.height).toBe('120px');resizeHomeworkQuestion(input as never,true);expect(input.style.height).toBe('220px');input.scrollHeight=35;resizeHomeworkQuestion(input as never,true);expect(input.style.height).toBe('120px');resizeHomeworkQuestion(input as never,false);expect(input.style.height).toBe('38px');
 });
 it('leaves Enter and Shift+Enter as editing, submits only an explicit Ctrl/Cmd+Enter and ignores composition/repeats',()=>{
  const requestSubmit=vi.fn(),preventDefault=vi.fn();const base={key:'Enter',ctrlKey:false,metaKey:false,shiftKey:false,repeat:false,nativeEvent:{isComposing:false},currentTarget:{form:{requestSubmit}},preventDefault};
  for(const event of [base,{...base,shiftKey:true},{...base,ctrlKey:true,repeat:true},{...base,metaKey:true,nativeEvent:{isComposing:true}}])homeworkQuestionKeyDown(event as never);
  expect(requestSubmit).not.toHaveBeenCalled();expect(preventDefault).not.toHaveBeenCalled();homeworkQuestionKeyDown({...base,ctrlKey:true} as never);homeworkQuestionKeyDown({...base,metaKey:true} as never);expect(requestSubmit).toHaveBeenCalledTimes(2);expect(preventDefault).toHaveBeenCalledTimes(2);
 });
 it.each(['released','clarify','blocked','withheld','pending'] as const)('keeps compact concern controls for %s messages with a scoped shortcut target',status=>{
  const item:HomeworkAIMessage={id:'message-one',message:'Synthetic learning question',created_at:'2026-11-11',idempotency_key:'key',status,error:null,answer:['released','clarify'].includes(status)?'Try a different example.':null,triage:{method:'general',course_id:null,course_name:null,prompt:null,candidates:[]},citations:[],references:[],images:[],usage:{},resume_available:false};
  const html=renderToStaticMarkup(<HomeworkAIMessageView item={item} sessionId="session-one" safetyScope="school:student" choose={()=>{}} openCourse={()=>{}}/>);expect(html).toContain('homework-message-actions');expect(html).toContain(`id="${homeworkReportControlId('session-one','message-one')}"`);expect(html).toContain('Report a concern');
 });
});
