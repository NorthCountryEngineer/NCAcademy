import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { HomeworkAIMessageView } from './HomeworkAI';
import { HomeworkSafetyDisclosure, HomeworkSafetyReportReceipt, HomeworkSafetyStaffReview } from './HomeworkSafety';
import { getHomeworkSafetyReport, getHomeworkSafetyReports, homeworkSafetyInbox, homeworkSafetyReportPayload, homeworkSafetyReviewPayload, retainHomeworkSafetyRequest, retainedHomeworkSafetyRequest, reviewHomeworkSafetyReport, sendHomeworkSafetyReport, type HomeworkSafetyReport, type HomeworkSafetyReportDetail } from './homework-safety-api';
import type { HomeworkAIMessage } from './homework-ai-api';

const message=(status:HomeworkAIMessage['status']='released'):HomeworkAIMessage=>({id:'message-one',message:'A synthetic question',created_at:'2026-11-11T10:00:00Z',idempotency_key:'message-key-one',status,error:status==='withheld'?'Not released':null,answer:['released','clarify'].includes(status)?'Try another example.':null,triage:{method:'general',course_id:null,course_name:null,prompt:null,candidates:[]},citations:[],references:[],images:[],usage:{},resume_available:false});
const report=():HomeworkSafetyReport=>({id:'report-one',session_id:'session-one',message_id:'message-one',category:'unsafe',detail:'Synthetic concern',created_at:'2026-11-11T10:00:00Z',status:'pending'});
const detail=():HomeworkSafetyReportDetail=>({report:{...report(),school_id:'school-one',student_id:'student-one',student_name:'Synthetic learner',source:'student',reason_codes:['unsafe'],report_sha256:'a'.repeat(64),review:null},message:message()});
afterEach(()=>vi.unstubAllGlobals());

describe('Homework Help child safety disclosure and reporting',()=>{
 it('plainly identifies AI, mistakes, privacy, school review and immediate adult support',()=>{
  const html=renderToStaticMarkup(<HomeworkSafetyDisclosure/>);
  for(const required of ['AI study helper','AI can make mistakes','Keep names, addresses, phone numbers','School staff can review saved conversations and photos','Report a concern','tell a trusted adult','not an emergency service'])expect(html).toContain(required);
  const privateHtml=renderToStaticMarkup(<HomeworkSafetyDisclosure privateMode/>);expect(privateHtml).toContain('Your study helper can make mistakes');expect(privateHtml).not.toContain('This is an AI');
 });
 it.each(['released','clarify','blocked','withheld','pending'] as const)('keeps reporting available for %s outcomes without asking AI again',status=>{
  const html=renderToStaticMarkup(<HomeworkAIMessageView item={message(status)} choose={()=>{}} openCourse={()=>{}} paused sessionId="session-one" safetyScope="school:student"/>);
  expect(html).toContain('Report a concern');expect(html).toContain('aria-label="Safety concern for this message"');expect(html).not.toMatch(/disabled=""[^>]*>[^<]*Report a concern/);
 });
 it('does not add student report controls to staff-only message renderers',()=>{
  const html=renderToStaticMarkup(<HomeworkAIMessageView item={message()} choose={()=>{}} openCourse={()=>{}}/>);expect(html).not.toContain('Report a concern');
 });
 it('escapes report, student and message text and never reveals a withheld answer',()=>{
  const value=detail();value.report.student_name='<img src=x onerror=alert(1)>';value.report.detail='<script>bad()</script>';value.report.reason_codes=['<svg onload=bad()>'];value.message={...message('withheld'),message:'<iframe src=bad>',answer:'WITHHELD_SECRET'};
  const html=renderToStaticMarkup(<HomeworkSafetyStaffReview schoolId="school-one" value={value} changed={()=>{}}/>);
  expect(html).toContain('&lt;script&gt;');expect(html).toContain('&lt;img');expect(html).toContain('&lt;iframe');for(const forbidden of ['<script>','<img src=x','<iframe src=bad>','WITHHELD_SECRET'])expect(html).not.toContain(forbidden);
 });
 it('keeps student receipts bounded and shows human review state',()=>{
  const pending=renderToStaticMarkup(<HomeworkSafetyReportReceipt report={report()}/>);expect(pending).toContain('still need to review');
  const reviewed=renderToStaticMarkup(<HomeworkSafetyReportReceipt report={{...report(),status:'reviewed'}}/>);expect(reviewed).toContain('have reviewed');expect(reviewed).toContain('tell a trusted adult now');const followup=renderToStaticMarkup(<HomeworkSafetyReportReceipt report={{...report(),status:'follow_up_required'}}/>);expect(followup).toContain('still need to follow up');
 });
});

