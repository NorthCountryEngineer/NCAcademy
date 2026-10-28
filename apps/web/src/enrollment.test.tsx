import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Enrollment } from './Enrollment';
import { Empty, Notice, Spinner } from './components';
import { setCsrf, type User } from './api';
import { enrollmentStudentName, humanEnrollmentTeachers, searchEnrollmentCandidates, type EnrollmentCandidate, type EnrollmentCandidates, type EnrollmentMember } from './enrollment-api';

const hooks=vi.hoisted(()=>({states:[] as unknown[],dependencies:[] as (readonly unknown[]|undefined)[],cleanups:[] as (((()=>void)|undefined))[],pending:[] as (()=>void)[],index:0,effectIndex:0,dirty:false}));
vi.mock('react',async importOriginal=>({...await importOriginal<typeof import('react')>(),useState:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push(typeof initial==='function'?initial():initial);return [hooks.states[index],(value:unknown)=>{const next=typeof value==='function'?value(hooks.states[index]):value;if(!Object.is(next,hooks.states[index])){hooks.states[index]=next;hooks.dirty=true;}}];},useEffect:(effect:()=>void|(()=>void),dependencies?:readonly unknown[])=>{const index=hooks.effectIndex++,previous=hooks.dependencies[index];if(!previous||!dependencies||previous.length!==dependencies.length||dependencies.some((value,i)=>!Object.is(value,previous[i]))){hooks.dependencies[index]=dependencies;hooks.pending.push(()=>{hooks.cleanups[index]?.();hooks.cleanups[index]=effect()||undefined;});}}}));
type Element=ReactElement<{children?:ReactNode;type?:string;name?:string;disabled?:boolean;checked?:boolean;value?:unknown;error?:string;title?:string;text?:string;onClick?:()=>void;onChange?:(event:{target:{value:string}})=>void;onSubmit?:(event:{preventDefault:()=>void})=>Promise<void>}>;
function nodes(node:ReactNode):Element[]{if(Array.isArray(node))return node.flatMap(nodes);if(!isValidElement<{children?:ReactNode}>(node))return [];return [node as Element,...nodes(node.props.children)];}
function text(node:ReactNode):string{if(Array.isArray(node))return node.map(text).join('');if(isValidElement<{children?:ReactNode}>(node))return text(node.props.children);return typeof node==='string'||typeof node==='number'?String(node):'';}
const third:EnrollmentCandidate={id:'opaque-grade3',name:'Alex Example',preferred_name:'Lex',display_name:'Lex (Alex Example)',username:'lex-three',grade:'3',grade_level:3,academic_year:'2026-2027'};
const tenth:EnrollmentCandidate={...third,id:'opaque-grade10',name:'Riley Example',preferred_name:'',display_name:'Riley Example',username:'riley-ten',grade:'10',grade_level:10};
const unplaced:EnrollmentCandidate={...third,id:'opaque-unplaced',name:'Taylor Example',preferred_name:'',display_name:'Taylor Example',username:'taylor',grade:'',grade_level:null};
const owner:EnrollmentMember={user_id:'owner',name:'Morgan Teacher',role:'teacher',principal_kind:'human'};
function page(candidates:EnrollmentCandidate[],overrides:Partial<EnrollmentCandidates>={}):EnrollmentCandidates{return {course_id:'math-3',school_id:'school',course_grade_levels:[3],selected_grade_levels:[3],grade_filter:'course',candidates,total:candidates.length,offset:0,limit:50,...overrides};}
function mockApi(){
 const fetcher=vi.fn(async(path:string,options?:RequestInit)=>{
  if(path==='/api/courses/math-3/enrollment-candidates/search?school_id=school'){
   const query=JSON.parse(String(options?.body));let available=query.grade==='all'?[third,tenth,unplaced]:query.grade==='unplaced'?[unplaced]:query.grade==='10'?[tenth]:[third];if(query.search)available=available.filter(student=>[student.name,student.preferred_name,student.username].join(' ').toLowerCase().includes(query.search.toLowerCase()));
   return new Response(JSON.stringify(page(available,{grade_filter:query.grade,offset:query.offset,selected_grade_levels:query.grade==='all'?null:query.grade==='unplaced'?[]:[Number(query.grade==='course'?3:query.grade)]})));
  }
  if(path==='/api/users?school_id=school')return new Response(JSON.stringify([{id:'human',name:'Casey Teacher',role:'teacher',principal_kind:'human'},{id:'agent',name:'Private AI Principal',role:'teacher',principal_kind:'agent'},{id:'owner',name:owner.name,role:'teacher',principal_kind:'human'},{id:'student',name:'Not a teacher',role:'student',principal_kind:'human'}]));
  if(options?.method==='POST'&&path==='/api/courses/math-3/enrollments?school_id=school')return new Response('{}');
  if(options?.method==='DELETE')return new Response(null,{status:204});
  throw new Error(`Unexpected synthetic URL ${path}`);
 });vi.stubGlobal('fetch',fetcher);return fetcher;
}
function view(admin=true,members:EnrollmentMember[]=[owner]){
 const close=vi.fn(),saved=vi.fn(async()=>{});let currentMembers=members;
 function render(){let tree:ReactNode;do{hooks.dirty=false;hooks.index=0;hooks.effectIndex=0;tree=Enrollment({courseId:'math-3',schoolId:'school',ownerId:'owner',members:currentMembers,admin,close,saved});hooks.pending.splice(0).forEach(effect=>effect());}while(hooks.dirty);return tree!;}
 function control(label:string){const row=nodes(render()).find(node=>node.type==='label'&&text(node).startsWith(label))!;return nodes(row).find(node=>node.type==='select'||node.type==='input')!;}
 return {render,saved,close,control,change:(label:string,value:string)=>{control(label).props.onChange!({target:{value}});render();},select:(name:string)=>{const row=nodes(render()).find(node=>node.type==='label'&&text(node).includes(name))!;nodes(row).find(node=>node.type==='input'&&node.props.type==='radio')!.props.onChange!({target:{value:''}});render();},submit:()=>nodes(render()).find(node=>node.type==='form')!.props.onSubmit!({preventDefault:vi.fn()}),saveButton:()=>nodes(render()).find(node=>node.type==='button'&&['Enroll student','Save enrollment'].includes(text(node)))!,updateMembers:(value:EnrollmentMember[])=>{currentMembers=value;render();},click:(label:string)=>{nodes(render()).find(node=>node.type==='button'&&text(node)===label)!.props.onClick!();render();}};
}
async function settle(ui:ReturnType<typeof view>,duration=250){await vi.advanceTimersByTimeAsync(duration);ui.render();}
beforeEach(()=>{vi.useFakeTimers();setCsrf('synthetic-csrf');});
afterEach(()=>{hooks.cleanups.forEach(cleanup=>cleanup?.());hooks.states=[];hooks.dependencies=[];hooks.cleanups=[];hooks.pending=[];hooks.index=0;hooks.effectIndex=0;hooks.dirty=false;vi.useRealTimers();vi.unstubAllGlobals();setCsrf('');});

