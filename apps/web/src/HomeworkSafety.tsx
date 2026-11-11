import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Flag, RefreshCw, ShieldCheck } from 'lucide-react';
import { errorText, stamp } from './api';
import { Badge, Notice, Spinner } from './components';
import { getHomeworkSafetyReport, getHomeworkSafetyReports, homeworkSafetyCategories, homeworkSafetyInbox, retainedHomeworkSafetyRequest, retainHomeworkSafetyRequest, reviewHomeworkSafetyReport, sendHomeworkSafetyReport, type HomeworkSafetyCategory, type HomeworkSafetyReport, type HomeworkSafetyReportDetail, type HomeworkSafetyReview, type HomeworkSafetyStaffReport } from './homework-safety-api';

export const homeworkSafetyCategoryLabels:Record<HomeworkSafetyCategory,string>={unsafe:'Something dangerous or upsetting',sexual_content:'Sexual or inappropriate content',bullying:'Bullying or mean behavior',self_harm:'Someone may hurt themselves',private_data:'Personal or private information',other:'Another concern'};
export function HomeworkSafetyDisclosure({privateMode=false}:{privateMode?:boolean}){
 return <aside className="help-text homework-safety-disclosure" aria-label="Using your study helper safely"><p>{privateMode?'Your study helper can make mistakes.':'This is an AI study helper. AI can make mistakes.'} Check important information with a teacher or trusted adult.</p><p>Keep names, addresses, phone numbers and other contact information out of your questions and photos. School staff can review saved conversations and photos.</p><p>If anything feels upsetting or unsafe, use <strong>Report a concern</strong> and tell a trusted adult. Reports are checked by school staff; they are not an emergency service.</p></aside>;
}

export function HomeworkSafetyReportReceipt({report}:{report:HomeworkSafetyReport}){
 return <div role="status"><strong>Your concern is saved for school staff.</strong><p className="help-text">{report.status==='follow_up_required'?'School staff have reviewed this concern and still need to follow up.':report.status==='reviewed'?'School staff have reviewed this concern.':'School staff still need to review this concern.'} If you feel unsafe or upset, tell a trusted adult now.</p><small>Saved {stamp(report.created_at)}</small></div>;
}

