import { afterEach, describe, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { TeacherModelPicker, TeacherModelRoutingPreview, TeacherModels, TeacherModelsEditor } from './TeacherModels';
import { Notice } from './components';
import { blankTeacherModelPolicy, type TeacherModelDescriptor, type TeacherModelPolicy } from './teacher-model-api';
import { blankBehavior, type TeacherProfile } from './teacher-api';

const hooks=vi.hoisted(()=>({states:[] as unknown[],dependencies:[] as (readonly unknown[]|undefined)[],index:0,effectIndex:0,pending:[] as (()=>void)[],dirty:false}));
vi.mock('react',async importOriginal=>({...await importOriginal<typeof import('react')>(),useState:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push(typeof initial==='function'?initial():initial);return [hooks.states[index],(value:unknown)=>{const next=typeof value==='function'?value(hooks.states[index]):value;if(!Object.is(next,hooks.states[index])){hooks.states[index]=next;hooks.dirty=true;}}];},useEffect:(effect:()=>void,dependencies?:readonly unknown[])=>{const index=hooks.effectIndex++,previous=hooks.dependencies[index];if(!previous||!dependencies||previous.length!==dependencies.length||dependencies.some((value,i)=>!Object.is(value,previous[i]))){hooks.dependencies[index]=dependencies;hooks.pending.push(effect);}}}));

type Element=ReactElement<{children?:ReactNode;disabled?:boolean;checked?:boolean;value?:unknown;error?:string;change?:(value:unknown)=>void;onChange?:(event:{target:{checked?:boolean;value?:string}})=>void;onClick?:()=>void;onSubmit?:(event:{preventDefault:()=>void})=>Promise<void>}>;
function nodes(node:ReactNode):Element[]{if(Array.isArray(node))return node.flatMap(nodes);if(!isValidElement<{children?:ReactNode}>(node))return [];return [node as Element,...nodes(node.props.children)];}
function text(node:ReactNode):string{if(Array.isArray(node))return node.map(text).join('');if(isValidElement<{children?:ReactNode}>(node))return text(node.props.children);return typeof node==='string'||typeof node==='number'?String(node):'';}
const profile=():TeacherProfile=>({id:'river',school_id:'school-a',kind:'ai',name:'River',title:'Science teacher',pronouns:'',biography:'KEEP_BIOGRAPHY',contact_email:null,expertise:['Science'],picture:null,ai_behavior:{...blankBehavior(),instructions:'KEEP_INSTRUCTIONS'},principal_id:null,agent_placement_id:null,status:'ready',version:3,course_ids:['class-a'],assigned_courses:[],can_manage:true,authority_granted:false,approved_revision_id:'profile-approved',approved_version:3,approval_current:true,activation:{identity_linked:false,identity_eligible:false,agent_admission_enabled:false,live_models_enabled:false,authority_granted:false,runtime_active:false}});
function policy():TeacherModelPolicy{return {...blankTeacherModelPolicy(profile()),primary:{connection_id:'integration-a',model:'primary'}};}
function descriptor():TeacherModelDescriptor{const current=policy();return {version:2,draft:{revision_id:'draft-two',sha256:'d'.repeat(64),policy:current},active:{revision_id:'approved-one',approval_id:'approval-one',sha256:'a'.repeat(64),policy:{...current,primary:{connection_id:'integration-a',model:'old-model'}},connections:[{id:'integration-a',name:'School integration',models:['old-model','primary','replacement'],enabled:true,version:1}]},draft_pending:true,can_manage:true,automatic_routing_enabled:false,homework_images:'school_models_only',pricing_status:'unknown'};}
function view(renderComponent:()=>ReactNode){function render(){let tree:ReactNode;do{hooks.dirty=false;hooks.index=0;hooks.effectIndex=0;tree=renderComponent();hooks.pending.splice(0).forEach(effect=>effect());}while(hooks.dirty);return tree!;}return {render,find:(kind:unknown,label='')=>nodes(render()).find(node=>node.type===kind&&text(node).includes(label)),label:(label:string)=>nodes(render()).find(node=>node.type==='label'&&text(node).startsWith(label))!};}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
const submit=(element:Element)=>element.props.onSubmit!({preventDefault:vi.fn()});
function checkbox(element:Element){return nodes(element.props.children).find(node=>node.type==='input')!;}
afterEach(()=>{hooks.states=[];hooks.dependencies=[];hooks.pending=[];hooks.index=0;hooks.effectIndex=0;hooks.dirty=false;vi.unstubAllGlobals();});

