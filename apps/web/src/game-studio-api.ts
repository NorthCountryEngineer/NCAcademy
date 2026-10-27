import { request, schoolPath, send } from './api';
import { GAME_STUDIO_RUNTIME_CONTRACT, GAME_STUDIO_RUNTIME_DIGEST, validateGameSpec, type GameSpec, type GameResult } from './game-studio-runtime';
export type GameStudioSettings={school_id:string;version:number;enabled:boolean;studio_origin:string;api_origin:string;host_id:string;revision_id:string|null;can_manage:boolean;can_browse:boolean;creator_url:string|null;creator_ready?:boolean;host_configured?:boolean};
export type GameStudioSettingsDraft=Pick<GameStudioSettings,'enabled'|'studio_origin'|'api_origin'|'host_id'>;
export type GameStudioVerification={verified:boolean;creator_url:string|null;creator_ready:boolean;public_catalog_ready:boolean;host_configured:boolean;return_path:string|null;connection_version:number;detail:string};
export type GameStudioCatalogGame={game_id:string;published_revision:number;title:string;description:string;spec_sha256:string;level_ids:string[];genre:string;theme:string};
export type GameStudioCatalog={games:GameStudioCatalogGame[];cursor:string|null;connection_version:number};
export type GameStudioRuntime={id:string;sha256:string};
export type ScoringPolicy={version:1;max_points:number;method:'proportional'|'completion'|'arcade_target';arcade_target?:number;rounding:'whole'|'hundredth';attempt_policy:'first'|'latest'|'best';max_attempts:number;reveal_policy:'zero'|'earned'};
export type CourseGame=GameStudioCatalogGame&{scoring_policy?:ScoringPolicy|null;can_configure_scoring?:boolean;id:string;course_id:string;school_id:string;version:number;status:'draft'|'approved'|'stale'|'revoked';learning_objective:string;lesson_label:string;approval_id:string|null;can_manage:boolean;can_approve:boolean;can_play:boolean;can_revoke:boolean;runtime_contract:GameStudioRuntime};
export type CourseGameIndex={games:CourseGame[];can_prepare:boolean;can_approve:boolean};
export type CourseGameDetail=CourseGame&{spec:GameSpec};
export type CourseGamePlay={spec:GameSpec;game_id:string;published_revision:number;spec_sha256:string;runtime_contract:GameStudioRuntime;resource_id:string;course_id:string;preview:boolean};
export type CourseGamePreparation={game_id:string;published_revision:number;spec_sha256:string;learning_objective:string;lesson_label:string;idempotency_key:string;scoring_policy?:ScoringPolicy|null};
export type GameStudioSelection={studio_origin:string;game_id:string;published_revision:number};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const settingsListeners=new Map<string,Set<()=>void>>();
/** Notify only mounted consumers of this school's local connection metadata. */
export function subscribeGameStudioSettings(schoolId:string,listener:()=>void):()=>void{
 const listeners=settingsListeners.get(schoolId)||new Set<()=>void>();listeners.add(listener);settingsListeners.set(schoolId,listeners);
 return ()=>{listeners.delete(listener);if(!listeners.size&&settingsListeners.get(schoolId)===listeners)settingsListeners.delete(schoolId);};
}
function settingsChanged(schoolId:string){for(const listener of settingsListeners.get(schoolId)||[])listener();}
export const gameStudioPath=(schoolId:string,suffix='')=>`/schools/${encodeURIComponent(schoolId)}/game-studio${suffix}`;
export const courseGamesPath=(schoolId:string,courseId:string,id?:string,action='')=>schoolPath(`/courses/${encodeURIComponent(courseId)}/games${id?`/${encodeURIComponent(id)}`:''}${action}`,schoolId);
export const gameStudioSettings=(schoolId:string)=>request<GameStudioSettings>(gameStudioPath(schoolId));
export async function saveGameStudioSettings(schoolId:string,value:GameStudioSettingsDraft,version:number){
 const saved=await send<GameStudioSettings>(gameStudioPath(schoolId),{expected_version:version,enabled:value.enabled,studio_origin:value.studio_origin.trim(),api_origin:value.api_origin.trim(),host_id:value.host_id.trim()},'PUT');settingsChanged(schoolId);return saved;
}
export async function verifyGameStudio(schoolId:string,version:number){
 const verified=await send<GameStudioVerification>(gameStudioPath(schoolId,'/verify'),{expected_version:version});settingsChanged(schoolId);return verified;
}
export const gameStudioCatalog=(schoolId:string,cursor?:string)=>request<GameStudioCatalog>(gameStudioPath(schoolId,`/catalog${cursor?`?cursor=${encodeURIComponent(cursor)}`:''}`));
export const courseGames=(schoolId:string,courseId:string)=>request<CourseGameIndex>(courseGamesPath(schoolId,courseId));
export const courseGameDetail=(schoolId:string,courseId:string,id:string)=>request<CourseGameDetail>(courseGamesPath(schoolId,courseId,id));
export const prepareCourseGame=(schoolId:string,courseId:string,value:CourseGamePreparation)=>send<CourseGame>(courseGamesPath(schoolId,courseId),{game_id:value.game_id,published_revision:value.published_revision,spec_sha256:value.spec_sha256,learning_objective:value.learning_objective.trim(),lesson_label:value.lesson_label.trim()||'Practice game',idempotency_key:value.idempotency_key,...(value.scoring_policy===undefined?{}:{scoring_policy:scoringPolicyPayload(value.scoring_policy)})});
export const approveCourseGame=(schoolId:string,courseId:string,value:CourseGame,levels:string[])=>send<CourseGame>(courseGamesPath(schoolId,courseId,value.id,'/approve'),{expected_version:value.version,reviewed_level_ids:[...new Set(levels)],reviewed:true});
export const revokeCourseGame=(schoolId:string,courseId:string,value:CourseGame)=>send<CourseGame>(courseGamesPath(schoolId,courseId,value.id,'/revoke'),{expected_version:value.version});
export const playCourseGame=(schoolId:string,courseId:string,value:CourseGame)=>send<CourseGamePlay>(courseGamesPath(schoolId,courseId,value.id,'/play'),{});
export type GameAttemptCalculation={earned_points:number;max_points:number;numerator:number;denominator:number;method:ScoringPolicy['method'];provisional:true};
export type GameAttemptAward={points:number;reason:string;awarded_at:string;awarded_by:string};
export type GameAttempt={id:string;student_id:string;ordinal:number;status:'started'|'observed';scoring_policy:ScoringPolicy;started_at:string;resource_version:number;scoring_revision_id:string;scoring_policy_sha256:string;result?:GameResult|null;calculation?:GameAttemptCalculation|null;award?:GameAttemptAward|null};
export type GameAttemptSummary={suggested_points:number|null;max_points:number|null;selected_attempt_id:string|null;attempt_count:number;remaining_attempts:number};
export type GameAttemptIndex={attempts:GameAttempt[];summary:GameAttemptSummary|null;summaries:({student_id:string}&GameAttemptSummary)[];can_award:boolean};
export type GameAttemptPlay=CourseGamePlay&{attempt:GameAttempt};
/** Only the public scoring fields are sent. Identity, points and approval remain server-owned. */
export function scoringPolicyPayload(value:ScoringPolicy|null):ScoringPolicy|null{return value===null?null:{version:1,max_points:value.max_points,method:value.method,...(value.method==='arcade_target'?{arcade_target:value.arcade_target}:{}),rounding:value.rounding,attempt_policy:value.attempt_policy,max_attempts:value.max_attempts,reveal_policy:value.reveal_policy};}
export const saveCourseGameScoring=(schoolId:string,courseId:string,value:CourseGame,policy:ScoringPolicy|null)=>send<CourseGame>(courseGamesPath(schoolId,courseId,value.id,'/scoring'),{expected_version:value.version,scoring_policy:scoringPolicyPayload(policy)},'PUT');
export const courseGameAttempts=(schoolId:string,courseId:string,id:string)=>request<GameAttemptIndex>(courseGamesPath(schoolId,courseId,id,'/attempts'));
export const startCourseGameAttempt=(schoolId:string,courseId:string,id:string,key:string)=>send<GameAttemptPlay>(courseGamesPath(schoolId,courseId,id,'/attempts'),{idempotency_key:key});
export const submitCourseGameResult=(schoolId:string,courseId:string,id:string,attemptId:string,result:GameResult)=>send<GameAttempt>(courseGamesPath(schoolId,courseId,id,`/attempts/${encodeURIComponent(attemptId)}/result`),{result:{outcome:result.outcome,elapsed:result.elapsed,score:result.score,...(result.points===undefined?{}:{points:result.points}),reviewedLevelIds:[...result.reviewedLevelIds]}});
export const awardCourseGameAttempt=(schoolId:string,courseId:string,id:string,attemptId:string,points:number,reason:string)=>send<GameAttempt>(courseGamesPath(schoolId,courseId,id,`/attempts/${encodeURIComponent(attemptId)}/award`),{points,reason:reason.trim()});
export function assertCourseGameSnapshot(value:CourseGameDetail|CourseGamePlay,expected:CourseGame):GameSpec{
 if(value.game_id!==expected.game_id||value.published_revision!==expected.published_revision||value.spec_sha256!==expected.spec_sha256||value.runtime_contract?.id!==GAME_STUDIO_RUNTIME_CONTRACT||value.runtime_contract?.sha256!==GAME_STUDIO_RUNTIME_DIGEST)throw new Error('This game snapshot or player version changed. Reload the class before playing.');
 if(('resource_id' in value&&(value.resource_id!==expected.id||value.course_id!==expected.course_id))||('id' in value&&(value.id!==expected.id||value.course_id!==expected.course_id||value.school_id!==expected.school_id)))throw new Error('This game belongs to a different class attachment.');
 return validateGameSpec(value.spec);
}
export function gameStudioOrigin(value:string):string|null{try{const parsed=new URL(value);if((parsed.protocol!=='https:'&&!(parsed.protocol==='http:'&&['127.0.0.1','localhost'].includes(parsed.hostname)))||parsed.origin!==value||parsed.username||parsed.password||parsed.search||parsed.hash)return null;return parsed.origin;}catch{return null;}}
export function gameStudioCreatorURL(value:GameStudioVerification,settings:GameStudioSettings):string|null{
 if(!settings.can_manage||!settings.enabled||!value.verified||!value.creator_ready||value.connection_version!==settings.version||!value.creator_url||!gameStudioOrigin(settings.studio_origin))return null;
 try{const url=new URL(value.creator_url);if(url.origin!==settings.studio_origin||url.pathname!=='/game-studio'||url.hash||url.username||url.password)return null;if(!url.search)return url.href;if(!value.host_configured||[...url.searchParams.keys()].some(key=>key!=='host'&&key!=='login')||url.searchParams.getAll('host').length!==1||url.searchParams.get('host')!==settings.host_id||url.searchParams.getAll('login').length>1||(url.searchParams.has('login')&&url.searchParams.get('login')!=='auto'))return null;return url.href;}catch{return null;}
}
/** Saved verification is scoped by the API to this exact connection revision. */
export function savedGameStudioVerification(settings:GameStudioSettings):GameStudioVerification|null{
 if(!settings.can_manage||!settings.enabled||settings.creator_ready!==true||!settings.creator_url)return null;
 const value:GameStudioVerification={verified:true,creator_ready:true,creator_url:settings.creator_url,public_catalog_ready:true,host_configured:settings.host_configured===true,return_path:settings.host_configured===true?'/':null,connection_version:settings.version,detail:settings.host_configured===true?'The saved connection is verified. Game Studio uses your NCE Core sign-in and checks its own administrative access.':'The saved public connection is verified. Open Game Studio separately, then return to Academy manually.'};
 return gameStudioCreatorURL(value,settings)?value:null;
}
export function readGameStudioSelection(locationURL:string,expectedStudioOrigin?:string):GameStudioSelection|null{
 try{const query=new URL(locationURL).searchParams,keys=['gameStudioOrigin','gameStudioGameId','gameStudioRevision'];if(keys.some(key=>query.getAll(key).length!==1))return null;const origin=query.get(keys[0])||'',id=query.get(keys[1])||'',revision=query.get(keys[2])||'';if(!gameStudioOrigin(origin)||(expectedStudioOrigin!==undefined&&origin!==expectedStudioOrigin)||!uuid.test(id)||!/^([1-9][0-9]{0,9})$/.test(revision)||Number(revision)>1000000000)return null;return {studio_origin:origin,game_id:id.toLowerCase(),published_revision:Number(revision)};}catch{return null;}
}
const selectionKey='ncacademy.game-studio-selection.v1';
export function captureGameStudioSelection():GameStudioSelection|null{
 if(typeof window==='undefined')return null;
 try{const fromURL=readGameStudioSelection(window.location.href);if(fromURL){try{sessionStorage.setItem(selectionKey,JSON.stringify({selection:fromURL,expires_at:Date.now()+15*60*1000}));}catch{/* Use the current public selection when storage is unavailable. */}return fromURL;}const stored=JSON.parse(sessionStorage.getItem(selectionKey)||'null');if(!stored||typeof stored.expires_at!=='number'||!Number.isFinite(stored.expires_at)||stored.expires_at<Date.now()||stored.expires_at>Date.now()+15*60*1000||!stored.selection)return null;const selection=stored.selection as GameStudioSelection;return readGameStudioSelection(`https://academy.example/?${new URLSearchParams({gameStudioOrigin:selection.studio_origin,gameStudioGameId:selection.game_id,gameStudioRevision:String(selection.published_revision)})}`);}catch{return null;}
}
export function clearGameStudioSelection(){try{sessionStorage.removeItem(selectionKey);}catch{/* Navigation remains usable when browser storage is blocked. */}}
export function stripGameStudioSelectionQuery(){if(typeof window==='undefined')return;const url=new URL(window.location.href);for(const key of ['gameStudioOrigin','gameStudioGameId','gameStudioRevision'])url.searchParams.delete(key);window.history.replaceState(null,'',url.pathname+url.search+url.hash);}