export function HomeworkSafetyReportButton({sessionId,messageId,scope}:{sessionId:string;messageId:string;scope:string}){
 const storageScope=`student:${scope}:${sessionId}:${messageId}`;
 const [open,setOpen]=useState(false),[loading,setLoading]=useState(false),[loaded,setLoaded]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [report,setReport]=useState<HomeworkSafetyReport|null>(null),[category,setCategory]=useState<HomeworkSafetyCategory|' '>(' '),[detail,setDetail]=useState('');
 const [uncertain,setUncertain]=useState(()=>!!retainedHomeworkSafetyRequest(storageScope));
 const requestLock=useRef(false);
 const check=async()=>{
  if(requestLock.current)return;
  requestLock.current=true;setLoading(true);setError('');
  try{const saved=await getHomeworkSafetyReports(sessionId,messageId);const found=saved.reports[0]||null;setReport(found);setLoaded(true);
   if(found){retainHomeworkSafetyRequest(storageScope,null);setUncertain(false);}else if(retainedHomeworkSafetyRequest(storageScope)){setUncertain(true);setError('Your report has not been confirmed. No additional report was sent. Ask a school adult for help.');}
  }catch(e){setError(errorText(e));}finally{requestLock.current=false;setLoading(false);}
 };
 async function save(event:FormEvent){
  event.preventDefault();if(requestLock.current||busy||loading||uncertain||report||!loaded||category===' ')return;
  requestLock.current=true;setBusy(true);setError('');
  const key=crypto.randomUUID();retainHomeworkSafetyRequest(storageScope,{key});
  try{setReport(await sendHomeworkSafetyReport(sessionId,messageId,category,detail,key));retainHomeworkSafetyRequest(storageScope,null);setUncertain(false);setDetail('');}
  catch(e){setUncertain(true);setError(`${errorText(e)} Check the saved concern before continuing. Your report will not be sent again automatically.`);}
  finally{requestLock.current=false;setBusy(false);}
 }
 return <section className="student-feedback homework-safety-report" aria-label="Safety concern for this message"><button type="button" className="text-button" aria-expanded={open} onClick={()=>{setOpen(value=>!value);if(!open&&!loaded)void check();}}><Flag size={14}/>{report?'Concern saved':'Report a concern'}</button>{open&&<div className="feedback-content"><Notice error={error}/>{loading?<Spinner text="Checking saved concerns…"/>:report?<HomeworkSafetyReportReceipt report={report}/>:uncertain?<><p className="help-text" role="status">Your report is awaiting confirmation. You can check its saved status. This will not repeat your report or ask the AI again.</p><button type="button" className="button secondary" onClick={()=>void check()}><RefreshCw size={14}/>Check saved concern</button></>:!loaded?<button type="button" className="button secondary" onClick={()=>void check()}>Check saved concern</button>:<form onSubmit={save}><p>Tell school staff about a concern with this conversation. If you feel unsafe, tell a trusted adult now.</p><fieldset disabled={busy}><legend className="sr-only">Safety concern</legend><label>What is the concern?<select required value={category} onChange={e=>setCategory(e.target.value as HomeworkSafetyCategory|' ')}><option value=" ">Choose a concern</option>{homeworkSafetyCategories.map(value=><option key={value} value={value}>{homeworkSafetyCategoryLabels[value]}</option>)}</select></label><label>What would you like school staff to know? · optional<textarea rows={3} maxLength={1000} value={detail} onChange={e=>setDetail(e.target.value)}/></label><p className="help-text">Please leave out personal contact information. This report goes to school staff. It does not change the AI’s personality or send another AI request.</p><button type="submit" className="button secondary" disabled={category===' '||busy}>{busy?'Saving concern…':'Send concern to school staff'}</button></fieldset></form>}</div>}</section>;
}

export function HomeworkSafetyInbox({schoolId}:{schoolId:string}){
 const [reports,setReports]=useState<HomeworkSafetyStaffReport[]>([]),[cursor,setCursor]=useState<string|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[selected,setSelected]=useState<HomeworkSafetyReportDetail|null>(null);
 const [detailLoading,setDetailLoading]=useState(false);
 const generation=useRef(0);
 const load=useCallback(async()=>{const current=++generation.current;setLoading(true);setError('');setSelected(null);try{const value=await homeworkSafetyInbox(schoolId);if(current===generation.current){setReports(value.reports);setCursor(value.next_cursor);}}catch(e){if(current===generation.current)setError(errorText(e));}finally{if(current===generation.current)setLoading(false);}},[schoolId]);
 useEffect(()=>{void load();return()=>{generation.current++;};},[load]);
 async function open(id:string){if(detailLoading)return;const current=++generation.current;setDetailLoading(true);setError('');try{const value=await getHomeworkSafetyReport(schoolId,id);if(current===generation.current)setSelected(value);}catch(e){if(current===generation.current)setError(errorText(e));}finally{if(current===generation.current)setDetailLoading(false);}}
 async function more(){if(loading||!cursor)return;setLoading(true);setError('');try{const value=await homeworkSafetyInbox(schoolId,cursor);setReports(previous=>[...new Map([...previous,...value.reports].map(item=>[item.id,item])).values()]);setCursor(value.next_cursor);}catch(e){setError(errorText(e));}finally{setLoading(false);}}
 return <section className="panel" aria-label="Homework safety review inbox"><div className="panel-head"><div><h2>Homework safety review</h2><p>Student concerns and automatic safety flags for this school. Human review never sends another model request.</p></div><button type="button" className="text-button" disabled={loading||detailLoading} onClick={()=>void load()}><RefreshCw size={14}/>Refresh safety inbox</button></div><div className="panel-body"><Notice error={error}/>{loading&&!reports.length?<Spinner text="Loading school safety reports…"/>:detailLoading?<Spinner text="Loading recorded concern…"/>:selected?<><button type="button" className="text-button" onClick={()=>setSelected(null)}>Back to safety inbox</button><HomeworkSafetyStaffReview key={selected.report.id} schoolId={schoolId} value={selected} changed={value=>{setSelected(value);setReports(previous=>previous.map(item=>item.id===value.report.id?value.report:item));}}/></>:reports.length?<><div className="table-scroll"><table><thead><tr><th>Student</th><th>Concern</th><th>Source</th><th>Status</th><th>Recorded</th><th>Review</th></tr></thead><tbody>{reports.map(report=><tr key={report.id}><td>{report.student_name}</td><td>{homeworkSafetyCategoryLabels[report.category]||'Safety concern'}</td><td>{report.source==='automatic'?'Automatic safety flag':'Student report'}</td><td><Badge tone={report.status!=='reviewed'?'amber':''}>{report.status==='pending'?'Needs review':report.status==='follow_up_required'?'Follow-up required':'Reviewed'}</Badge></td><td>{stamp(report.created_at)}</td><td><button type="button" className="text-button" onClick={()=>void open(report.id)}>Review concern</button></td></tr>)}</tbody></table></div>{cursor&&<button type="button" className="button secondary" disabled={loading} onClick={()=>void more()}>Load older safety reports</button>}</>:<p>No safety concerns are recorded for this school.</p>}</div></section>;
}

