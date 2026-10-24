import { afterEach, describe, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CourseAssignedTeachers, CourseEditor, CourseTeacherProfiles } from './Curriculum';
import { Notice } from './components';
import { setCsrf, type Course, type School } from './api';
import { blankBehavior, type TeacherProfile } from './teacher-api';

const hooks=vi.hoisted(()=>({states:[] as unknown[],dependencies:[] as (readonly unknown[]|undefined)[],index:0,effectIndex:0,pending:[] as (()=>void)[],dirty:false}));
vi.mock('react',async importOriginal=>({...await importOriginal<typeof import('react')>(),useState:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push(typeof initial==='function'?initial():initial);return [hooks.states[index],(value:unknown)=>{const next=typeof value==='function'?value(hooks.states[index]):value;if(!Object.is(next,hooks.states[index])){hooks.states[index]=next;hooks.dirty=true;}}];},useEffect:(effect:()=>void,dependencies?:readonly unknown[])=>{const index=hooks.effectIndex++,previous=hooks.dependencies[index];if(!previous||!dependencies||previous.length!==dependencies.length||dependencies.some((value,i)=>!Object.is(value,previous[i]))){hooks.dependencies[index]=dependencies;hooks.pending.push(effect);}}}));

type Element=ReactElement<{children?:ReactNode;disabled?:boolean;checked?:boolean;value?:unknown;error?:string;onChange?:(event:{target:{checked?:boolean;value?:string}})=>void;onSubmit?:(event:{preventDefault:()=>void})=>Promise<void>}>;
function nodes(node:ReactNode):Element[]{if(Array.isArray(node))return node.flatMap(nodes);if(!isValidElement<{children?:ReactNode}>(node))return [];return [node as Element,...nodes(node.props.children)];}
function text(node:ReactNode):string{if(Array.isArray(node))return node.map(text).join('');if(isValidElement<{children?:ReactNode}>(node))return text(node.props.children);return typeof node==='string'||typeof node==='number'?String(node):'';}
const school:School={id:'school-a',name:'North School',description:'',core_resource_id:null,role:'admin',effective_role:'admin',capabilities:{manage_school:true,manage_members:true,manage_agents:true,manage_resources:true,create_course:true,approve_changes:true,manage_connections:true}};
const teacher=(id='ai-river',kind:'human'|'ai'='ai'):TeacherProfile=>({id,school_id:school.id,kind,name:kind==='ai'?'River':'Morgan',title:'Mathematics',pronouns:'',biography:'KEEP_BIOGRAPHY',contact_email:null,expertise:['Mathematics'],picture:null,ai_behavior:kind==='ai'?blankBehavior():null,principal_id:null,agent_placement_id:null,status:'draft',version:3,course_ids:['grade-three-math'],assigned_courses:[],can_manage:true,authority_granted:false,approval_current:false,activation:{identity_linked:false,identity_eligible:false,agent_admission_enabled:false,live_models_enabled:false,authority_granted:false,runtime_active:false}});
const course=():Course=>({id:'grade-three-math',school_id:school.id,teacher_id:'human-admin',teacher_name:'Administrator',teacher_principal_kind:'human',title:'Grade 3 mathematics',description:'',teacher_profile_ids:['ai-river'],teachers:[]});
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
function view(initial:Course|null=course(),selectedSchool=school){
 const saved=vi.fn(async()=>{});function render(){let tree:ReactNode;do{hooks.dirty=false;hooks.index=0;hooks.effectIndex=0;tree=CourseEditor({school:selectedSchool,initial:initial||undefined,close:()=>{},saved});hooks.pending.splice(0).forEach(effect=>effect());}while(hooks.dirty);return tree!;}
 function profiles(){const element=nodes(render()).find(node=>node.type===CourseTeacherProfiles)!;return CourseTeacherProfiles(element.props as Parameters<typeof CourseTeacherProfiles>[0]);}
 return {render,saved,profiles,submit:()=>nodes(render()).find(node=>node.type==='form')!.props.onSubmit!({preventDefault:vi.fn()}),selectProfile:(name:string,checked:boolean)=>{const row=nodes(profiles()).find(node=>node.type==='label'&&text(node).includes(name))!;nodes(row).find(node=>node.type==='input')!.props.onChange!({target:{checked}});},owner:()=>nodes(render()).find(node=>node.type==='select')!};
}
function mockApi(profiles:TeacherProfile[],failure=false){const fetcher=vi.fn(async(path:string,options?:RequestInit)=>{if(options?.method&&options.method!=='GET')return new Response(JSON.stringify(course()));if(path.startsWith('/api/users?'))return new Response('[]');if(path==='/api/schools/school-a/teachers')return new Response(JSON.stringify(failure?{detail:'School teacher list unavailable'}:profiles),{status:failure?503:200});throw new Error(`Unexpected synthetic URL ${path}`);});vi.stubGlobal('fetch',fetcher);return fetcher;}
afterEach(()=>{hooks.states=[];hooks.dependencies=[];hooks.pending=[];hooks.index=0;hooks.effectIndex=0;hooks.dirty=false;vi.unstubAllGlobals();setCsrf('');});

