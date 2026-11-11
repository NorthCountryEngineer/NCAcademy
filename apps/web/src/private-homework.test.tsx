import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { Notice, Spinner } from './components';
import { HomeworkAI, HomeworkAIMessageView } from './HomeworkAI';
import { HomeworkPrivateSettings, HomeworkLiveAISettings } from './HomeworkAISettings';
import { prepareAssignmentImage, type CuratedImage } from './curated-api';
import { blankHomeworkAIPolicy, type HomeworkAIIndex, type HomeworkAIMessage, type HomeworkAIConfiguration } from './homework-ai-api';

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

it('confirms provider use before carrying a locally retained worksheet into a vision follow-up',async()=>{
 const image={id:'photo',name:'Homework picture 1',sha256:'c'.repeat(64),width:320,height:240,media_type:'image/jpeg'};
 const source:HomeworkAIMessage={
  id:'local-photo-message',message:'Help with this worksheet',created_at:'2026-11-11',idempotency_key:'old-key',
  private_mode:true,status:'released',error:null,answer:'A prior local hint',
  triage:{method:'private_coach',course_id:null,course_name:null,prompt:null,candidates:[]},
  citations:[],references:[],images:[image],usage:{},resume_available:false,
 };
 const assistant:HomeworkAIIndex['assistant']={
  school_id:'school',version:2,revision_id:'revision',approved_revision_id:'revision',approved:true,
  available:true,live_models_enabled:true,configuration_sha256:'approved-sha',image_policy_sha256:'d'.repeat(64),approval_required:false,
  configuration:{enabled:true,name:'Vision tutor',biography:'',allow_images:true,max_input_chars:1500,max_output_tokens:500},
  photo_model_labels:{triage:'Approved vision',tutor:'Approved vision',safety:'Approved review'},
 };
 const session={id:'session',created_at:'2026-11-11'};
 const reply:HomeworkAIMessage={...source,id:'vision-reply',private_mode:false,message:'Question 7. Look at the sheet.',answer:'What did you try for question 7?'};
 const index={assistant,courses:[],sessions:[session]};
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  if(url==='/api/homework-help/ai/sessions/session')return json({session,messages:[source]});
  if(url==='/api/homework-help/ai/sessions/session/messages'){
   expect(options.method).toBe('POST');
   expect(JSON.parse(String(options.body))).toMatchObject({
    message:'Question 7. Look at the sheet.',images:[],previous_image_message_id:source.id,image_use_confirmed:true,
    expected_image_policy_sha256:'d'.repeat(64),
   });
   return json(reply);
  }
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);
 vi.stubGlobal('crypto',{randomUUID:()=> 'new-question'});
 const ui=view(()=>HomeworkAI({schoolId:'school',userId:'local-student',index,openCourse:()=>{}}));
 ui.text();
 await settle();
 expect(ui.text()).toContain('Use the earlier worksheet for this question (1 photo)');
 expect(ui.text()).toContain('Approved vision');
 change(ui.find('textarea'),{value:'Question 7. Look at the sheet.'});
 expect(ui.find('button',byText('Send')).props.disabled).toBe(true);
 await submit(ui.find('form'));
 expect(fetcher).toHaveBeenCalledTimes(1);
 change(ui.find('input',node=>node.props.type==='checkbox'&&node.props.checked===false),{checked:true});
 expect(ui.find('button',byText('Send')).props.disabled).toBe(false);
 await submit(ui.find('form'));
 expect(ui.text()).toContain('What did you try for question 7?');
 expect(ui.find('button',byText('Send')).props.disabled).toBe(true);
 expect(fetcher).toHaveBeenCalledTimes(2);
});