describe('teacher model form interactions',()=>{
 it('saves only the model policy while retaining teacher metadata, corpus assignment and profile approval',async()=>{
  const connections=[{id:'integration-a',name:'School integration',endpoint:'https://models.example',models:['primary','replacement'],enabled:true}],returned={...descriptor(),version:3};
  const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(connections))).mockResolvedValueOnce(new Response(JSON.stringify(returned)));vi.stubGlobal('fetch',fetcher);
  const teacher=profile(),before=JSON.stringify(teacher),saved=vi.fn(),ui=view(()=>TeacherModelsEditor({schoolId:'school-a',profile:teacher,descriptor:descriptor(),close:()=>{},saved}));ui.render();await settle();ui.render();
  const picker=ui.find(TeacherModelPicker)!;picker.props.change!({connection_id:'integration-a',model:'replacement'});ui.render();await submit(ui.find('form')!);
  expect(fetcher).toHaveBeenCalledTimes(2);const [path,options]=fetcher.mock.calls[1];expect(path).toBe('/api/schools/school-a/teachers/river/models');expect(options.method).toBe('PUT');const body=JSON.parse(options.body);expect(body.expected_version).toBe(2);expect(body.policy.primary.model).toBe('replacement');for(const field of ['KEEP_BIOGRAPHY','KEEP_INSTRUCTIONS','course_ids','approved_revision_id','picture'])expect(options.body).not.toContain(field);expect(JSON.stringify(teacher)).toBe(before);expect(saved).toHaveBeenCalledExactlyOnceWith(returned);
 });
 it('clears the image capability attestation when swapping primary models',async()=>{
  const initial=descriptor();initial.draft!.policy.assignment_images_reviewed=true;initial.draft!.policy.lesson_policy.allow_lesson_curation=true;initial.draft!.policy.lesson_policy.allow_assignment_images=true;
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify([{id:'integration-a',name:'School integration',models:['primary','replacement'],enabled:true}])));vi.stubGlobal('fetch',fetcher);
  const ui=view(()=>TeacherModelsEditor({schoolId:'school-a',profile:profile(),descriptor:initial,close:()=>{},saved:vi.fn()}));ui.render();await settle();ui.find(TeacherModelPicker)!.props.change!({connection_id:'integration-a',model:'replacement'});
  expect(checkbox(ui.label('I reviewed the primary model’s image-input support')).props.checked).toBe(false);expect(fetcher).toHaveBeenCalledOnce();
 });
 it('retains an explicit error for unavailable primary models and does not save',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response('[]'));vi.stubGlobal('fetch',fetcher);const saved=vi.fn(),ui=view(()=>TeacherModelsEditor({schoolId:'school-a',profile:profile(),descriptor:descriptor(),close:()=>{},saved}));ui.render();await settle();await submit(ui.find('form')!);
  expect(ui.find(Notice)?.props.error).toContain('primary model is no longer available');expect(fetcher).toHaveBeenCalledOnce();expect(saved).not.toHaveBeenCalled();
 });
 it('requires human review for the exact draft and keeps the previous approved policy until approval',async()=>{
  let finish!:(value:Response)=>void;const current=descriptor(),approved={...current,draft_pending:false,active:{...current.active!,...current.draft!,approval_id:'approval-two'}};
  const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(current))).mockImplementationOnce(()=>new Promise<Response>(resolve=>{finish=resolve;}));vi.stubGlobal('fetch',fetcher);
  const ui=view(()=>TeacherModels({schoolId:'school-a',profile:profile(),manage:true}));ui.render();await settle();
  expect(text(ui.render())).toContain('The current approved policy remains active');expect(ui.find('button','Approve model policy')?.props.disabled).toBe(true);await submit(ui.find('form')!);expect(fetcher).toHaveBeenCalledOnce();
  checkbox(ui.label('I reviewed this model assignment')).props.onChange!({target:{checked:true}});const approval=submit(ui.find('form')!);expect(ui.find('button','Approving model policy')?.props.disabled).toBe(true);await submit(ui.find('form')!);expect(fetcher).toHaveBeenCalledTimes(2);const [path,options]=fetcher.mock.calls[1];expect(path).toBe('/api/schools/school-a/teachers/river/models/approve');expect(JSON.parse(options.body)).toEqual({expected_version:2,revision_id:'draft-two'});
  finish(new Response(JSON.stringify(approved)));await approval;expect(ui.find('form')).toBeUndefined();expect(text(ui.render())).toContain('Approved model policy');expect(text(ui.render())).not.toContain('Draft model policy');
 });
 it('does not fetch or permit administration for a human teacher or non-manager',()=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);let teacher=profile(),manage=false;const ui=view(()=>TeacherModels({schoolId:'school-a',profile:teacher,manage}));expect(ui.render()).toBeNull();manage=true;teacher={...teacher,kind:'human'};expect(ui.render()).toBeNull();expect(fetcher).not.toHaveBeenCalled();
 });
 it('keeps a previously approved but unavailable model policy visibly unavailable',async()=>{
  const current={...descriptor(),draft_pending:false,active_available:false,unavailable_reason:'The approved model integration changed. Review and approve the current integration.'};const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(current)));vi.stubGlobal('fetch',fetcher);
  const ui=view(()=>TeacherModels({schoolId:'school-a',profile:profile(),manage:true}));ui.render();await settle();expect(text(ui.render())).toContain('Approved policy · model unavailable');expect(nodes(ui.render()).filter(node=>node.type===Notice).some(node=>node.props.error===current.unavailable_reason)).toBe(true);expect(ui.find('button','Manage models')).toBeDefined();
 });
});

