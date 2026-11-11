import { afterEach, describe, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { HomeworkSafetyReportButton, HomeworkSafetyStaffReview } from './HomeworkSafety';
import { Notice } from './components';
import type { HomeworkSafetyReport, HomeworkSafetyReportDetail } from './homework-safety-api';

const hooks=vi.hoisted(()=>({states:[] as unknown[],index:0,dirty:false}));
vi.mock('react',async importOriginal=>({...await importOriginal<typeof import('react')>(),useState:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push(typeof initial==='function'?initial():initial);return [hooks.states[index],(value:unknown)=>{const next=typeof value==='function'?value(hooks.states[index]):value;if(!Object.is(next,hooks.states[index])){hooks.states[index]=next;hooks.dirty=true;}}];},useRef:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push({current:initial});return hooks.states[index];}}));
type Element=ReactElement<{children?:ReactNode;disabled?:boolean;value?:unknown;error?:string;onChange?:(event:{target:{value?:string;checked?:boolean}})=>void;onClick?:()=>void;onSubmit?:(event:{preventDefault:()=>void})=>Promise<void>}>;
function nodes(node:ReactNode):Element[]{if(Array.isArray(node))return node.flatMap(nodes);if(!isValidElement<{children?:ReactNode}>(node))return [];return [node as Element,...nodes(node.props.children)];}
function text(node:ReactNode):string{if(Array.isArray(node))return node.map(text).join('');if(isValidElement<{children?:ReactNode}>(node))return text(node.props.children);return typeof node==='string'||typeof node==='number'?String(node):'';}
function view(component:()=>ReactNode){function render(){let tree:ReactNode;do{hooks.dirty=false;hooks.index=0;tree=component();}while(hooks.dirty);return tree!;}return {render,find:(kind:unknown,label='')=>nodes(render()).find(node=>node.type===kind&&text(node).includes(label)),all:(kind:unknown)=>nodes(render()).filter(node=>node.type===kind)};}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
const submit=(element:Element)=>element.props.onSubmit!({preventDefault:vi.fn()});
const report=():HomeworkSafetyReport=>({id:'report-one',session_id:'session-one',message_id:'message-one',category:'unsafe',detail:'synthetic concern',created_at:'2026-11-11T10:00:00Z',status:'pending'});
function detail():HomeworkSafetyReportDetail{return {report:{...report(),school_id:'school-one',student_id:'student-one',student_name:'Synthetic learner',source:'student',reason_codes:[],report_sha256:'a'.repeat(64),review:null},message:{id:'message-one',message:'synthetic question',created_at:'2026-11-11T10:00:00Z',status:'withheld',error:'Not released',answer:null}};}
function storage(){vi.stubGlobal('crypto',{randomUUID:()=>"00000000-0000-4000-8000-000000000001"});const values=new Map<string,string>();vi.stubGlobal('window',{sessionStorage:{getItem:(key:string)=>values.get(key)||null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)}});return values;}
const resetHooks=()=>{hooks.states=[];hooks.index=0;hooks.dirty=false;};
afterEach(()=>{resetHooks();vi.unstubAllGlobals();});

