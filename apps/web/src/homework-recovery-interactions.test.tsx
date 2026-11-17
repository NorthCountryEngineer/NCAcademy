import { afterEach, describe, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { HomeworkAI } from './HomeworkAI';
import { Notice } from './components';
import type { HomeworkAIIndex, HomeworkAIMessage } from './homework-ai-api';

const hooks=vi.hoisted(()=>({states:[] as unknown[],index:0,dirty:false}));
vi.mock('react',async importOriginal=>({...await importOriginal<typeof import('react')>(),
 useState:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push(typeof initial==='function'?initial():initial);return [hooks.states[index],(value:unknown)=>{const next=typeof value==='function'?value(hooks.states[index]):value;if(!Object.is(next,hooks.states[index])){hooks.states[index]=next;hooks.dirty=true;}}];},
 useRef:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push({current:initial});return hooks.states[index];},
 useEffect:()=>{},
}));
type Element=ReactElement<{children?:ReactNode;disabled?:boolean;value?:unknown;error?:string;'aria-label'?:string;onChange?:(event:{target:{value:string}})=>void;onClick?:()=>void;onSubmit?:(event:{preventDefault:()=>void})=>Promise<void>}>;
function nodes(node:ReactNode):Element[]{if(Array.isArray(node))return node.flatMap(nodes);if(!isValidElement<{children?:ReactNode}>(node))return [];return [node as Element,...nodes(node.props.children)];}
function text(node:ReactNode):string{if(Array.isArray(node))return node.map(text).join('');if(isValidElement<{children?:ReactNode}>(node))return text(node.props.children);return typeof node==='string'||typeof node==='number'?String(node):'';}
function view(index:HomeworkAIIndex){function render(){let tree:ReactNode;do{hooks.dirty=false;hooks.index=0;tree=HomeworkAI({schoolId:'school',userId:'student',index,openCourse:()=>{}});}while(hooks.dirty);return tree!;}return {render,find:(kind:unknown,label='')=>nodes(render()).find(node=>node.type===kind&&text(node).includes(label)),findLabel:(label:string)=>nodes(render()).find(node=>node.props['aria-label']===label)};}
const index:HomeworkAIIndex={assistant:{school_id:'school',version:1,revision_id:'revision',approved_revision_id:'revision',approved:true,available:true,live_models_enabled:true,configuration_sha256:'a'.repeat(64),approval_required:false,privacy_mode:'deidentified',local_only_images:true,configuration:{enabled:true,name:'Study helper',biography:'',allow_images:false,max_input_chars:5000,max_output_tokens:500,privacy_mode:'deidentified'}},courses:[],sessions:[]};
const session={id:'session',created_at:'2026-11-17',title:'New conversation'};
const reply:HomeworkAIMessage={id:'reply',message:'Explain multiplication',created_at:'2026-11-17',idempotency_key:'reply-key',status:'released',error:null,answer:'Think about equal groups.',triage:{method:'general',course_id:null,course_name:null,prompt:null,candidates:[]},citations:[],references:[],images:[],usage:{},resume_available:false};
const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status});
function storage(){let next=0;vi.stubGlobal('crypto',{randomUUID:()=>`request-${++next}`});const values=new Map<string,string>();vi.stubGlobal('window',{sessionStorage:{getItem:(key:string)=>values.get(key)||null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)}});return values;}
async function submit(ui:ReturnType<typeof view>){ui.find('textarea')!.props.onChange!({target:{value:'Explain multiplication'}});await ui.find('form')!.props.onSubmit!({preventDefault:vi.fn()});}
afterEach(()=>{hooks.states=[];hooks.index=0;hooks.dirty=false;vi.unstubAllGlobals();});