export function HomeworkSafetyStaffReview({schoolId,value,changed}:{schoolId:string;value:HomeworkSafetyReportDetail;changed:(value:HomeworkSafetyReportDetail)=>void}){
 const [disposition,setDisposition]=useState<HomeworkSafetyReview['disposition']>('reviewed'),[reason,setReason]=useState<HomeworkSafetyReview['reason_code']>('addressed'),[note,setNote]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [resolutionAcknowledged,setResolutionAcknowledged]=useState(false);
 const storageScope=`review:${schoolId}:${value.report.id}`;
 const [uncertain,setUncertain]=useState(()=>!!retainedHomeworkSafetyRequest(storageScope));
 const lock=useRef(false);
 const expectedReview=useRef(retainedHomeworkSafetyRequest(storageScope)?.expected_review_id);
 const report=value.report,message=value.message,followup=report.status==='follow_up_required'&&report.review?.disposition==='follow_up_required';
 const final=report.status==='reviewed';
 async function check(){
  if(lock.current)return;lock.current=true;setBusy(true);setError('');
  try{const fresh=await getHomeworkSafetyReport(schoolId,report.id);changed(fresh);
   if(fresh.report.review&&fresh.report.review.id!==expectedReview.current){retainHomeworkSafetyRequest(storageScope,null);setUncertain(false);expectedReview.current=undefined;}
   else setError('The review outcome has not been confirmed. No additional review was sent.');
  }catch(e){setError(errorText(e));}finally{lock.current=false;setBusy(false);}
 }
 async function review(event:FormEvent){
  event.preventDefault();if(lock.current||busy||uncertain||final||report.review&&!followup||followup&&!resolutionAcknowledged||note.trim().length<10)return;
  lock.current=true;setBusy(true);setError('');const key=crypto.randomUUID(),prior=followup?report.review!.id:undefined;
  expectedReview.current=prior;retainHomeworkSafetyRequest(storageScope,{key,...(prior?{expected_review_id:prior}:{})});
  try{const saved=await reviewHomeworkSafetyReport(schoolId,report.id,report.report_sha256,followup?'reviewed':disposition,reason,note,key,prior);
   retainHomeworkSafetyRequest(storageScope,null);setUncertain(false);expectedReview.current=undefined;setNote('');setResolutionAcknowledged(false);
   const history=report.review_history||[...(report.review?[report.review]:[])];
   changed({...value,report:{...report,status:saved.disposition==='follow_up_required'?'follow_up_required':'reviewed',review:saved,review_history:[...history.filter(item=>item.id!==saved.id),saved]}});
  }catch(e){setUncertain(true);setError(`${errorText(e)} Check the recorded review before continuing; it will not be submitted again automatically.`);}finally{lock.current=false;setBusy(false);}
 }
 const history=report.review_history?.length?report.review_history:report.review?[report.review]:[];
 return <article className="review-content"><h3>{homeworkSafetyCategoryLabels[report.category]||'Safety concern'}</h3><p><strong>{report.student_name}</strong> · {report.source==='automatic'?'Automatic safety flag':'Student report'} · {stamp(report.created_at)}</p>{report.detail&&<><h4>Concern submitted</h4><p className="prose">{report.detail}</p></>}{report.reason_codes.length>0&&<><h4>Recorded safety reasons</h4><ul>{report.reason_codes.map((code,index)=><li key={`${code}:${index}`}>{code.replaceAll('_',' ')}</li>)}</ul></>}<h4>Recorded message and outcome</h4>{message?<><p className="help-text">Status: {message.status}</p><p className="prose">{message.message}</p>{['released','clarify'].includes(message.status)&&message.answer&&<><h4>Student-visible explanation</h4><p className="prose">{message.answer}</p></>}{message.error&&<p className="help-text">{message.error}</p>}<p className="help-text">Use the school’s audited conversation view when associated homework photos need review.</p></>:<p className="help-text">This message is unavailable under the current school access rules. The concern remains recorded.</p>}<Notice error={error}/>
  {history.length>0&&<section aria-label="Recorded human review history"><h4>Human review history</h4>{history.map(receipt=><div key={receipt.id}><p>{receipt.disposition==='follow_up_required'?'Follow-up required':'Reviewed'} · {receipt.reason_code.replaceAll('_',' ')}</p>{receipt.note&&<p className="prose">{receipt.note}</p>}<small>{stamp(receipt.created_at)}</small></div>)}</section>}
  {final?<p role="status">Human review and any required resolution are recorded.</p>:uncertain?<><p role="status">This review is awaiting confirmation. No model request or repeated review will be sent.</p><button type="button" className="button secondary" disabled={busy} onClick={()=>void check()}>Check saved review</button></>:<form onSubmit={review}><fieldset disabled={busy}><legend>{followup?'Resolve required follow-up':'Human school review'}</legend>{followup?<p role="status">Follow-up remains open. Record its resolution after a human has addressed the concern. This app does not contact an adult automatically.</p>:<label>Disposition<select value={disposition} onChange={e=>setDisposition(e.target.value as HomeworkSafetyReview['disposition'])}><option value="reviewed">Reviewed</option><option value="follow_up_required">Follow-up required</option></select></label>}<label>Reason<select value={reason} onChange={e=>setReason(e.target.value as HomeworkSafetyReview['reason_code'])}><option value="addressed">Addressed</option><option value="contacted_trusted_adult">Contacted a trusted adult</option><option value="false_positive">False positive</option><option value="other">Other</option></select></label><label>Human review note · at least 10 characters<textarea required rows={3} minLength={10} maxLength={1000} value={note} onChange={e=>setNote(e.target.value)}/></label><p className="help-text">Records an append-only human {followup?'resolution against the saved follow-up receipt':'review against this exact report'}. It does not change teaching facts, model personality, publication permissions or school policy.</p><label className="checkbox-label" hidden={!followup}>{followup&&<><input type="checkbox" checked={resolutionAcknowledged} onChange={e=>setResolutionAcknowledged(e.target.checked)}/>I have completed the required human follow-up and am recording its resolution.</>}</label><button className="button primary" disabled={busy||followup&&!resolutionAcknowledged||note.trim().length<10}><ShieldCheck size={14}/>{busy?'Recording review…':followup?'Record follow-up resolution':'Record human review'}</button></fieldset></form>}
 </article>;
}
