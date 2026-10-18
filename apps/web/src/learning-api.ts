import { request, send } from './api';

export const preferenceDimensions = ['reading','diagrams','video','audio','pace','detail','encouragement','question_frequency','structure'] as const;
export type PreferenceDimension = typeof preferenceDimensions[number];
export type PreferenceAnswers = Record<PreferenceDimension,number>;
export type LearningQuestion = {id:PreferenceDimension;prompt:string;low_label:string;high_label:string};
export type LearningProfile = {status:'required'|'complete';version:number;answers:PreferenceAnswers;weights:PreferenceAnswers;feedback_count:number;revision_id:string|null;questions:LearningQuestion[];adaptation:'bounded-per-student-per-teacher-v1'};
export type StudentFeedbackRecord = {id:string;turn_id:string;accuracy:number|null;accuracy_status?:'not_assessed'|'student_rating';helpfulness:number;style_preferences:Partial<PreferenceAnswers>;review_required:boolean;created_at:string;effective_weights:PreferenceAnswers};
export type FeedbackInput = {turn_id:string;accuracy:number|null;helpfulness:number;style_preferences?:Partial<PreferenceAnswers>};
export const neutralPreferences = ():PreferenceAnswers=>Object.fromEntries(preferenceDimensions.map(key=>[key,2])) as PreferenceAnswers;
export function completePreferenceAnswers(answers:Partial<PreferenceAnswers>):answers is PreferenceAnswers{return preferenceDimensions.every(key=>Number.isInteger(answers[key])&&answers[key]!>=0&&answers[key]!<=4);}
export function preferencePayload(answers:PreferenceAnswers,expectedVersion:number){
 if(!completePreferenceAnswers(answers))throw new Error('Choose a preference for each of the nine questions.');
 return {answers:Object.fromEntries(preferenceDimensions.map(key=>[key,answers[key]])) as PreferenceAnswers,expected_version:expectedVersion};
}
export const getLearningPreferences=()=>request<LearningProfile>('/me/learning-preferences');
export const saveLearningPreferences=(answers:PreferenceAnswers,version:number)=>send<LearningProfile>('/me/learning-preferences',preferencePayload(answers,version),'PUT');
export const resetLearningPreferences=(version:number)=>send<LearningProfile>('/me/learning-preferences/reset',{expected_version:version});
export const getStudentFeedback=(turnId:string)=>request<StudentFeedbackRecord|null>(`/me/feedback?turn_id=${encodeURIComponent(turnId)}`);
export function feedbackPayload(input:FeedbackInput):FeedbackInput{
 if(input.accuracy!==null&&(!Number.isInteger(input.accuracy)||input.accuracy<1||input.accuracy>5)||!Number.isInteger(input.helpfulness)||input.helpfulness<1||input.helpfulness>5)throw new Error('Choose both feedback ratings.');
 const style=Object.fromEntries(preferenceDimensions.filter(key=>input.style_preferences?.[key]!==undefined).map(key=>[key,input.style_preferences![key]])) as Partial<PreferenceAnswers>;
 if(Object.values(style).some(value=>!Number.isInteger(value)||value!<0||value!>4))throw new Error('Choose a valid teaching preference.');
 return {turn_id:input.turn_id,accuracy:input.accuracy,helpfulness:input.helpfulness,...(Object.keys(style).length?{style_preferences:style}:{})};
}
export const sendStudentFeedback=(input:FeedbackInput)=>send<StudentFeedbackRecord>('/me/feedback',feedbackPayload(input));