describe('readable grade-aware course enrollment',()=>{
 it('defaults to class grades and shows real and preferred names, usernames and placement without a global user fetch',async()=>{
  const fetcher=mockApi(),ui=view();ui.render();expect(nodes(ui.render()).some(node=>node.type===Spinner)).toBe(true);expect(ui.saveButton().props.disabled).toBe(true);await settle(ui);const visible=text(ui.render());
  for(const expected of ['Lex (Alex Example)','@lex-three','Grade 3','2026-2027','Class grades · Grade 3','1 matching student','Morgan Teacher'])expect(visible).toContain(expected);
  for(const forbidden of ['opaque-grade3','opaque-grade10','Riley Example','Student 10b3bd31'])expect(visible).not.toContain(forbidden);
  expect(fetcher).toHaveBeenCalledOnce();expect(JSON.parse(String(fetcher.mock.calls[0][1]!.body))).toEqual({grade:'course',search:'',offset:0,limit:50});expect(ui.saveButton().props.disabled).toBe(true);
 });
 it('allows explicit all-grade and other-grade placement, then resets a now-hidden selection before saving',async()=>{
  mockApi();const ui=view();ui.render();await settle(ui);ui.select('Alex Example');expect(ui.saveButton().props.disabled).toBe(false);
  ui.change('Grade level','all');expect(ui.saveButton().props.disabled).toBe(true);expect(text(ui.render())).not.toContain('Alex Example');await settle(ui);expect(text(ui.render())).toContain('Riley Example');expect(text(ui.render())).toContain('Grade not set');
  ui.select('Riley Example');ui.change('Grade level','3');expect(ui.saveButton().props.disabled).toBe(true);await settle(ui);expect(text(ui.render())).not.toContain('Riley Example');expect(nodes(ui.render()).filter(node=>node.type==='input'&&node.props.checked)).toHaveLength(0);await ui.submit();expect(ui.saved).not.toHaveBeenCalled();
  ui.change('Grade level','10');await settle(ui);expect(text(ui.render())).toContain('Riley Example');expect(text(ui.render())).not.toContain('Alex Example');
 });
 it('searches names and usernames in a protected POST body and debounces rapid changes',async()=>{
  const fetcher=mockApi(),ui=view();ui.render();await settle(ui);ui.change('Grade level','all');await settle(ui);ui.change('Search students','ri');ui.change('Search students','riley-ten');await settle(ui,199);expect(fetcher).toHaveBeenCalledTimes(2);await settle(ui,1);expect(text(ui.render())).toContain('Riley Example');expect(text(ui.render())).not.toContain('Alex Example');
  const [url,options]=fetcher.mock.calls.at(-1)!;expect(url).not.toContain('riley');expect(options?.method).toBe('POST');expect(JSON.parse(String(options?.body))).toEqual({grade:'all',search:'riley-ten',offset:0,limit:50});expect((options?.headers as Headers).get('X-CSRF-Token')).toBe('synthetic-csrf');
 });
 it('keeps stale asynchronous results from replacing a newer grade selection or exposing a removed selection',async()=>{
  const fetcher=mockApi();let resolveOld!:(response:Response)=>void;fetcher.mockImplementationOnce(()=>new Promise<Response>(resolve=>{resolveOld=resolve;}));const ui=view();ui.render();await settle(ui,0);ui.change('Grade level','10');await settle(ui);expect(text(ui.render())).toContain('Riley Example');resolveOld(new Response(JSON.stringify(page([third]))));await settle(ui);expect(text(ui.render())).toContain('Riley Example');expect(text(ui.render())).not.toContain('Alex Example');expect(ui.saveButton().props.disabled).toBe(true);
 });
 it('shows unplaced students explicitly and explains empty or failed searches with a safe retry',async()=>{
  const fetcher=mockApi(),ui=view();ui.render();await settle(ui);ui.change('Grade level','unplaced');await settle(ui);expect(text(ui.render())).toContain('Taylor Example');expect(text(ui.render())).toContain('Grade not set');ui.change('Search students','missing');await settle(ui);expect(nodes(ui.render()).find(node=>node.type===Empty)?.props.title).toBe('No students match these filters');expect(ui.saveButton().props.disabled).toBe(true);
  fetcher.mockImplementationOnce(async()=>new Response(JSON.stringify({detail:'Student search unavailable'}),{status:503}));ui.change('Search students','taylor');await settle(ui);expect(nodes(ui.render()).find(node=>node.type===Notice&&node.props.error)?.props.error).toBe('Student search unavailable');expect(ui.saveButton().props.disabled).toBe(true);ui.click('Retry list');await settle(ui);expect(text(ui.render())).toContain('Taylor Example');
 });
 it('uses human-readable roster names and excludes already enrolled students even when a result arrives during a roster refresh',async()=>{
  mockApi();const ui=view(true,[owner,{user_id:third.id,name:'Alex Example',display_name:'Lex (Alex Example)',preferred_name:'Lex',username:'lex-three',grade:'3',grade_level:3,academic_year:'2026-2027',role:'student',principal_kind:'human'}]);ui.render();await settle(ui);const tree=ui.render();expect(text(tree)).toContain('Lex (Alex Example)');expect(text(tree)).toContain('@lex-three');expect(nodes(tree).filter(node=>node.type==='input'&&node.props.type==='radio')).toHaveLength(0);expect(text(tree)).not.toContain(third.id);
 });
 it('submits only the selected principal and course role through the unchanged enrollment mutation',async()=>{
  const fetcher=mockApi(),ui=view();ui.render();await settle(ui);ui.select('Alex Example');await ui.submit();const mutations=fetcher.mock.calls.filter(([url])=>url==='/api/courses/math-3/enrollments?school_id=school');expect(mutations).toHaveLength(1);expect(JSON.parse(String(mutations[0][1]!.body))).toEqual({user_id:third.id,role:'student'});expect(ui.saved).toHaveBeenCalledOnce();expect(ui.close).toHaveBeenCalledOnce();
 });
 it('keeps administrator teacher enrollment separate, excludes AI principals and current members, and hides it from class teachers',async()=>{
  const fetcher=mockApi(),ui=view();ui.render();await settle(ui);ui.change('Course role','teacher');await settle(ui);const teacherSelect=ui.control('Teacher');expect(text(teacherSelect)).toContain('Casey Teacher');for(const forbidden of ['Private AI Principal','Morgan Teacher','Not a teacher'])expect(text(teacherSelect)).not.toContain(forbidden);expect(text(ui.render())).toContain('Assign AI teacher profiles in Configure course');expect(fetcher.mock.calls.at(-1)![0]).toBe('/api/users?school_id=school');ui.change('Teacher','human');await ui.submit();expect(JSON.parse(String(fetcher.mock.calls.at(-1)![1]!.body))).toEqual({user_id:'human',role:'teacher'});
  expect(humanEnrollmentTeachers([{id:'a',name:'AI',role:'teacher',principal_kind:'agent'}] as User[],[],'')).toEqual([]);
 });
 it('does not offer teacher enrollment to ordinary class teachers or expose student identifiers as labels',()=>{
  mockApi();const ui=view(false,[owner,{user_id:'private-opaque-id',role:'student',principal_kind:'human'}]);const visible=text(ui.render());expect(text(ui.control('Course role'))).toBe('Student');expect(visible).not.toContain('private-opaque-id');expect(visible).toContain('Student');expect(enrollmentStudentName({name:'',username:'learner'})).toBe('learner');
 });
 it('treats custom classes without grade alignment as all-grade by default and retains escaped labels',async()=>{
  const fetcher=mockApi();fetcher.mockImplementationOnce(async()=>new Response(JSON.stringify(page([{...third,name:'Synthetic <script>',display_name:'Synthetic <script>'}],{course_grade_levels:[],selected_grade_levels:null,grade_filter:'all'}))));const ui=view();ui.render();await settle(ui);expect(text(ui.render())).toContain('All grades · class has no grade set');const html=renderToStaticMarkup(<strong>{enrollmentStudentName({...third,display_name:'Synthetic <script>'})}</strong>);expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');
 });
 it('requests subsequent bounded pages and clears the previous page selection',async()=>{
  const fetcher=mockApi();fetcher.mockImplementationOnce(async()=>new Response(JSON.stringify(page([third],{total:75}))));const ui=view();ui.render();await settle(ui);expect(text(ui.render())).toContain('Showing 1–50');ui.select('Alex Example');ui.click('Next students');expect(ui.saveButton().props.disabled).toBe(true);await settle(ui);expect(JSON.parse(String(fetcher.mock.calls.at(-1)![1]!.body)).offset).toBe(50);
 });
 it('uses the course-scoped body search contract with encoded scope, never a family-account endpoint',async()=>{
  const fetcher=vi.fn(async(_path:string,_options?:RequestInit)=>new Response(JSON.stringify(page([]))));vi.stubGlobal('fetch',fetcher);await searchEnrollmentCandidates('school a/b','course a/b',{grade:'3',search:'Synthetic Name',offset:0,limit:50});expect(fetcher.mock.calls[0][0]).toBe('/api/courses/course%20a%2Fb/enrollment-candidates/search?school_id=school%20a%2Fb');expect(String(fetcher.mock.calls[0][0])).not.toContain('Synthetic');
 });
});
