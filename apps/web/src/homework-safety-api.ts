import { request, schoolPath, send } from './api';
import type { HomeworkAIMessage } from './homework-ai-api';

export const homeworkSafetyCategories = ['unsafe','sexual_content','bullying','self_harm','private_data','other'] as const;
export type HomeworkSafetyCategory = typeof homeworkSafetyCategories[number];
export type HomeworkSafetyReview = {id:string;report_id?:string;created_at:string;disposition:'reviewed'|'follow_up_required';reason_code:'addressed'|'contacted_trusted_adult'|'false_positive'|'other';note:string;idempotency_key?:string};
export type HomeworkSafetyReport = {id:string;session_id:string;message_id:string;category:HomeworkSafetyCategory;detail:string;created_at:string;status:'pending'|'follow_up_required'|'reviewed';idempotency_key?:string;review?:Pick<HomeworkSafetyReview,'id'|'disposition'|'created_at'>|null};
export type HomeworkSafetyStaffReport = Omit<HomeworkSafetyReport,'review'> & {school_id:string;student_id:string;student_name:string;source:'student'|'automatic';reason_codes:string[];priority?:string;report_sha256:string;review:HomeworkSafetyReview|null;review_history?:HomeworkSafetyReview[]};
export type HomeworkSafetyReportDetail = {report:HomeworkSafetyStaffReport;message:Pick<HomeworkAIMessage,'id'|'message'|'created_at'|'status'|'error'|'answer'>|null};
export type HomeworkSafetyReportPage = {reports:HomeworkSafetyStaffReport[];next_cursor:string|null};
export type HomeworkSafetyPending = {key:string;expected_review_id?:string};

function boundedNote(value:string){const text=value.trim();if(text.length>1000)throw new Error('Use up to 1,000 characters.');return text;}
function requestKey(key:string){if(key.length<8||key.length>128)throw new Error('A valid report request ID is required.');return key;}
export function homeworkSafetyReportPayload(category:HomeworkSafetyCategory,detail:string,key:string){
 if(!homeworkSafetyCategories.includes(category))throw new Error('Choose a concern type.');
 return {idempotency_key:requestKey(key),category,detail:boundedNote(detail)};
}
const messageSafetyPath=(sessionId:string,messageId:string)=>`/homework-help/ai/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/safety-report`;
export const getHomeworkSafetyReports=(sessionId:string,messageId:string)=>request<{reports:HomeworkSafetyReport[]}>(messageSafetyPath(sessionId,messageId));
export const sendHomeworkSafetyReport=(sessionId:string,messageId:string,category:HomeworkSafetyCategory,detail:string,key:string)=>send<HomeworkSafetyReport>(messageSafetyPath(sessionId,messageId),homeworkSafetyReportPayload(category,detail,key));
export const homeworkSafetyInbox=(schoolId:string,cursor?:string)=>request<HomeworkSafetyReportPage>(schoolPath('/homework-help/ai/safety-reports',schoolId)+(cursor?`&cursor=${encodeURIComponent(cursor)}`:''));
export const getHomeworkSafetyReport=(schoolId:string,reportId:string)=>request<HomeworkSafetyReportDetail>(schoolPath(`/homework-help/ai/safety-reports/${encodeURIComponent(reportId)}`,schoolId));
export function homeworkSafetyReviewPayload(expectedSha256:string,disposition:HomeworkSafetyReview['disposition'],reasonCode:HomeworkSafetyReview['reason_code'],note:string,key:string,expectedReviewId?:string){
 if(!/^[a-f0-9]{64}$/.test(expectedSha256))throw new Error('Reload the saved report before reviewing.');
 if(!['reviewed','follow_up_required'].includes(disposition)||!['addressed','contacted_trusted_adult','false_positive','other'].includes(reasonCode))throw new Error('Choose the review outcome and reason.');
 if(expectedReviewId&&(!/^[A-Za-z0-9_:-]{1,64}$/.test(expectedReviewId)||disposition!=='reviewed'))throw new Error('A follow-up resolution must reference its saved review.');
 const reviewNote=boundedNote(note);if(reviewNote.length<10)throw new Error('Describe the human review in at least 10 characters.');
 return {expected_sha256:expectedSha256,disposition,reason_code:reasonCode,note:reviewNote,idempotency_key:requestKey(key),...(expectedReviewId?{expected_review_id:expectedReviewId}:{})};
}
export const reviewHomeworkSafetyReport=(schoolId:string,reportId:string,expectedSha256:string,disposition:HomeworkSafetyReview['disposition'],reasonCode:HomeworkSafetyReview['reason_code'],note:string,key:string,expectedReviewId?:string)=>send<HomeworkSafetyReview>(schoolPath(`/homework-help/ai/safety-reports/${encodeURIComponent(reportId)}/review`,schoolId),homeworkSafetyReviewPayload(expectedSha256,disposition,reasonCode,note,key,expectedReviewId));

export function retainedHomeworkSafetyRequest(scope:string):HomeworkSafetyPending|null{
 try{const value=JSON.parse(window.sessionStorage.getItem(`ncacademy:homework-safety:${scope}`)||'null');if(value&&typeof value.key==='string'&&value.key.length>=8&&value.key.length<=128)return {key:value.key,...(typeof value.expected_review_id==='string'&&/^[A-Za-z0-9_:-]{1,64}$/.test(value.expected_review_id)?{expected_review_id:value.expected_review_id}:{})};}catch{/* The in-memory lock remains when browser storage is unavailable. */}return null;
}
export function retainHomeworkSafetyRequest(scope:string,value:HomeworkSafetyPending|null){
 try{const name=`ncacademy:homework-safety:${scope}`;if(value)window.sessionStorage.setItem(name,JSON.stringify({key:requestKey(value.key),...(value.expected_review_id?{expected_review_id:value.expected_review_id}:{})}));else window.sessionStorage.removeItem(name);}catch{/* Do not store report notes or conversation text in browser storage. */}
}