describe('student concern interactions',()=>{
 it('checks saved state only after opening and sends one stable explicit report even on repeated submit',async()=>{
  const stored=storage();let finish!:(response:Response)=>void;
  const fetcher=vi.fn().mockResolvedValueOnce(new Response('{"reports":[]}')).mockImplementationOnce(()=>new Promise<Response>(resolve=>{finish=resolve;}));vi.stubGlobal('fetch',fetcher);
  const ui=view(()=>HomeworkSafetyReportButton({sessionId:'session-one',messageId:'message-one',scope:'school:student'}));ui.render();expect(fetcher).not.toHaveBeenCalled();ui.find('button','Report a concern')!.props.onClick!();await settle();
  expect(fetcher).toHaveBeenCalledOnce();ui.find('select')!.props.onChange!({target:{value:'unsafe'}});ui.find('textarea')!.props.onChange!({target:{value:'  synthetic concern  '}});
  const pending=submit(ui.find('form')!);await submit(ui.find('form')!);expect(fetcher).toHaveBeenCalledTimes(2);expect(ui.find('button','Saving concern')?.props.disabled).toBe(true);
  const payload=JSON.parse(fetcher.mock.calls[1][1].body);expect(payload).toMatchObject({category:'unsafe',detail:'synthetic concern'});expect(payload.idempotency_key).toHaveLength(36);expect([...stored.values()]).toEqual([JSON.stringify({key:payload.idempotency_key})]);expect([...stored.values()].join('')).not.toContain('synthetic concern');
  finish(new Response(JSON.stringify(report())));await pending;expect(ui.find('form')).toBeUndefined();expect(stored.size).toBe(0);expect(fetcher.mock.calls.filter(call=>call[1].method==='POST')).toHaveLength(1);
 });
 it('locks an uncertain outcome across reloading and reconciles with GET without repeating or asking AI',async()=>{
  const stored=storage();const fetcher=vi.fn().mockResolvedValueOnce(new Response('{"reports":[]}')).mockRejectedValueOnce(new TypeError('connection lost')).mockResolvedValueOnce(new Response('{"reports":[]}')).mockResolvedValueOnce(new Response(JSON.stringify({reports:[report()]})));vi.stubGlobal('fetch',fetcher);
  let ui=view(()=>HomeworkSafetyReportButton({sessionId:'session-one',messageId:'message-one',scope:'school:student'}));ui.find('button','Report a concern')!.props.onClick!();await settle();ui.find('select')!.props.onChange!({target:{value:'other'}});await submit(ui.find('form')!);
  expect(ui.find('form')).toBeUndefined();expect(ui.find(Notice)?.props.error).toContain('will not be sent again automatically');expect(stored.size).toBe(1);expect(ui.find('button','Check saved concern')).toBeDefined();
  resetHooks();ui=view(()=>HomeworkSafetyReportButton({sessionId:'session-one',messageId:'message-one',scope:'school:student'}));ui.find('button','Report a concern')!.props.onClick!();await settle();expect(ui.find('form')).toBeUndefined();expect(ui.find(Notice)?.props.error).toContain('No additional report was sent');
  ui.find('button','Check saved concern')!.props.onClick!();await settle();expect(stored.size).toBe(0);expect(ui.find('form')).toBeUndefined();expect(fetcher.mock.calls.filter(call=>call[1].method==='POST')).toHaveLength(1);expect(fetcher.mock.calls.every(call=>String(call[0]).endsWith('/safety-report'))).toBe(true);
 });
 it('does not offer a new report when the existing own report has already been saved',async()=>{
  storage();const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({reports:[report()]})));vi.stubGlobal('fetch',fetcher);const ui=view(()=>HomeworkSafetyReportButton({sessionId:'session-one',messageId:'message-one',scope:'school:student'}));ui.find('button','Report a concern')!.props.onClick!();await settle();expect(ui.find('form')).toBeUndefined();expect(fetcher).toHaveBeenCalledOnce();
 });
});

