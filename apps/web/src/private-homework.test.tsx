import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { Notice, Spinner } from './components';
import { HomeworkAI, HomeworkAIMessageView } from './HomeworkAI';
import { HomeworkPrivateSettings } from './HomeworkAISettings';
import { prepareAssignmentImage, type CuratedImage } from './curated-api';
import type { HomeworkAIIndex, HomeworkAIMessage } from './homework-ai-api';

const hooks = vi.hoisted(() => ({
 values: [] as unknown[],
 dependencies: [] as (readonly unknown[] | undefined)[],
 effects: [] as (() => void)[],
 valueIndex: 0,
 effectIndex: 0,
}));

vi.mock('react', async importOriginal => ({
 ...await importOriginal<typeof import('react')>(),
 useState(initial: unknown) {
  const index=hooks.valueIndex++;
  if(index>=hooks.values.length)hooks.values.push(typeof initial==='function'?initial():initial);
  return [hooks.values[index], (value: unknown) => {
   hooks.values[index]=typeof value==='function'?value(hooks.values[index]):value;
  }];
 },
 useRef(initial: unknown) {
  const index=hooks.valueIndex++;
  if(index>=hooks.values.length)hooks.values.push({current:initial});
  return hooks.values[index];
 },
 useCallback(callback: unknown, dependencies: readonly unknown[]) {
  const index=hooks.valueIndex++;
  const previous=hooks.values[index] as {callback:unknown;dependencies:readonly unknown[]} | undefined;
  if(previous?.dependencies.every((value,i)=>Object.is(value,dependencies[i])))return previous.callback;
  hooks.values[index]={callback,dependencies};
  return callback;
 },
 useEffect(effect: () => void | (() => void), dependencies?: readonly unknown[]) {
  const index=hooks.effectIndex++, previous=hooks.dependencies[index];
  if(previous&&dependencies&&previous.every((value,i)=>Object.is(value,dependencies[i])))return;
  hooks.dependencies[index]=dependencies;
  hooks.effects.push(()=>{effect();});
 },
}));

vi.mock('./curated-api', async importOriginal => ({
 ...await importOriginal<typeof import('./curated-api')>(),
 prepareAssignmentImage:vi.fn(),
}));

type Node = ReactElement<{
 children?:ReactNode; disabled?:boolean; type?:string; checked?:boolean; value?:string;
 onClick?:()=>void; onChange?:(event:{target:Record<string,unknown>})=>void;
 onSubmit?:(event:{preventDefault:()=>void})=>Promise<void>; 'aria-label'?:string;
}>;
const expanded=new Set([HomeworkAIMessageView,Notice,Spinner]);
function children(node:Node):ReactNode {
 return expanded.has(node.type as typeof Notice)
  ? (node.type as (props:unknown)=>ReactNode)(node.props)
  : node.props.children;
}
function nodes(value:ReactNode):Node[] {
 if(Array.isArray(value))return value.flatMap(nodes);
 if(!isValidElement(value))return [];
 const node=value as Node;
 return [node,...nodes(children(node))];
}
function visible(value:ReactNode):string {
 if(Array.isArray(value))return value.map(visible).join('');
 if(isValidElement(value))return visible(children(value as Node));
 return typeof value==='string'||typeof value==='number'?String(value):'';
}
function view(component:()=>ReactNode){
 function render(){
  hooks.valueIndex=0;
  hooks.effectIndex=0;
  const tree=component();
  hooks.effects.splice(0).forEach(effect=>effect());
  return tree;
 }
 return {
  text:()=>visible(render()),
  find:(type:string, predicate:(node:Node)=>boolean=()=>true)=>{
   const node=nodes(render()).find(node=>node.type===type&&predicate(node));
   if(!node)throw new Error(`Could not find ${type}`);
   return node;
  },
 };
}
const byText=(text:string)=>(node:Node)=>visible(node.props.children).includes(text);
const change=(node:Node,target:Record<string,unknown>)=>node.props.onChange!({target});
const submit=(node:Node)=>node.props.onSubmit!({preventDefault:()=>{}});
const settle=()=>new Promise<void>(resolve=>setTimeout(resolve,0));
const json=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status});

beforeEach(()=>{
 hooks.values.length=0;
 hooks.dependencies.length=0;
 hooks.effects.length=0;
 vi.resetAllMocks();
});
afterEach(()=>vi.unstubAllGlobals());

it('saves the separate school private policy and reloads a stale revision before another save',async()=>{
 const path='/api/homework-help/private-policy?school_id=school%20a';
 let policy={enabled:false,version:0};
 let conflict=false;
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  expect(url).toBe(path);
  if(options.method==='GET')return json(policy);
  expect(options.method).toBe('PUT');
  const body=JSON.parse(String(options.body));
  if(conflict){
   policy={enabled:true,version:2};
   return json({detail:'Version conflict'},409);
  }
  expect(body).toEqual({enabled:!policy.enabled,expected_version:policy.version});
  policy={enabled:body.enabled,version:policy.version+1};
  return json(policy);
 });
 vi.stubGlobal('fetch',fetcher);
 const ui=view(()=>HomeworkPrivateSettings({schoolId:'school a'}));
 expect(ui.text()).toContain('Loading Private Homework Help settings');
 await settle();
 expect(ui.text()).toContain('Saved status · Disabled');
 expect(ui.text()).toContain('separate live AI configuration, including any OpenAI draft');
 expect(ui.find('button',byText('Save private setting')).props.disabled).toBe(true);
 change(ui.find('input'),{checked:true});
 expect(fetcher).toHaveBeenCalledTimes(1);
 await submit(ui.find('form'));
 expect(ui.text()).toContain('Saved status · Enabled');
 conflict=true;
 change(ui.find('input'),{checked:false});
 await submit(ui.find('form'));
 expect(ui.text()).toContain('Someone changed this setting');
 ui.find('button',byText('Reload private setting')).props.onClick!();
 await settle();
 expect(ui.text()).toContain('Saved status · Enabled');
 conflict=false;
 change(ui.find('input'),{checked:false});
 await submit(ui.find('form'));
 expect(ui.text()).toContain('Saved status · Disabled');
 expect(fetcher.mock.calls.map(([,options])=>options.method)).toEqual(['GET','PUT','PUT','GET','PUT']);
});