function retainedVisionConversation(){
 const source:HomeworkAIMessage={
  id:'local-photo-message',message:'Help with this worksheet',created_at:'2026-11-11',idempotency_key:'old-key',
  private_mode:true,status:'released',error:null,answer:'A prior local hint',
  triage:{method:'private_coach',course_id:null,course_name:null,prompt:null,candidates:[]},
  citations:[],references:[],images:[{id:'photo',name:'Homework picture 1',sha256:'c'.repeat(64),width:320,height:240}],
  usage:{},resume_available:false,
 };
 const assistant:HomeworkAIIndex['assistant']={
  school_id:'school',version:2,revision_id:'revision',approved_revision_id:'revision',approved:true,
  available:true,live_models_enabled:true,configuration_sha256:'approved-sha',image_policy_sha256:'d'.repeat(64),approval_required:false,
  configuration:{enabled:true,name:'Vision tutor',biography:'',allow_images:true,max_input_chars:1500,max_output_tokens:500},
  photo_model_labels:{triage:'Approved vision',tutor:'Approved vision',safety:'Approved review'},
 };
 const session={id:'session',created_at:'2026-11-11'};
 const index:HomeworkAIIndex={assistant,courses:[],sessions:[session]};
 const stored=new Map<string,string>();
 vi.stubGlobal('window',{sessionStorage:{
  getItem:(key:string)=>stored.get(key)||null,
  setItem:(key:string,value:string)=>stored.set(key,value),
  removeItem:(key:string)=>stored.delete(key),
 }});
 vi.stubGlobal('crypto',{randomUUID:vi.fn().mockReturnValueOnce('first-question').mockReturnValueOnce('confirmed-question')});
 return {source,session,index,stored};
}

it('recovers a rejected photo-policy preflight only after refreshing destinations and explicit reconfirmation',async()=>{
 const {source,session,index,stored}=retainedVisionConversation();
 const destinationConflict='Homework photo destinations changed. Reload the assistant and confirm photo use again.';
 const updated:HomeworkAIIndex={...index,assistant:{...index.assistant,image_policy_sha256:'e'.repeat(64),
  photo_model_labels:{triage:'Updated vision',tutor:'Updated vision',safety:'Updated review'}}};
 const questions:Record<string,unknown>[]=[];
 const reply:HomeworkAIMessage={...source,id:'vision-reply',private_mode:false,message:'Question 7 is confusing.',answer:'Which part of question 7 did you try?'};
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  if(url==='/api/homework-help/ai/sessions/session'&&options.method==='GET')return json({session,messages:[source]});
  if(url==='/api/homework-help/ai?school_id=school'&&options.method==='GET')return json(updated);
  if(url==='/api/homework-help/ai/sessions/session/messages'&&options.method==='POST'){
   questions.push(JSON.parse(String(options.body)));
   return questions.length===1?json({detail:destinationConflict},409):json(reply);
  }
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);
 const ui=view(()=>HomeworkAI({schoolId:'school',userId:'local-student',index,openCourse:()=>{}}));
 ui.text();await settle();
 change(ui.find('textarea'),{value:'Question 7 is confusing.'});
 change(ui.find('input',node=>node.props.type==='checkbox'&&node.props.checked===false),{checked:true});
 await submit(ui.find('form'));
 expect(questions).toEqual([expect.objectContaining({
  message:'Question 7 is confusing.',images:[],previous_image_message_id:source.id,
  image_use_confirmed:true,expected_image_policy_sha256:'d'.repeat(64),idempotency_key:'first-question',
 })]);
 expect(ui.text()).toContain(destinationConflict);
 expect(ui.text()).toContain('Updated vision');
 expect(ui.text()).not.toContain('Your request is recorded or awaiting confirmation.');
 expect(ui.find('textarea').props.value).toBe('Question 7 is confusing.');
 expect(ui.find('button',byText('New conversation')).props.disabled).toBe(false);
 expect(ui.find('fieldset').props.disabled).toBe(false);
 expect(ui.find('button',byText('Send')).props.disabled).toBe(true);
 expect(stored.size).toBe(0);
 expect(fetcher.mock.calls.map(([url,options])=>[options.method,url])).toEqual([
  ['GET','/api/homework-help/ai/sessions/session'],
  ['POST','/api/homework-help/ai/sessions/session/messages'],
  ['GET','/api/homework-help/ai?school_id=school'],
 ]);
 await submit(ui.find('form'));
 expect(questions).toHaveLength(1);
 change(ui.find('input',node=>node.props.type==='checkbox'&&node.props.checked===false),{checked:true});
 expect(ui.find('button',byText('Send')).props.disabled).toBe(false);
 await submit(ui.find('form'));
 expect(questions).toHaveLength(2);
 expect(questions[1]).toMatchObject({message:'Question 7 is confusing.',previous_image_message_id:source.id,
  image_use_confirmed:true,expected_image_policy_sha256:'e'.repeat(64),idempotency_key:'confirmed-question'});
 expect(ui.text()).toContain(reply.answer);
 expect(stored.size).toBe(0);
});