describe('Homework Help rejected-request recovery',()=>{
 it.each([
  [429,'This conversation has 100 questions. Start a new conversation; daily limits still apply'],
  [429,'Daily Homework Help question or picture limit reached'],
  [403,'Choose only a currently enrolled course'],
  [409,'A previous AI question has an unresolved outcome; ask your administrator. No automatic retry is permitted'],
 ] as const)('unlocks a known unsent question rejection (%s), preserving the question until an explicit next action',async(status,detail)=>{
  const stored=storage(),fetcher=vi.fn().mockResolvedValueOnce(response(session)).mockResolvedValueOnce(response({detail},status)).mockResolvedValueOnce(response({...session,id:'next-session'})).mockResolvedValueOnce(response(reply));vi.stubGlobal('fetch',fetcher);
  const ui=view(index);await submit(ui);
  expect(ui.find(Notice)?.props.error).toBe(detail);expect(ui.find('textarea')?.props.value).toBe('Explain multiplication');expect(ui.find('fieldset')?.props.disabled).toBe(false);expect(ui.find('button','New conversation')?.props.disabled).toBe(false);expect(stored.size).toBe(0);expect(fetcher).toHaveBeenCalledTimes(2);
  ui.find('button','New conversation')!.props.onClick!();await submit(ui);
  expect(fetcher).toHaveBeenCalledTimes(4);expect(stored.size).toBe(0);expect(ui.find('textarea')?.props.value).toBe('');
 });
 it('recovers from a refused conversation creation without submitting the question or retaining an unusable key',async()=>{
  const stored=storage(),fetcher=vi.fn().mockResolvedValueOnce(response({detail:'Daily Homework Help conversation creation limit reached'},429));vi.stubGlobal('fetch',fetcher);
  const ui=view(index);await submit(ui);
  expect(ui.find('fieldset')?.props.disabled).toBe(false);expect(ui.find('textarea')?.props.value).toBe('Explain multiplication');expect(stored.size).toBe(0);expect(fetcher).toHaveBeenCalledOnce();expect(fetcher.mock.calls[0][0]).toBe('/api/homework-help/ai/sessions');
 });
 it('keeps a recognized unsupported equation editable and sends only an explicit corrected question',async()=>{
  const question='Help me factor x^2 - 5x + 6 without just giving me the answer.';
  const clarification:HomeworkAIMessage={...reply,message:question,status:'clarify',privacy_mode:'deidentified',answer:'That full expression is outside the math this mode supports.',deidentification:{status:'clarify',reason:'unsupported_math',task_summary:null,image_read:false,action:'hint',stage:0,local_only_images:true}};
  const stored=storage(),fetcher=vi.fn().mockResolvedValueOnce(response(session)).mockResolvedValueOnce(response(clarification)).mockResolvedValueOnce(response({...reply,id:'corrected'}));vi.stubGlobal('fetch',fetcher);
  const ui=view(index);ui.find('textarea')!.props.onChange!({target:{value:question}});await ui.find('form')!.props.onSubmit!({preventDefault:vi.fn()});
  expect(ui.find('textarea')?.props.value).toBe(question);expect(ui.find('fieldset')?.props.disabled).toBe(false);expect(stored.size).toBe(0);expect(fetcher).toHaveBeenCalledTimes(2);
  await submit(ui);expect(fetcher).toHaveBeenCalledTimes(3);expect(JSON.parse(fetcher.mock.calls[2][1].body).message).toBe('Explain multiplication');expect(ui.find('textarea')?.props.value).toBe('');
 });
 it.each([
  new TypeError('Connection lost'),
  response({detail:'Unrecognized conflict'},409),
  response({detail:'Daily Homework Help question or picture limit reached'},503),
  response({detail:'Unrecognized rejection'},403),
 ])('keeps ambiguous outcomes locked and reconciles by reading without a repeated send',async(failure)=>{
  const stored=storage(),fetcher=vi.fn().mockResolvedValueOnce(response(session));
  if(failure instanceof Error)fetcher.mockRejectedValueOnce(failure);else fetcher.mockResolvedValueOnce(failure);
  fetcher.mockResolvedValueOnce(response({...index,sessions:[session]})).mockResolvedValueOnce(response({session,messages:[]}));vi.stubGlobal('fetch',fetcher);
  const ui=view(index);await submit(ui);expect(ui.find('fieldset')?.props.disabled).toBe(true);expect(stored.size).toBe(1);
  await ui.find('form')!.props.onSubmit!({preventDefault:vi.fn()});expect(fetcher).toHaveBeenCalledTimes(2);
  ui.findLabel('Refresh history')!.props.onClick!();await new Promise(resolve=>setTimeout(resolve,0));
  expect(fetcher).toHaveBeenCalledTimes(4);expect(fetcher.mock.calls.filter(call=>call[1].method==='POST')).toHaveLength(2);expect(ui.find('fieldset')?.props.disabled).toBe(true);expect(stored.size).toBe(1);expect(ui.find(Notice)?.props.error).toContain('No additional question was sent');
 });
});