it('requires private photo confirmation and displays the recorded unreadable-photo follow-up in history',async()=>{
 const image:CuratedImage={name:'worksheet.jpg',data_url:'data:image/jpeg;base64,AAE=',sha256:'a'.repeat(64),width:320,height:240};
 vi.mocked(prepareAssignmentImage).mockResolvedValue(image);
 const assistant:HomeworkAIIndex['assistant']={
  school_id:'school',version:1,revision_id:null,approved_revision_id:null,approved:true,
  available:true,live_models_enabled:false,configuration_sha256:null,approval_required:true,private_mode:true,
  configuration:{enabled:true,name:'Private study coach',biography:'',allow_images:true,max_input_chars:1500,max_output_tokens:0},
 };
 const session={id:'session',created_at:'2026-11-10'};
 const clarification='I could not read enough of the photo. Please crop it to one question or type the problem.';
 const reply:HomeworkAIMessage={
  id:'reply',message:'',created_at:'2026-11-10',idempotency_key:'key',private_mode:true,status:'clarify',
  error:null,answer:clarification,triage:{method:'private_coach',course_id:null,course_name:null,prompt:null,candidates:[]},
  citations:[],references:[],images:[{...image,id:'retained-photo'}],usage:{},resume_available:false,
 };
 const index={assistant,courses:[],sessions:[]};
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  if(url==='/api/homework-help/ai/sessions'&&options.method==='POST')return json(session);
  if(url==='/api/homework-help/ai/sessions/session/messages'&&options.method==='POST'){
   expect(JSON.parse(String(options.body))).toMatchObject({
    message:'',course_id:null,images:[{name:image.name,data_url:image.data_url}],image_use_confirmed:true,
   });
   return json(reply);
  }
  if(url==='/api/homework-help/ai?school_id=school')return json({...index,sessions:[session]});
  if(url==='/api/homework-help/ai/sessions/session')return json({session,messages:[reply]});
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);
 vi.stubGlobal('crypto',{randomUUID:vi.fn().mockReturnValueOnce('create-key').mockReturnValueOnce('question-key')});
 const ui=view(()=>HomeworkAI({schoolId:'school',userId:'local-student',index,openCourse:()=>{}}));
 expect(ui.text()).toContain('Private Homework Help · Academy study coach');
 expect(ui.find('textarea').props.value).toBe('');
 change(ui.find('input',node=>node.props.type==='file'),{files:[{name:'worksheet.png'}],value:'worksheet.png'});
 await settle();
 expect(ui.text()).toContain('Academy can read these photos inside the service and save them for authorized school review');
 expect(ui.text()).not.toContain('school-approved');
 expect(ui.find('button',byText('Send')).props.disabled).toBe(true);
 await submit(ui.find('form'));
 expect(fetcher).not.toHaveBeenCalled();
 change(ui.find('input',node=>node.props.type==='checkbox'),{checked:true});
 expect(ui.find('button',byText('Send')).props.disabled).toBe(false);
 await submit(ui.find('form'));
 expect(ui.text()).toContain(clarification);
 expect(ui.text()).toContain('Homework photos');
 expect(visible(ui.find('div',node=>node.props['aria-label']==='Study coach follow-up'))).toBe(clarification);
 expect(fetcher.mock.calls.map(([url])=>url)).toEqual([
  '/api/homework-help/ai/sessions',
  '/api/homework-help/ai/sessions/session/messages',
 ]);
 ui.find('button',byText('Refresh history')).props.onClick!();
 await settle();
 expect(ui.text()).toContain(clarification);
 expect(fetcher.mock.calls.map(([,options])=>options.method)).toEqual(['POST','POST','GET','GET']);
});

it('shows the retained coaching refusal without showing a blocked answer',()=>{
 const item:HomeworkAIMessage={
  id:'blocked',message:'Write my assignment for me.',created_at:'2026-11-10',idempotency_key:'blocked-key',
  private_mode:true,status:'blocked',error:'I can help you plan your own answer.',answer:'UNRELEASED_ANSWER',
  triage:{method:'private_coach',course_id:null,course_name:null,prompt:null,candidates:[]},
  citations:[],references:[],images:[],usage:{},resume_available:false,
 };
 const ui=view(()=>HomeworkAIMessageView({item,choose:()=>{},openCourse:()=>{}}));
 expect(ui.text()).toContain('I can help you plan your own answer.');
 expect(ui.text()).not.toContain('UNRELEASED_ANSWER');
});