it.each(['unrecognized conflict','transport failure'] as const)('keeps a photo question locked after %s without refresh or retry',async failure=>{
 const {source,session,index,stored}=retainedVisionConversation();
 let questions=0;
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  if(url==='/api/homework-help/ai/sessions/session'&&options.method==='GET')return json({session,messages:[source]});
  if(url==='/api/homework-help/ai/sessions/session/messages'&&options.method==='POST'){
   questions++;
   if(failure==='transport failure')throw new TypeError('Network lost after send');
   return json({detail:'An unrecognized conflict requires reconciliation'},409);
  }
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);
 const ui=view(()=>HomeworkAI({schoolId:'school',userId:'local-student',index,openCourse:()=>{}}));
 ui.text();await settle();
 change(ui.find('textarea'),{value:'Question 7 is confusing.'});
 change(ui.find('input',node=>node.props.type==='checkbox'&&node.props.checked===false),{checked:true});
 await submit(ui.find('form'));
 expect(ui.text()).toContain('Your request is recorded or awaiting confirmation.');
 expect(ui.find('textarea').props.value).toBe('Question 7 is confusing.');
 expect(ui.find('button',byText('New conversation')).props.disabled).toBe(true);
 expect(ui.find('fieldset').props.disabled).toBe(true);
 expect(JSON.parse(stored.get('ncacademy:homework-ai:school:local-student')!)).toEqual({
  kind:'message',key:'first-question',session_id:'session',
 });
 await settle();
 await submit(ui.find('form'));
 expect(questions).toBe(1);
 expect(fetcher.mock.calls.map(([url,options])=>[options.method,url])).toEqual([
  ['GET','/api/homework-help/ai/sessions/session'],
  ['POST','/api/homework-help/ai/sessions/session/messages'],
 ]);
});

it('keeps a local homework upload and draft through clarification, then clears them only after a supported learning reply',async()=>{
 const image:CuratedImage={name:'worksheet.jpg',data_url:'data:image/jpeg;base64,AAE=',sha256:'f'.repeat(64),width:320,height:240};
 vi.mocked(prepareAssignmentImage).mockResolvedValue(image);
 const assistant:HomeworkAIIndex['assistant']={school_id:'school',version:1,revision_id:'revision',approved_revision_id:'revision',approved:true,available:true,live_models_enabled:true,configuration_sha256:'approved',image_policy_sha256:'a'.repeat(64),approval_required:false,privacy_mode:'deidentified',local_only_images:true,photo_model_labels:null,configuration:{enabled:true,name:'School helper',biography:'',allow_images:true,max_input_chars:1500,max_output_tokens:500,privacy_mode:'deidentified'}};
 const session={id:'session',created_at:'2026-11-11'};
 const clarification:HomeworkAIMessage={id:'clarification',message:'Help with this worksheet.',created_at:'2026-11-11',idempotency_key:'first-question',status:'clarify',error:null,answer:'Please type an equation or topic so I can help.',privacy_mode:'deidentified',deidentification:{status:'clarify',reason:'unsupported_task',task_summary:null,image_read:true,action:'hint',stage:0,local_only_images:true},triage:{method:'local_task',course_id:null,course_name:null,prompt:null,candidates:[]},citations:[],references:[],images:[{...image,id:'photo'}],usage:{},resume_available:false};
 const ready:HomeworkAIMessage={...clarification,id:'ready',message:'24 ÷ 4',idempotency_key:'second-question',status:'released',answer:'Draw four equal groups and think about sharing fairly.',deidentification:{...clarification.deidentification!,status:'ready',reason:'recognized',task_summary:'Equal sharing'}};
 const posted:Record<string,unknown>[]=[];
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  if(url==='/api/homework-help/ai/sessions'&&options.method==='POST')return json(session);
  if(url==='/api/homework-help/ai/sessions/session/messages'&&options.method==='POST'){posted.push(JSON.parse(String(options.body)));return json(posted.length===1?clarification:ready);}
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);vi.stubGlobal('crypto',{randomUUID:vi.fn().mockReturnValueOnce('create-question').mockReturnValueOnce('first-question').mockReturnValueOnce('second-question')});
 const ui=view(()=>HomeworkAI({schoolId:'school',userId:'student',index:{assistant,courses:[],sessions:[]},openCourse:()=>{}}));
 change(ui.find('textarea'),{value:'Help with this worksheet.'});change(ui.find('input',node=>node.props.type==='file'),{files:[{name:'worksheet.png'}],value:'worksheet.png'});await settle();
 expect(ui.find('button',byText('Send')).props.disabled).toBe(true);change(ui.find('input',node=>node.props.type==='checkbox'),{checked:true});expect(ui.find('button',byText('Send')).props.disabled).toBe(false);await submit(ui.find('form'));
 expect(ui.find('textarea').props.value).toBe('Help with this worksheet.');expect(ui.text()).toContain('worksheet.jpg');expect(ui.text()).toContain('Please type an equation or topic');expect(ui.text()).toContain('Private question check');expect(posted).toHaveLength(1);
 change(ui.find('textarea'),{value:'24 ÷ 4'});await submit(ui.find('form'));expect(posted).toHaveLength(2);expect(posted[1]).toMatchObject({message:'24 ÷ 4',images:[{name:image.name,data_url:image.data_url}],image_use_confirmed:true});expect(ui.find('textarea').props.value).toBe('');expect(ui.text()).toContain(ready.answer);expect(ui.text()).not.toContain('named school-approved');
});

