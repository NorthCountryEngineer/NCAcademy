import { afterEach, expect, it, vi } from 'vitest';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import App from './App';
import { request, setCsrf } from './api';
import { FamilyAccounts, PasswordSignIn } from './FamilyAccounts';
import { Notice } from './components';

const hooks=vi.hoisted(()=>({states:[] as unknown[],index:0,dirty:false,effects:[] as (()=>void)[]}));
function changed(previous:unknown[]|undefined,next:unknown[]|undefined){return !previous||!next||previous.length!==next.length||previous.some((value,index)=>!Object.is(value,next[index]));}
vi.mock('react',async importOriginal=>({...await importOriginal<typeof import('react')>(),
 useState:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push(typeof initial==='function'?initial():initial);return [hooks.states[index],(value:unknown)=>{const next=typeof value==='function'?value(hooks.states[index]):value;if(!Object.is(next,hooks.states[index])){hooks.states[index]=next;hooks.dirty=true;}}];},
 useRef:(initial:unknown)=>{const index=hooks.index++;if(index>=hooks.states.length)hooks.states.push({current:initial});return hooks.states[index];},
 useCallback:(callback:unknown,deps:unknown[])=>{const index=hooks.index++,prior=hooks.states[index] as {value:unknown;deps:unknown[]}|undefined;if(!prior||changed(prior.deps,deps))hooks.states[index]={value:callback,deps};return (hooks.states[index] as {value:unknown}).value;},
 useEffect:(effect:()=>void|(()=>void),deps?:unknown[])=>{const index=hooks.index++,prior=hooks.states[index] as {deps?:unknown[];cleanup?:()=>void}|undefined;if(!prior||changed(prior.deps,deps)){hooks.states[index]={deps};hooks.effects.push(()=>{prior?.cleanup?.();const cleanup=effect();if(typeof cleanup==='function')(hooks.states[index] as {cleanup?:()=>void}).cleanup=cleanup;});}},
}));
vi.mock('./game-studio-api',async importOriginal=>({...await importOriginal<typeof import('./game-studio-api')>(),captureGameStudioSelection:()=>null,clearGameStudioSelection:vi.fn(),stripGameStudioSelectionQuery:vi.fn()}));
type Element=ReactElement<{children?:ReactNode;error?:string;onClick?:()=>void}>;
function nodes(node:ReactNode):Element[]{if(Array.isArray(node))return node.flatMap(nodes);if(!isValidElement<{children?:ReactNode}>(node))return [];return [node as Element,...nodes(node.props.children)];}
function text(node:ReactNode):string{if(Array.isArray(node))return node.map(text).join('');if(isValidElement<{children?:ReactNode}>(node))return text(node.props.children);return typeof node==='string'||typeof node==='number'?String(node):'';}
function view(){function render(){let tree:ReactNode;do{hooks.dirty=false;hooks.index=0;tree=App();while(hooks.effects.length)hooks.effects.shift()!();}while(hooks.dirty);return tree!;}return {render,find:(kind:unknown,label='')=>nodes(render()).find(node=>node.type===kind&&text(node).includes(label))};}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
afterEach(()=>{for(const value of hooks.states)(value as {cleanup?:()=>void})?.cleanup?.();hooks.states=[];hooks.index=0;hooks.dirty=false;hooks.effects=[];setCsrf('');vi.unstubAllGlobals();});

it('replaces an expired administrative workspace with sign-in immediately and does not repeat account creation',async()=>{
 const target=Object.assign(new EventTarget(),{location:{search:''}});vi.stubGlobal('window',target);
 const user={id:'admin',name:'Synthetic Admin',role:'admin',auth_provider:'core',csrf_token:'current-session',principal_kind:'human'};
 const school={id:'school',name:'Synthetic school',description:'',core_resource_id:null,role:'admin',effective_role:'admin',capabilities:{manage_school:true,manage_members:true,manage_agents:true,manage_resources:true,create_course:true,approve_changes:true,manage_connections:true}};
 const fetcher=vi.fn().mockImplementation((url:string)=>Promise.resolve(new Response(JSON.stringify(url==='/api/auth/config'?{mode:'core',password_login:true}:url==='/api/auth/me'?user:url==='/api/schools'?[school]:{detail:'Sign in required'}),{status:url==='/api/admin/family-accounts'?401:200})));vi.stubGlobal('fetch',fetcher);
 const ui=view();ui.render();await settle();ui.render();await settle();ui.render();
 ui.find('button','Students & parents')!.props.onClick!();expect(ui.find(FamilyAccounts)).toBeDefined();
 await expect(request('/admin/family-accounts',{method:'POST',body:'{"name":"Synthetic learner"}'})).rejects.toMatchObject({status:401});
 expect(ui.find(FamilyAccounts)).toBeUndefined();expect(ui.find(PasswordSignIn)).toBeDefined();expect(ui.find(Notice)?.props.error).toContain('Your sign-in expired');expect(ui.find('button','Students & parents')).toBeUndefined();
 expect(fetcher.mock.calls.filter(call=>call[0]==='/api/admin/family-accounts')).toHaveLength(1);expect(fetcher.mock.calls.filter(call=>call[0]==='/api/auth/password')).toHaveLength(0);
});