describe('human school safety review interactions',()=>{
 it('sends exact report hash and selected disposition with one immutable request identity',async()=>{
  storage();let finish!:(response:Response)=>void;const fetcher=vi.fn().mockImplementation(()=>new Promise<Response>(resolve=>{finish=resolve;}));vi.stubGlobal('fetch',fetcher);const changed=vi.fn(),value=detail();const ui=view(()=>HomeworkSafetyStaffReview({schoolId:'school-one',value,changed}));
  const selects=ui.all('select');selects[0].props.onChange!({target:{value:'follow_up_required'}});selects[1].props.onChange!({target:{value:'contacted_trusted_adult'}});ui.find('textarea')!.props.onChange!({target:{value:'  synthetic follow-up  '}});
  const pending=submit(ui.find('form')!);await submit(ui.find('form')!);expect(fetcher).toHaveBeenCalledOnce();const [url,options]=fetcher.mock.calls[0];expect(url).toBe('/api/homework-help/ai/safety-reports/report-one/review?school_id=school-one');expect(JSON.parse(options.body)).toMatchObject({expected_sha256:'a'.repeat(64),disposition:'follow_up_required',reason_code:'contacted_trusted_adult',note:'synthetic follow-up'});
  const saved={id:'review-one',created_at:'2026-11-11T11:00:00Z',disposition:'follow_up_required',reason_code:'contacted_trusted_adult',note:'synthetic follow-up'};finish(new Response(JSON.stringify(saved)));await pending;expect(changed).toHaveBeenCalledWith({...value,report:{...value.report,status:'follow_up_required',review:saved,review_history:[saved]}});
 });
 it('keeps uncertain review locked and checks the same school/report without replaying',async()=>{
  const stored=storage(),value=detail(),saved={id:'review-one',created_at:'2026-11-11T11:00:00Z',disposition:'reviewed' as const,reason_code:'addressed' as const,note:''};const fresh={...value,report:{...value.report,status:'reviewed',review:saved}};
  const fetcher=vi.fn().mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce(new Response(JSON.stringify(fresh)));vi.stubGlobal('fetch',fetcher);const changed=vi.fn(),ui=view(()=>HomeworkSafetyStaffReview({schoolId:'school-one',value,changed}));ui.find('textarea')!.props.onChange!({target:{value:'Human checked the concern.'}});await submit(ui.find('form')!);expect(ui.find('form')).toBeUndefined();expect(stored.size).toBe(1);ui.find('button','Check saved review')!.props.onClick!();await settle();expect(fetcher).toHaveBeenCalledTimes(2);expect(fetcher.mock.calls[1][0]).toBe('/api/homework-help/ai/safety-reports/report-one?school_id=school-one');expect(fetcher.mock.calls[1][1].method).toBe('GET');expect(changed).toHaveBeenCalledWith(fresh);expect(stored.size).toBe(0);
 });
 it('keeps follow-up open until an acknowledged resolution references the initial receipt',async()=>{
  storage();const initial={id:'safety-review:initial-receipt',created_at:'2026-11-11T11:00:00Z',disposition:'follow_up_required' as const,reason_code:'other' as const,note:'Human follow-up needed'};
  let value:HomeworkSafetyReportDetail={...detail(),report:{...detail().report,status:'follow_up_required',review:initial,review_history:[initial]}};
  const saved={id:'review-final',created_at:'2026-11-11T12:00:00Z',disposition:'reviewed' as const,reason_code:'addressed' as const,note:''};
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(saved)));vi.stubGlobal('fetch',fetcher);const changed=vi.fn((next:HomeworkSafetyReportDetail)=>{value=next;});const ui=view(()=>HomeworkSafetyStaffReview({schoolId:'school-one',value,changed}));
  expect(text(ui.render())).toContain('Follow-up remains open');expect(text(ui.render())).toContain('does not contact an adult automatically');expect(ui.find('button','Record follow-up resolution')!.props.disabled).toBe(true);await submit(ui.find('form')!);expect(fetcher).not.toHaveBeenCalled();
  ui.find('textarea')!.props.onChange!({target:{value:'Human completed the follow-up.'}});ui.find('input')!.props.onChange!({target:{checked:true}});await submit(ui.find('form')!);expect(fetcher).toHaveBeenCalledOnce();expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({disposition:'reviewed',expected_review_id:'safety-review:initial-receipt',expected_sha256:'a'.repeat(64)});expect(value.report.status).toBe('reviewed');expect(value.report.review_history).toEqual([initial,saved]);expect(ui.find('form')).toBeUndefined();
 });
 it('does not mistake the old follow-up receipt for a successful uncertain resolution after reload',async()=>{
  const stored=storage(),initial={id:'safety-review:initial-receipt',created_at:'2026-11-11T11:00:00Z',disposition:'follow_up_required' as const,reason_code:'other' as const,note:''};
  const value:HomeworkSafetyReportDetail={...detail(),report:{...detail().report,status:'follow_up_required',review:initial,review_history:[initial]}};
  const saved={id:'review-final',created_at:'2026-11-11T12:00:00Z',disposition:'reviewed' as const,reason_code:'addressed' as const,note:''};
  const final={...value,report:{...value.report,status:'reviewed',review:saved,review_history:[initial,saved]}};
  const fetcher=vi.fn().mockRejectedValueOnce(new Error('lost final response')).mockResolvedValueOnce(new Response(JSON.stringify(value))).mockResolvedValueOnce(new Response(JSON.stringify(final)));vi.stubGlobal('fetch',fetcher);
  let ui=view(()=>HomeworkSafetyStaffReview({schoolId:'school-one',value,changed:vi.fn()}));ui.find('textarea')!.props.onChange!({target:{value:'Human completed the follow-up.'}});ui.find('input')!.props.onChange!({target:{checked:true}});await submit(ui.find('form')!);expect([...stored.values()][0]).toContain('safety-review:initial-receipt');
  resetHooks();ui=view(()=>HomeworkSafetyStaffReview({schoolId:'school-one',value,changed:vi.fn()}));expect(ui.find('form')).toBeUndefined();ui.find('button','Check saved review')!.props.onClick!();await settle();expect(ui.find('form')).toBeUndefined();expect(ui.find(Notice)?.props.error).toContain('has not been confirmed');expect(stored.size).toBe(1);
  ui.find('button','Check saved review')!.props.onClick!();await settle();expect(stored.size).toBe(0);expect(fetcher.mock.calls.filter(call=>call[1].method==='POST')).toHaveLength(1);
 });

});