it('uses only the selected conversation’s local worksheet and keeps its draft on read-only recovery',async()=>{
 const {source,session,index,stored}=retainedVisionConversation();
 const other={id:'other-session',created_at:'2026-11-10',title:'Earlier worksheet'},otherSource:HomeworkAIMessage={...source,id:'other-photo-message',message:'Earlier private question.',images:[{...source.images[0],sha256:'e'.repeat(64)}]};
 const local:HomeworkAIIndex={...index,assistant:{...index.assistant,privacy_mode:'deidentified',local_only_images:true,configuration:{...index.assistant.configuration!,privacy_mode:'deidentified'},photo_model_labels:null},sessions:[session,other]};
 const clarification:HomeworkAIMessage={...otherSource,id:'clarified-recovery',idempotency_key:'first-question',message:'Help with question 7.',status:'clarify',answer:'Please type an equation or topic.',privacy_mode:'deidentified',deidentification:{status:'clarify',reason:'unsupported_task',task_summary:null,image_read:true,action:'hint',stage:0,local_only_images:true}};
 let sent=0,recover=false;
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  if(url==='/api/homework-help/ai/sessions/session')return json({session,messages:[source]});
  if(url==='/api/homework-help/ai/sessions/other-session')return json({session:other,messages:recover?[otherSource,clarification]:[otherSource]});
  if(url==='/api/homework-help/ai?school_id=school')return json(local);
  if(url==='/api/homework-help/ai/sessions/other-session/messages'&&options.method==='POST'){sent++;expect(JSON.parse(String(options.body))).toMatchObject({previous_image_message_id:'other-photo-message',images:[]});throw new TypeError('Synthetic lost response');}
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);const ui=view(()=>HomeworkAI({schoolId:'school',userId:'local-student',index:local,openCourse:()=>{}}));ui.text();await settle();
 ui.find('button',node=>visible(node).includes('Earlier')||visible(node).includes('2026-11-10')).props.onClick!();ui.text();await settle();
 change(ui.find('textarea'),{value:'Help with question 7.'});change(ui.find('input',node=>node.props.type==='checkbox'&&node.props.checked===false),{checked:true});await submit(ui.find('form'));expect(sent).toBe(1);expect(stored.size).toBe(1);expect(ui.find('fieldset').props.disabled).toBe(true);
 recover=true;ui.find('button',byText('Refresh history')).props.onClick!();await settle();expect(sent).toBe(1);expect(stored.size).toBe(0);expect(ui.find('textarea').props.value).toBe('Help with question 7.');expect(ui.find('fieldset').props.disabled).toBe(false);expect(ui.text()).toContain('Please type an equation or topic.');
});