describe('course teacher profile selection',()=>{
 it('shows assigned AI teachers without identity, approval or corpus readiness and keeps staff responsibility separate',async()=>{
  const fetcher=mockApi([teacher(),teacher('human-morgan','human'),{...teacher('archived'),status:'archived'}]);const ui=view();ui.render();await settle();const fields=ui.profiles(),visible=text(fields);
  for(const label of ['Class teachers','River','Managed AI teacher','Morgan','Human teacher','Profiles awaiting approval or teaching materials can still be assigned'])expect(visible).toContain(label);
  expect(visible).not.toContain('Previously assigned teacher');expect(nodes(fields).filter(node=>node.type==='input'&&node.props.checked)).toHaveLength(1);expect(nodes(fields).filter(node=>node.type==='input')).toHaveLength(2);
  expect(text(ui.render())).toContain('Class administration');expect(text(ui.render())).toContain('Responsible staff member');expect(ui.owner().props.value).toBe('human-admin');expect(fetcher).toHaveBeenCalledTimes(2);
 });
 it('saves a selected AI profile separately from the real course owner and retains existing profile selections',async()=>{
  const profiles=[teacher(),teacher('human-morgan','human')],before=JSON.stringify(profiles),fetcher=mockApi(profiles);setCsrf('synthetic');const ui=view();ui.render();await settle();ui.selectProfile('Morgan',true);await ui.submit();
  const [path,options]=fetcher.mock.calls.find(([,options])=>options?.method==='PUT')!;expect(path).toBe('/api/courses/grade-three-math?school_id=school-a');const payload=JSON.parse(String(options!.body));expect(payload.teacher_id).toBe('human-admin');expect(payload.teacher_profile_ids).toEqual(['ai-river','human-morgan']);expect(payload.expected_teacher_profile_ids).toEqual(['ai-river']);expect((options!.headers as Headers).get('X-CSRF-Token')).toBe('synthetic');expect(JSON.stringify(profiles)).toBe(before);for(const value of ['KEEP_BIOGRAPHY','ai_behavior','expected_version','approved_revision_id'])expect(String(options!.body)).not.toContain(value);expect(ui.saved).toHaveBeenCalledOnce();
 });
 it('sends an intentional empty profile selection only after removing the current teacher',async()=>{
  const fetcher=mockApi([teacher()]);const ui=view();ui.render();await settle();ui.selectProfile('River',false);await ui.submit();const options=fetcher.mock.calls.find(([,options])=>options?.method==='PUT')![1]!;expect(JSON.parse(String(options.body)).teacher_profile_ids).toEqual([]);expect(JSON.parse(String(options.body)).teacher_id).toBe('human-admin');
 });
 it('preserves existing assignments on an unrelated save while profile loading fails',async()=>{
  const fetcher=mockApi([],true);const ui=view();ui.render();await settle();expect(text(ui.profiles())).toContain('Existing teacher assignments will be preserved');await ui.submit();const options=fetcher.mock.calls.find(([,options])=>options?.method==='PUT')![1]!;expect(JSON.parse(String(options.body))).not.toHaveProperty('teacher_profile_ids');expect(ui.saved).toHaveBeenCalledOnce();
 });
 it('omits untouched assignment lists and blocks saving an explicitly selected unavailable profile',async()=>{
  const fetcher=mockApi([teacher('human-morgan','human')]);const ui=view();ui.render();await settle();await ui.submit();expect(JSON.parse(String(fetcher.mock.calls.find(([,options])=>options?.method==='PUT')![1]!.body))).not.toHaveProperty('teacher_profile_ids');ui.selectProfile('Morgan',true);await ui.submit();expect(nodes(ui.render()).find(node=>node.type===Notice)?.props.error).toContain('no longer available');expect(fetcher.mock.calls.filter(([,options])=>options?.method==='PUT')).toHaveLength(1);
 });
 it('retains a visible stale-team conflict without reporting the course saved or retrying',async()=>{
  const fetcher=mockApi([teacher(),teacher('human-morgan','human')]);const ui=view();ui.render();await settle();ui.selectProfile('Morgan',true);fetcher.mockImplementationOnce(async()=>new Response(JSON.stringify({detail:'Teaching team changed; reload the course before saving'}),{status:409}));await ui.submit();expect(nodes(ui.render()).find(node=>node.type===Notice)?.props.error).toBe('Teaching team changed; reload the course before saving');expect(ui.saved).not.toHaveBeenCalled();expect(fetcher.mock.calls.filter(([,options])=>options?.method==='PUT')).toHaveLength(1);expect(nodes(ui.profiles()).filter(node=>node.type==='input'&&node.props.checked)).toHaveLength(2);
 });
 it('assigns a managed AI profile during course creation without making it the course principal',async()=>{
  const fetcher=mockApi([teacher()]);const ui=view(null);ui.render();await settle();ui.selectProfile('River',true);await ui.submit();const [path,options]=fetcher.mock.calls.find(([,options])=>options?.method==='POST')!;expect(path).toBe('/api/courses?school_id=school-a');const payload=JSON.parse(String(options!.body));expect(payload.teacher_profile_ids).toEqual(['ai-river']);expect(payload).not.toHaveProperty('teacher_id');expect(payload).not.toHaveProperty('expected_teacher_profile_ids');
 });
 it('keeps teacher-profile administration out of ordinary teacher course edits',async()=>{
  const fetcher=mockApi([teacher()]);const ui=view(course(),{...school,effective_role:'teacher',role:'teacher',capabilities:{...school.capabilities,manage_school:false,manage_members:false}});expect(nodes(ui.render()).some(node=>node.type===CourseTeacherProfiles)).toBe(false);await settle();await ui.submit();expect(fetcher).toHaveBeenCalledOnce();expect(JSON.parse(String(fetcher.mock.calls[0][1]!.body))).not.toHaveProperty('teacher_profile_ids');expect(JSON.parse(String(fetcher.mock.calls[0][1]!.body))).not.toHaveProperty('teacher_id');
 });
 it('shows the assigned AI teacher in the course summary while approval and teaching setup remain pending',()=>{
  const html=renderToStaticMarkup(<CourseAssignedTeachers profiles={[{id:'ai-river',kind:'ai',name:'River',status:'draft',approval_current:false,context_ready:false}]}/>);for(const expected of ['Class teachers','River','AI teacher','Profile needs approval','Class teaching setup incomplete'])expect(html).toContain(expected);expect(html).not.toContain('Available for Lesson Q&A');
 });
 it('renders ordinary profile metadata escaped without disabling teachers who are awaiting approval',()=>{
  const html=renderToStaticMarkup(CourseTeacherProfiles({profiles:[{...teacher(),name:'River <script>alert(1)</script>'}],selected:['ai-river'],loading:false,error:'',change:()=>{}}));expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');expect(html).not.toContain('disabled');expect(html).toContain('checked');
 });
});