describe('saved model route preview interactions',()=>{
 it('only previews after an explicit request, reports primary execution and makes no model send',async()=>{
  const result={task:'reasoning',reason_code:'reasoning_terms',executed:{connection_id:'integration-a',model:'primary',label:'Primary model',selection_reason:'approved_primary'},suggested:{connection_id:'integration-b',model:'reasoner',label:'Reasoning candidate',candidate_id:'reasoner'},automatic_routing_enabled:false,no_provider_calls:true,pricing_status:'unknown',api_key:'PRIVATE_KEY',endpoint:'PRIVATE_ENDPOINT',prompt:'PRIVATE_PROMPT'};
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(result)));vi.stubGlobal('fetch',fetcher);const ui=view(()=>TeacherModelRoutingPreview({schoolId:'school-a',teacherId:'river',descriptor:descriptor()}));ui.render();expect(fetcher).not.toHaveBeenCalled();
  const input=ui.find('textarea')!;input.props.onChange!({target:{value:'Why does evaporation cool water?'}});await submit(ui.find('form')!);
  expect(fetcher).toHaveBeenCalledOnce();expect(fetcher.mock.calls[0][0]).toBe('/api/schools/school-a/teachers/river/models/preview');expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({text:'Why does evaporation cool water?',has_images:false});const visible=text(ui.render());for(const expected of ['Primary model','Reasoning candidate','No provider was called','Cost estimate unavailable'])expect(visible).toContain(expected);for(const forbidden of ['PRIVATE_KEY','PRIVATE_ENDPOINT','PRIVATE_PROMPT','$0'])expect(visible).not.toContain(forbidden);
 });
});
