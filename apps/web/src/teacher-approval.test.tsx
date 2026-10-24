import { afterEach, describe, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { TeacherDetail } from './Teachers';
import { Notice } from './components';
import { setCsrf, type School, type User } from './api';
import { blankBehavior, type TeacherProfile } from './teacher-api';

// Exercise the actual form handlers without adding a browser-DOM dependency.
const hooks=vi.hoisted(()=>({states:[] as unknown[],dependencies:[] as (readonly unknown[]|undefined)[],index:0,effectIndex:0,pending:[] as (()=>void)[],dirty:false}));
vi.mock('react',async importOriginal=>({
 ...await importOriginal<typeof import('react')>(),
 useState:(initial:unknown)=>{
  const index=hooks.index++;
  if(index>=hooks.states.length)hooks.states.push(typeof initial==='function'?initial():initial);
  return [hooks.states[index],(value:unknown)=>{const next=typeof value==='function'?value(hooks.states[index]):value;if(!Object.is(next,hooks.states[index])){hooks.states[index]=next;hooks.dirty=true;}}];
 },
 useEffect:(effect:()=>void,dependencies?:readonly unknown[])=>{
  const index=hooks.effectIndex++,previous=hooks.dependencies[index];
  if(!previous||!dependencies||previous.length!==dependencies.length||dependencies.some((value,i)=>!Object.is(value,previous[i]))){hooks.dependencies[index]=dependencies;hooks.pending.push(effect);}
 },
}));

type FormElement=ReactElement<{children?:ReactNode;disabled?:boolean;checked?:boolean;onChange?:(event:{target:{checked:boolean}})=>void;onSubmit?:(event:{preventDefault:()=>void})=>Promise<void>}>;
function elements(node:ReactNode):FormElement[]{
 if(Array.isArray(node))return node.flatMap(elements);
 if(!isValidElement<{children?:ReactNode}>(node))return [];
 return [node as FormElement,...elements(node.props.children)];
}
function text(node:ReactNode):string{
 if(Array.isArray(node))return node.map(text).join('');
 if(isValidElement<{children?:ReactNode}>(node))return text(node.props.children);
 return typeof node==='string'||typeof node==='number'?String(node):'';
}
const school:School={id:'school-a',name:'North School',description:'',core_resource_id:null,role:'school',effective_role:'school',capabilities:{manage_school:true,manage_members:true,manage_agents:true,manage_resources:true,create_course:true,approve_changes:true,manage_connections:true}};
const admin:User={id:'human-a',name:'Administrator',role:'school',principal_kind:'human'};
const teacher=():TeacherProfile=>({id:'profile-a',school_id:school.id,kind:'ai',name:'River',title:'Science teacher',pronouns:'',biography:'Explore evidence.',contact_email:null,expertise:['Science'],picture:null,ai_behavior:blankBehavior(),principal_id:null,agent_placement_id:null,status:'ready',version:3,course_ids:['class-a'],assigned_courses:[{id:'class-a',title:'Earth science',curriculum:null,corpus:null,has_teaching_authority:false}],can_manage:true,authority_granted:false,approved_revision_id:null,approved_version:null,approval_current:false,activation:{identity_linked:false,identity_eligible:false,agent_admission_enabled:false,live_models_enabled:false,authority_granted:false,runtime_active:false}});
function detail(initial=teacher(),user=admin){
 let profile=initial;
 const changed=vi.fn(async(value:TeacherProfile)=>{profile=value;});
 function render(){
  let tree:ReactNode;
  do{hooks.dirty=false;hooks.index=0;hooks.effectIndex=0;tree=TeacherDetail({profile,school,user,edit:()=>{},openCourse:()=>{},changed});const pending=hooks.pending.splice(0);pending.forEach(effect=>effect());}while(hooks.dirty);
  return tree!;
 }
 const find=(kind:string,label='')=>elements(render()).find(node=>node.type===kind&&text(node).includes(label));
 return {render,changed,find,replace:(value:TeacherProfile)=>{profile=value;},review:()=>{find('input')!.props.onChange!({target:{checked:true}});render();},button:()=>find('button','Approve current profile')!,submit:()=>find('form')!.props.onSubmit!({preventDefault:vi.fn()})};
}
afterEach(()=>{hooks.states=[];hooks.dependencies=[];hooks.pending=[];hooks.index=0;hooks.effectIndex=0;hooks.dirty=false;vi.unstubAllGlobals();setCsrf('');});

describe('teacher profile approval interactions',()=>{
 it('approves an exact reviewed profile while its assigned corpus is not prepared, without authorizing teaching',async()=>{
  let finish!:(value:Response)=>void;
  const fetcher=vi.fn(()=>new Promise<Response>(resolve=>{finish=resolve;}));vi.stubGlobal('fetch',fetcher);setCsrf('synthetic');
  const view=detail();
  expect(view.button().props.disabled).toBe(true);
  await view.submit();expect(fetcher).not.toHaveBeenCalled();
  expect(text(view.render())).toContain('Assigned class teaching materials need separate approval before AI Lesson Q&A can use them.');
  view.review();expect(view.button().props.disabled).toBe(false);expect(fetcher).not.toHaveBeenCalled();
  const approval=view.submit();
  expect(view.find('button','Approving…')!.props.disabled).toBe(true);
  await view.submit();expect(fetcher).toHaveBeenCalledOnce();
  const [path,options]=fetcher.mock.calls[0] as unknown as [string,RequestInit];
  expect(path).toBe('/api/schools/school-a/teachers/profile-a/approve');expect(JSON.parse(String(options.body))).toEqual({expected_version:3});
  expect((options.headers as Headers).get('X-CSRF-Token')).toBe('synthetic');
  const approved={...teacher(),approved_revision_id:'review-a',approved_version:3,approval_current:true};finish(new Response(JSON.stringify(approved)));
  await approval;expect(view.changed).toHaveBeenCalledExactlyOnceWith(approved);expect(view.find('form')).toBeUndefined();
  expect(text(view.render())).toContain('Approval of a profile does not activate an agent or approve class teaching materials.');
 });
 it('retains a visible server conflict and allows an explicit new attempt',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Teacher profile changed; reload before approving'}),{status:409}));vi.stubGlobal('fetch',fetcher);
  const view=detail();view.review();await view.submit();
  expect((elements(view.render()).find(node=>node.type===Notice)?.props as {error?:string}).error).toBe('Teacher profile changed; reload before approving');expect(view.button().props.disabled).toBe(false);expect(fetcher).toHaveBeenCalledOnce();expect(view.changed).not.toHaveBeenCalled();
 });
 it('requires a new human review after switching to a different profile with the same version',()=>{
  const view=detail();view.review();expect(view.button().props.disabled).toBe(false);
  view.replace({...teacher(),id:'profile-b',name:'Sky'});expect(view.button().props.disabled).toBe(true);
 });
 it('requires review again after the profile version, class context or approval state changes',()=>{
  const view=detail();
  for(const next of [{...teacher(),version:4},{...teacher(),assigned_courses:[{...teacher().assigned_courses[0],id:'class-b'}]},{...teacher(),approval_current:true}]){
   view.review();expect(view.button().props.disabled).toBe(false);view.replace(next);expect(view.button().props.disabled).toBe(true);
  }
 });
 it('hides profile approval for an archived profile, non-manager or agent',()=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
  for(const [profile,user] of [[{...teacher(),status:'archived' as const},admin],[{...teacher(),can_manage:false},admin],[teacher(),{...admin,principal_kind:'agent' as const}]] as const){expect(detail(profile,user).find('form')).toBeUndefined();}
  expect(fetcher).not.toHaveBeenCalled();
 });
});
