import { request, schoolPath, send } from './api';

export type ChatAuthor={id:string;name:string;principal_kind:'human'|'agent'};
export type ChatGroup={id:string;title:string;description:string;school_id:string;course_id:string|null;visibility:'school'|'class'|'private';join_policy:'open'|'approval'|'invite';required:boolean;status:'pending'|'ready'|'uncertain'|'unavailable';membership:'active'|'requested'|'invited'|'none';can_read:boolean;can_send:boolean;can_manage:boolean;can_review:boolean;can_join:boolean;can_leave:boolean;can_recover:boolean;error:string|null};
export type ChatIndex={groups:ChatGroup[];can_create:boolean;transport:{enabled:boolean};courses:{id:string;title:string}[];school_id:string;truncated?:boolean;courses_truncated?:boolean};
export type ChatMessage={id:string;sequence:number;text:string;created_at:string;author:ChatAuthor;ai:boolean;reply_to:string|null};
export type ChatPage={messages:ChatMessage[];next_after:number;has_more:boolean};
export type ChatCommand={id:string;group_id:string;operation:'message';status:'pending'|'delivered'|'uncertain'|'failed'|'pending_review'|'rejected';text:string;author:ChatAuthor;created_at:string;message_id:string|null;can_retry:boolean;error:string|null;proposal_id:string|null};
export type ChatMember={id:string;name:string;role:string;principal_kind:'human'|'agent';membership:'active'|'requested'|'invited'|'none'};
export type ChatMembers={members:ChatMember[];candidates:ChatMember[];can_manage:boolean};
export type GroupInput={title:string;description:string;course_id:string|null;visibility:ChatGroup['visibility'];join_policy:ChatGroup['join_policy']};
const groupPath=(id:string)=>`/chat/groups/${encodeURIComponent(id)}`;
export const chatIndex=(schoolId:string,discover=false)=>request<ChatIndex>(schoolPath(`/chat/groups${discover?'?discover=true':''}`,schoolId));
export const bootstrapChat=(schoolId:string)=>send<ChatIndex>('/chat/bootstrap',{school_id:schoolId});
export const chatGroup=(id:string)=>request<ChatGroup>(groupPath(id));
export function createChatGroup(schoolId:string,input:GroupInput,key:string){return send<ChatGroup>('/chat/groups',{school_id:schoolId,title:input.title.trim(),description:input.description.trim(),course_id:input.course_id||null,visibility:input.visibility,join_policy:input.visibility==='private'?'invite':input.visibility==='class'?'open':input.join_policy,idempotency_key:key});}
export const joinChatGroup=(id:string)=>send<ChatGroup>(`${groupPath(id)}/join`,{});
export const leaveChatGroup=(id:string)=>send<ChatGroup>(`${groupPath(id)}/leave`,{});
export const recoverChatGroup=(id:string)=>send<ChatGroup>(`${groupPath(id)}/recover`,{});
export const chatMembers=(id:string)=>request<ChatMembers>(`${groupPath(id)}/members`);
export const manageChatMember=(id:string,principalId:string,action:'invite'|'approve'|'remove'|'reject')=>send<ChatMembers>(`${groupPath(id)}/members`,{principal_id:principalId,action});
export const chatMessages=(id:string,after=0)=>request<ChatPage>(`${groupPath(id)}/messages?after=${Math.max(0,Math.floor(after))}&limit=100`);
export function sendChatMessage(id:string,text:string,key:string,replyTo:string|null=null){return send<ChatCommand>(`${groupPath(id)}/messages`,{text:text.trim(),idempotency_key:key,reply_to:replyTo});}
export const chatCommand=(id:string,commandId:string)=>request<ChatCommand>(`${groupPath(id)}/commands/${encodeURIComponent(commandId)}`);
export const findChatCommand=(id:string,key:string)=>request<ChatCommand>(`${groupPath(id)}/commands?idempotency_key=${encodeURIComponent(key)}`);
export const retryChatCommand=(id:string,commandId:string)=>send<ChatCommand>(`${groupPath(id)}/commands/${encodeURIComponent(commandId)}/retry`,{});
export const chatProposals=(id:string)=>request<{proposals:ChatCommand[]}>(`${groupPath(id)}/proposals`);
export const reviewChatProposal=(id:string,proposalId:string,decision:'approve'|'reject')=>send<ChatCommand>(`${groupPath(id)}/proposals/${encodeURIComponent(proposalId)}/review`,{decision});
export function chatMessageProblem(text:string){const value=text.trim();if(!value)return 'Write a message before sending.';if(value.length>4000||new TextEncoder().encode(value).byteLength>16384)return 'Shorten your message to 4,000 characters.';return null;}
export function mergeChatMessages(current:ChatMessage[],incoming:ChatMessage[]){const items=new Map(current.map(item=>[item.id,item]));for(const item of incoming)items.set(item.id,item);return [...items.values()].sort((a,b)=>a.sequence-b.sequence);}
export function chatReceiptStorageKey(userId:string,groupId:string){return `ncacademy:chat-request:${encodeURIComponent(userId)}:${encodeURIComponent(groupId)}`;}
export function savedChatRequest(userId:string,groupId:string){try{return window.sessionStorage.getItem(chatReceiptStorageKey(userId,groupId))||'';}catch{return '';}}
export function rememberChatRequest(userId:string,groupId:string,key:string){try{window.sessionStorage.setItem(chatReceiptStorageKey(userId,groupId),key);}catch{/* In-memory receipt remains available. */}}
export function forgetChatRequest(userId:string,groupId:string){try{window.sessionStorage.removeItem(chatReceiptStorageKey(userId,groupId));}catch{/* No private content is persisted. */}}

export function visibleChatGroups(groups:ChatGroup[],discover=false,query=''){const search=query.trim().toLowerCase();return groups.filter(group=>(discover||group.membership!=='none'||group.required||group.can_manage)&&`${group.title} ${group.description}`.toLowerCase().includes(search));}

// A newer authorized descriptor or a denied read invalidates earlier promises.
// This tracks authority only; paging cursors remain owned by the conversation.
export class ChatAuthorityGuard{
 private generation=0;
 private descriptor:ChatGroup;
 private readable:boolean;
 constructor(group:ChatGroup){this.descriptor=group;this.readable=group.can_read&&group.status==='ready';}
 observe(group:ChatGroup){if(this.descriptor!==group){this.descriptor=group;this.generation++;this.readable=group.can_read&&group.status==='ready';}}
 capture(){return this.generation;}
 current(generation:number){return generation===this.generation;}
 canRead(generation=this.generation){return this.current(generation)&&this.readable;}
 revoke(){this.generation++;this.readable=false;}
}
