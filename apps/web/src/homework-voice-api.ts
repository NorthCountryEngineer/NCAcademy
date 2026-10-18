import { request, send } from './api';

export type TeacherVoice={id:string;name:string};
export type TeacherVoiceConfig={enabled:boolean;available:boolean;voices:TeacherVoice[];selected_voice_id:string|null;preference_version:number;max_recording_seconds:number;audio_mime_type:'audio/wav'};
export type VoiceOperation={id:string;kind?:string;status:'released'|'pending'|'withheld'|'failed';transcript:string|null;audio_url:string|null;error:string|null;created_at?:string};
const voicePath=(courseId:string)=>`/courses/${encodeURIComponent(courseId)}/voice`;
const teacherQuery=(teacherId:string)=>`teacher_profile_id=${encodeURIComponent(teacherId)}`;
export const getTeacherVoice=(courseId:string,teacherId:string)=>request<TeacherVoiceConfig>(`${voicePath(courseId)}?${teacherQuery(teacherId)}`);
export const saveTeacherVoice=(courseId:string,teacherId:string,voiceId:string|null,version:number)=>send<TeacherVoiceConfig>(`${voicePath(courseId)}-preference`,{teacher_profile_id:teacherId,voice_id:voiceId,expected_version:version},'PUT');
export const transcribeHomeworkAudio=(courseId:string,teacherId:string,audioBase64:string,key:string)=>send<VoiceOperation>(`${voicePath(courseId)}/transcriptions`,{teacher_profile_id:teacherId,audio_base64:audioBase64,idempotency_key:key,consent:true});
export const speakHomeworkTurn=(courseId:string,teacherId:string,turnId:string,key:string)=>send<VoiceOperation>(`${voicePath(courseId)}/speech`,{teacher_profile_id:teacherId,turn_id:turnId,idempotency_key:key,consent:true});
export const findVoiceOperation=(courseId:string,teacherId:string,key:string)=>request<VoiceOperation>(`${voicePath(courseId)}/operations?${teacherQuery(teacherId)}&idempotency_key=${encodeURIComponent(key)}`);
export function privateVoiceAudioUrl(courseId:string,value:string|null):string|null{
 if(!/^[A-Za-z0-9_-]{1,64}$/.test(courseId)||!value)return null;
 const match=value.match(/^\/api\/courses\/([A-Za-z0-9_-]{1,64})\/voice\/operations\/[A-Za-z0-9_-]{1,64}\/audio$/);
 return match?.[1]===courseId?value:null;
}
const pendingVoiceKeys=new Map<string,string>();
const voiceStorageKey=(scope:string)=>`ncacademy:voice-request:${scope}`;
// Only an opaque request identity is retained across reloads. Audio, transcripts,
// access tokens and provider credentials are never stored in browser storage.
export function savedVoiceRequest(scope:string):string{
 try{const value=typeof window!=='undefined'?window.sessionStorage.getItem(voiceStorageKey(scope)):null;if(value&&/^[A-Za-z0-9_-]{12,100}$/.test(value))return value;}catch{/* Storage may be unavailable in a private browser. */}
 return pendingVoiceKeys.get(scope)||'';
}
export function rememberVoiceRequest(scope:string,key:string){pendingVoiceKeys.set(scope,key);if(pendingVoiceKeys.size>128)pendingVoiceKeys.delete(pendingVoiceKeys.keys().next().value!);try{if(typeof window!=='undefined')window.sessionStorage.setItem(voiceStorageKey(scope),key);}catch{/* The in-memory key still prevents an automatic resend. */}}
export function forgetVoiceRequest(scope:string){pendingVoiceKeys.delete(scope);try{if(typeof window!=='undefined')window.sessionStorage.removeItem(voiceStorageKey(scope));}catch{/* No audio or transcript is retained here. */}}
// This local conversion strips container metadata and emits bounded mono PCM rather than
// sending a browser-specific recording container or using a browser speech service.
export function pcmWave(samples:Float32Array,inputRate:number,outputRate=16000):Uint8Array{
 if(!Number.isFinite(inputRate)||inputRate<8000||inputRate>192000||!samples.length||samples.length/inputRate>30.05)throw new Error('Record up to 30 seconds of audio.');
 const length=Math.min(Math.floor(samples.length*outputRate/inputRate),30*outputRate),bytes=new Uint8Array(44+length*2),view=new DataView(bytes.buffer);
 const text=(offset:number,value:string)=>{for(let i=0;i<value.length;i++)view.setUint8(offset+i,value.charCodeAt(i));};
 text(0,'RIFF');view.setUint32(4,36+length*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,outputRate,true);view.setUint32(28,outputRate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,length*2,true);
 for(let i=0;i<length;i++){const position=i*inputRate/outputRate,index=Math.floor(position),fraction=position-index,value=Math.max(-1,Math.min(1,samples[index]*(1-fraction)+(samples[Math.min(index+1,samples.length-1)]||0)*fraction));view.setInt16(44+i*2,value<0?Math.round(value*32768):Math.round(value*32767),true);}
 return bytes;
}
export async function prepareVoiceRecording(blob:Blob):Promise<string>{
 if(!blob.size||blob.size>3*1024*1024)throw new Error('Record a shorter message, up to 30 seconds and 3 MB.');
 const context=new AudioContext();
 try{const decoded=await context.decodeAudioData(await blob.arrayBuffer());const mono=new Float32Array(decoded.length);for(let channel=0;channel<decoded.numberOfChannels;channel++){const values=decoded.getChannelData(channel);for(let i=0;i<mono.length;i++)mono[i]+=values[i]/decoded.numberOfChannels;}const bytes=pcmWave(mono,decoded.sampleRate);let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);return btoa(binary);}finally{await context.close();}
}