describe('safety API scope, payload and request identity',()=>{
 it('maps exact bounded report fields and rejects invalid category, oversized note and invalid ID',()=>{
  expect(homeworkSafetyReportPayload('unsafe','  synthetic note  ','report-key-one')).toEqual({category:'unsafe',detail:'synthetic note',idempotency_key:'report-key-one'});
  expect(()=>homeworkSafetyReportPayload('custom' as never,'','report-key-one')).toThrow('concern type');
  expect(()=>homeworkSafetyReportPayload('other','x'.repeat(1001),'report-key-one')).toThrow('1,000');
  expect(()=>homeworkSafetyReportPayload('other','','short')).toThrow('request ID');
 });
 it('encodes every route identifier and fixes school scope for all staff reads/reviews',async()=>{
  const fetcher=vi.fn().mockImplementation(async()=>new Response('{}'));vi.stubGlobal('fetch',fetcher);
  await getHomeworkSafetyReports('session/a','message?b');await sendHomeworkSafetyReport('session/a','message?b','private_data','synthetic','report-key-one');await homeworkSafetyInbox('school/a','cursor?x');await getHomeworkSafetyReport('school/a','report/b');await reviewHomeworkSafetyReport('school/a','report/b','a'.repeat(64),'follow_up_required','contacted_trusted_adult',' tested follow up ','review-key-one');
  expect(fetcher.mock.calls.map(call=>call[0])).toEqual(['/api/homework-help/ai/sessions/session%2Fa/messages/message%3Fb/safety-report','/api/homework-help/ai/sessions/session%2Fa/messages/message%3Fb/safety-report','/api/homework-help/ai/safety-reports?school_id=school%2Fa&cursor=cursor%3Fx','/api/homework-help/ai/safety-reports/report%2Fb?school_id=school%2Fa','/api/homework-help/ai/safety-reports/report%2Fb/review?school_id=school%2Fa']);
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({idempotency_key:'report-key-one',category:'private_data',detail:'synthetic'});
  expect(JSON.parse(fetcher.mock.calls[4][1].body)).toEqual({expected_sha256:'a'.repeat(64),disposition:'follow_up_required',reason_code:'contacted_trusted_adult',note:'tested follow up',idempotency_key:'review-key-one'});
  expect(fetcher.mock.calls.every(call=>call[1].credentials==='same-origin'&&call[1].cache==='no-store')).toBe(true);
 });
 it('binds review to an exact saved hash and bounded human dispositions',()=>{
  expect(()=>homeworkSafetyReviewPayload('a'.repeat(64),'reviewed','addressed','short','review-key-one')).toThrow('at least 10');
  expect(()=>homeworkSafetyReviewPayload('bad','reviewed','addressed','','review-key-one')).toThrow('Reload');
  expect(()=>homeworkSafetyReviewPayload('a'.repeat(64),'publish' as never,'addressed','','review-key-one')).toThrow('outcome');
  expect(()=>homeworkSafetyReviewPayload('a'.repeat(64),'reviewed','custom' as never,'','review-key-one')).toThrow('outcome');
 });
 it('retains only opaque request metadata, with isolated scopes and no note/transcript persistence',()=>{
  const stored=new Map<string,string>();vi.stubGlobal('window',{sessionStorage:{getItem:(key:string)=>stored.get(key)||null,setItem:(key:string,value:string)=>stored.set(key,value),removeItem:(key:string)=>stored.delete(key)}});
  retainHomeworkSafetyRequest('school:student:message',{key:'report-key-one',note:'PRIVATE_NOTE',message:'PRIVATE_TEXT'} as never);
  expect([...stored.values()]).toEqual(['{"key":"report-key-one"}']);expect(retainedHomeworkSafetyRequest('school:student:message')).toEqual({key:'report-key-one'});expect(retainedHomeworkSafetyRequest('other')).toBeNull();
  retainHomeworkSafetyRequest('school:student:message',null);expect(retainedHomeworkSafetyRequest('school:student:message')).toBeNull();
  stored.set('ncacademy:homework-safety:bad','{"key":"x"}');expect(retainedHomeworkSafetyRequest('bad')).toBeNull();
 });
});