it('saves private homework mode with existing image-capable models while reserving ZDR evidence for original-data mode',async()=>{
 const policy={...blankHomeworkAIPolicy(),enabled:true,allow_local_students:true,allow_images:true,triage_image_capable:true,tutor_image_capable:true,safety_image_capable:true,triage:{connection_id:'models',model:'luna'},tutor:{connection_id:'models',model:'luna'},safety:{connection_id:'models',model:'luna'}};
 const saved:HomeworkAIConfiguration={school_id:'school',version:1,revision_id:'revision',approved_revision_id:'revision',approved:true,available:true,live_models_enabled:true,configuration:policy,configuration_sha256:'a'.repeat(64),approval_required:true};
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  if(url==='/api/admin/connections?school_id=school')return json([{id:'models',name:'School model',endpoint:'https://api.openai.com/v1',models:['luna'],enabled:true}]);
  if(url==='/api/homework-help/configuration?school_id=school'){
   if(options.method==='GET')return json(saved);
   const body=JSON.parse(String(options.body));expect(options.method).toBe('PUT');expect(body).toMatchObject({privacy_mode:'deidentified',allow_local_students:true,allow_images:true,local_student_privacy_confirmation:'',expected_version:1});return json({...saved,version:2,revision_id:'new-revision',approved:false,configuration:body});
  }
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);const ui=view(()=>HomeworkLiveAISettings({schoolId:'school'}));ui.text();await settle();
 const privacy=()=>ui.find('select',node=>nodes(node.props.children).some(option=>visible(option).includes('Original questions and photos')));
 expect(privacy().props.value).toBe('deidentified');expect(ui.text()).not.toContain('Verified provider data controls and evidence reference');expect(ui.find('button',byText('Save configuration for review')).props.disabled).toBe(false);
 change(privacy(),{value:'raw_zdr'});expect(ui.text()).toContain('Verified provider data controls and evidence reference');expect(ui.text()).toContain('I have verified that the selected safety reviewer supports images');expect(ui.find('button',byText('Save configuration for review')).props.disabled).toBe(false);
 change(privacy(),{value:'deidentified'});expect(ui.text()).not.toContain('Verified provider data controls and evidence reference');expect(ui.find('button',byText('Save configuration for review')).props.disabled).toBe(false);
 await submit(ui.find('form'));expect(fetcher.mock.calls.filter(([,options])=>options.method==='PUT')).toHaveLength(1);expect(fetcher.mock.calls).toHaveLength(3);
});


it('lets an administrator enable and attest all photo models from a blank deidentified configuration',async()=>{
 const blank=blankHomeworkAIPolicy();expect(blank.privacy_mode).toBe('deidentified');expect([blank.triage_image_capable,blank.tutor_image_capable,blank.safety_image_capable]).toEqual([false,false,false]);
 const state:HomeworkAIConfiguration={school_id:'school',version:0,revision_id:null,approved_revision_id:null,approved:false,available:false,live_models_enabled:true,configuration:null,configuration_sha256:null,approval_required:true};
 let savedBody:Record<string,unknown>|null=null;
 const fetcher=vi.fn(async(url:string,options:RequestInit)=>{
  if(url==='/api/admin/connections?school_id=school')return json([{id:'models',name:'School model',endpoint:'https://api.openai.com/v1',models:['luna'],enabled:true}]);
  if(url==='/api/homework-help/configuration?school_id=school'){
   if(options.method==='GET')return json(state);
   expect(options.method).toBe('PUT');savedBody=JSON.parse(String(options.body));return json({...state,version:1,revision_id:'saved-revision',configuration:savedBody});
  }
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);const ui=view(()=>HomeworkLiveAISettings({schoolId:'school'}));ui.text();await settle();
 const control=(label:string,type:string)=>nodes(ui.find('label',byText(label)).props.children).find(node=>node.type===type)!;
 for(const label of ['Triage and general-help model','AI teacher response model','Response safety reviewer model'])change(control(label,'select'),{value:'models::luna'});
 change(control('Allow homework photographs to be read and retained privately','input'),{checked:true});
 expect(ui.text()).toContain('fresh generated math diagrams');expect(ui.text()).toContain('Original worksheet photos stay in Academy');
 const labels=['I have verified that the selected triage model supports images','I have verified that the selected tutor model supports images','I have verified that the selected safety reviewer supports images'];
 for(const label of labels)expect(control(label,'input').props.checked).toBe(false);
 expect(ui.find('button',byText('Save configuration for review')).props.disabled).toBe(true);await submit(ui.find('form'));expect(fetcher.mock.calls.filter(([,options])=>options.method==='PUT')).toHaveLength(0);
 for(const [index,label] of labels.entries()){change(control(label,'input'),{checked:true});expect(ui.find('button',byText('Save configuration for review')).props.disabled).toBe(index<labels.length-1);}
 await submit(ui.find('form'));expect(savedBody).toMatchObject({privacy_mode:'deidentified',allow_images:true,triage_image_capable:true,tutor_image_capable:true,safety_image_capable:true,expected_version:0});expect(fetcher.mock.calls.filter(([,options])=>options.method==='PUT')).toHaveLength(1);
});
