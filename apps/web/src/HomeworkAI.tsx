import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowRight, BookOpen, ImagePlus, MessageSquareText, Plus, RefreshCw, Send, X } from 'lucide-react';
import { errorText, stamp } from './api';
import { Badge, Notice, PageHead, Spinner } from './components';
import { prepareAssignmentImage, type CuratedImage } from './curated-api';
import { StudentFeedback } from './StudentFeedback';
import { homeworkReferenceTarget, type HomeworkTarget } from './homework-api';
import { createHomeworkAISession, getHomeworkAISession, homeworkAIContext, homeworkAIIndex, homeworkAIRequestProblem, mergeHomeworkAIPages, privateHomeworkAIImageUrl, retainedHomeworkAIRequest, retainHomeworkAIRequest, sendHomeworkAIMessage, type HomeworkAIConversationSummary, type HomeworkAIIndex, type HomeworkAIInput, type HomeworkAIMessage, type HomeworkAIPending, type HomeworkAISession, type HomeworkAIStudentConfiguration } from './homework-ai-api';
import './homework-chat.css';

type HomeworkAIProps = { schoolId:string; userId:string; index:HomeworkAIIndex; openCourse:(target:HomeworkTarget)=>void; initialCourseId?:string; compact?:boolean };

export function homeworkConversationTitle(session:HomeworkAIConversationSummary):string {
 return session.title?.trim().slice(0,160) || 'New conversation';
}

export function homeworkConversationWithReply(conversation:HomeworkAISession,reply:HomeworkAIMessage):HomeworkAISession {
 const firstQuestion=conversation.messages.length===0;
 return {...conversation,session:firstQuestion?{...conversation.session,title:reply.message.trim().slice(0,160) || 'Homework photos'}:conversation.session,messages:[...conversation.messages.filter(item=>item.id!==reply.id),reply]};
}

export function homeworkResponder(item:HomeworkAIMessage, assistantName:string) {
 const triage=item.triage;
 const teacher=triage.responder==='teacher' && !!triage.teacher_name && !!(triage.teacher_profile_id || item.teacher_profile_id);
 return {name:teacher?triage.teacher_name!:assistantName,description:teacher?'AI teacher':'Homework assistant'};
}

export function homeworkAssistantSetupMessage(assistant:HomeworkAIStudentConfiguration):string {
 if(assistant.reapproval_required)return 'A school administrator needs to review and approve the updated assistant before new questions can be answered.';
 if(!assistant.configuration)return 'A school administrator needs to set up and approve the homework assistant.';
 if(!assistant.approved)return 'A school administrator needs to approve the current homework assistant.';
 if(!assistant.configuration.enabled)return 'A school administrator needs to enable Homework Help for this school.';
 return 'A school administrator needs to activate the approved assistant. Please try again once setup is complete.';
}

export function HomeworkAI({schoolId,userId,index:initial,openCourse,initialCourseId,compact=false}:HomeworkAIProps) {
 const [index,setIndex]=useState(initial);
 const [sessionId,setSessionId]=useState(initial.sessions[0]?.id || '');
 const [session,setSession]=useState<HomeworkAISession|null>(null);
 const [courseId,setCourseId]=useState(initial.courses.some(course=>course.id===initialCourseId)?initialCourseId!: '');
 const [message,setMessage]=useState('');
 const [images,setImages]=useState<CuratedImage[]>([]);
 const [confirmed,setConfirmed]=useState(false);
 const [loading,setLoading]=useState(!!initial.sessions.length);
 const [busy,setBusy]=useState(false);
 const [photoBusy,setPhotoBusy]=useState(false);
 const [error,setError]=useState('');
 const [uncertain,setUncertain]=useState(false);
 const [submitted,setSubmitted]=useState<{message:string;photoCount:number}|null>(null);
 const scope=`${schoolId}:${userId}`;
 const pendingRequest=useRef<HomeworkAIPending|null>(retainedHomeworkAIRequest(scope));
 const sending=useRef(false),generation=useRef(0),loadedSession=useRef<string|null>(null);
 const messagesEnd=useRef<HTMLDivElement>(null),question=useRef<HTMLTextAreaElement>(null);
 const assistant=index.assistant,policy=assistant.configuration;
 const pending=!!session?.messages.some(item=>item.status==='pending');
 const locked=busy || loading || uncertain || pending || (!!sessionId && !session);
 const limit=policy?.max_input_chars || 5000;
 const title=policy?.name || 'Homework assistant';
 const remember=(value:HomeworkAIPending|null)=>{pendingRequest.current=value;retainHomeworkAIRequest(scope,value);};

 useEffect(()=>{
  const saved=pendingRequest.current;
  if(saved){setUncertain(true);if(saved.session_id)setSessionId(saved.session_id);}
 },[]);
 useEffect(()=>{
  if(!sessionId){setSession(null);setLoading(false);return;}
  if(loadedSession.current===sessionId)return;
  const revision=++generation.current;
  setSession(null);setLoading(true);setError('');
  getHomeworkAISession(sessionId).then(value=>{
   if(generation.current===revision){loadedSession.current=sessionId;setSession(value);}
  }).catch(e=>{if(generation.current===revision)setError(errorText(e));})
   .finally(()=>{if(generation.current===revision)setLoading(false);});
  return()=>{generation.current++;};
 },[sessionId]);
 useEffect(()=>{messagesEnd.current?.scrollIntoView?.({block:'end',behavior:'instant'});},[session?.messages.length,submitted]);

 function chooseCourse(value:string){setCourseId(value);setConfirmed(false);question.current?.focus();}
 function start(){
  if(sending.current || locked || photoBusy || pendingRequest.current)return;
  generation.current++;loadedSession.current=null;
  setSessionId('');setSession(null);setLoading(false);setCourseId(index.courses.some(course=>course.id===initialCourseId)?initialCourseId!: '');
  setMessage('');setImages([]);setConfirmed(false);setError('');setUncertain(false);
  question.current?.focus();
 }
 function chooseConversation(id:string){
  if(locked || photoBusy)return;
  if(id===sessionId && session)return;
  loadedSession.current=null;setSessionId(id);setSession(null);setLoading(true);
  setCourseId('');setMessage('');setImages([]);setConfirmed(false);setError('');
 }
 async function refresh(){
  if(sending.current)return;
  sending.current=true;setBusy(true);setError('');
  try{
   const next=await homeworkAIIndex(schoolId);setIndex(next);
   const saved=pendingRequest.current;
   let id=sessionId;
   if(saved?.kind==='create'){
    const found=next.sessions.find(item=>item.idempotency_key===saved.key);
    if(!found)throw new Error('The conversation request has not been confirmed. No additional request was sent.');
    id=found.id;remember(null);
   }
   if(saved?.kind==='message')id=saved.session_id || id;
   if(id){
    const recorded=await getHomeworkAISession(id);
    generation.current++;loadedSession.current=id;setSession(recorded);setSessionId(id);setLoading(false);
    if(saved?.kind==='message'){
     const receipt=recorded.messages.find(item=>item.idempotency_key===saved.key);
     if(!receipt || receipt.status==='pending')throw new Error('Your question is recorded or awaiting confirmation. No additional question was sent.');
     remember(null);setMessage('');setImages([]);setConfirmed(false);
    }
   }
   setUncertain(false);
  }catch(e){setUncertain(true);setError(errorText(e));}
  finally{sending.current=false;setBusy(false);}
 }
 async function more(){
  if(sending.current || locked || !index.next_cursor)return;
  sending.current=true;setBusy(true);setError('');
  try{const next=await homeworkAIIndex(schoolId,index.next_cursor);setIndex(current=>({...next,sessions:mergeHomeworkAIPages(current.sessions,next.sessions)}));}
  catch(e){setError(errorText(e));}finally{sending.current=false;setBusy(false);}
 }
 async function addPhotos(files:FileList|null){
  if(!files?.length || locked)return;
  if(images.length+files.length>2){setError('Choose up to two homework photos.');return;}
  setPhotoBusy(true);setConfirmed(false);setError('');
  try{const prepared:CuratedImage[]=[];for(const file of Array.from(files))prepared.push(await prepareAssignmentImage(file));setImages(current=>[...current,...prepared].slice(0,2));}
  catch(e){setError(errorText(e));}finally{setPhotoBusy(false);}
 }
 const input:HomeworkAIInput={message,...homeworkAIContext(courseId),images,image_use_confirmed:images.length>0 && confirmed};
 async function ask(event:FormEvent){
  event.preventDefault();
  if(sending.current || locked || photoBusy || pendingRequest.current || homeworkAIRequestProblem(input,assistant))return;
  sending.current=true;setBusy(true);setError('');setSubmitted({message,photoCount:images.length});
  try{
   let current=session;
   if(!current){
    const createKey=crypto.randomUUID();remember({kind:'create',key:createKey,session_id:null});
    const value=await createHomeworkAISession(schoolId,createKey);
    remember(null);current={session:value,messages:[]};
    loadedSession.current=value.id;setSession(current);setSessionId(value.id);
    setIndex(previous=>({...previous,sessions:[value,...previous.sessions.filter(item=>item.id!==value.id)]}));
   }
   const id=current.session.id,key=crypto.randomUUID();remember({kind:'message',key,session_id:id});
   const reply=await sendHomeworkAIMessage(id,input,key);
   const updated=homeworkConversationWithReply(current,reply);
   setSession(previous=>previous?.session.id===id?homeworkConversationWithReply(previous,reply):previous);
   setIndex(previous=>({...previous,sessions:previous.sessions.map(item=>item.id===id?updated.session:item)}));
   if(reply.status==='pending')setUncertain(true);
   else{remember(null);setMessage('');setImages([]);setConfirmed(false);}
  }catch(e){setUncertain(true);setError(`${errorText(e)} Check recorded history before continuing. Your question will not be sent again automatically.`);}
  finally{sending.current=false;setBusy(false);setSubmitted(null);}
 }

 return <div className={`homework-chat${compact?' homework-embedded':''}`}>
  {compact?<div className="classroom-help-heading"><div><h2>Homework Help</h2><p>Ask a learning question, with or without a class.</p></div></div>:<PageHead title="Homework Help" description="Ask about anything you’re learning. No course enrollment required."/>}
  <Notice error={error}/>
  <div className="homework-layout homework-chat-layout">
   <aside className="panel homework-sidebar homework-chat-sidebar" aria-label="Conversation history">
    <div className="homework-chat-sidebar-heading"><h2>Conversations</h2><MessageSquareText size={17}/></div>
    <button type="button" className="button secondary homework-new-conversation" disabled={locked || photoBusy} onClick={start}><Plus size={16}/>New conversation</button>
    {index.sessions.length?<nav aria-label="Homework conversations">{index.sessions.map(item=><button key={item.id} type="button" className={item.id===sessionId?'selected':''} disabled={locked || photoBusy} aria-current={item.id===sessionId?'true':undefined} onClick={()=>chooseConversation(item.id)}><strong>{homeworkConversationTitle(item)}</strong><small>{stamp(item.created_at)}</small></button>)}</nav>:<p className="help-text homework-history-empty">Your conversations will appear here after your first question.</p>}
    {index.next_cursor && <button type="button" className="text-button homework-load-more" disabled={locked || photoBusy} onClick={()=>void more()}>Load older conversations</button>}
    <div className="homework-sidebar-note"><small>Conversations and homework photos are saved privately for authorized school review. You write your own submitted work.</small></div>
   </aside>
   <section className="panel homework-conversation homework-chat-conversation" aria-label="Homework conversation">
    <div className="panel-head"><div><h2>{session?homeworkConversationTitle(session.session):'New conversation'}</h2><p>{title} · AI study partner</p></div><button type="button" className="text-button" disabled={busy || loading} onClick={()=>void refresh()}><RefreshCw size={14}/>Refresh history</button></div>
    {!assistant.available && <div className="homework-pending" role="status"><strong>Homework Help is not ready for new questions at this school.</strong><p>{homeworkAssistantSetupMessage(assistant)} You do not need to enroll in a class. Your recorded conversations remain available while you have access.</p></div>}
    <div className="homework-chat-scroll" aria-busy={loading || busy}>
     {loading?<Spinner text="Loading your conversation…"/>:<div className="homework-messages" aria-label="Your homework conversation" aria-live="polite" aria-relevant="additions">
      {!session?.messages.length && !submitted?<div className="homework-triage-intro"><span className="homework-intro-icon"><MessageSquareText size={24}/></span><h3>What would you like to understand?</h3><p>Type a question or add a homework photo. Your assistant can help on its own and bring in a relevant approved AI teacher when one is available.</p><p className="help-text">You can ask about any subject, even if you are not enrolled in a course.</p></div>:session?.messages.map(item=><HomeworkAIMessageView key={item.id} item={item} assistantName={title} paused={locked} choose={chooseCourse} openCourse={openCourse}/>)}
      {submitted && <div className="homework-message homework-submitted" role="status"><div className="homework-student-message"><small>You</small>{submitted.message && <p className="prose">{submitted.message}</p>}{submitted.photoCount>0 && <p className="help-text">{submitted.photoCount} homework photo{submitted.photoCount===1?'':'s'} attached</p>}</div><Spinner text="Working on your question…"/></div>}
      <div ref={messagesEnd}/>
     </div>}
    </div>
    {(uncertain || pending) && <div className="homework-pending" role="status"><strong>Your request is recorded or awaiting confirmation.</strong><p>Refresh history to check its result. We will not repeat a request automatically, including after you reload this page.</p></div>}
    <form className="homework-composer homework-chat-composer" onSubmit={ask}>
     <fieldset disabled={locked || photoBusy || !assistant.available}>
      <legend className="sr-only">Ask a learning question</legend>
      <label className="homework-question-label" htmlFor={`homework-question-${compact?'embedded':'main'}`}>Your question</label>
      <textarea ref={question} id={`homework-question-${compact?'embedded':'main'}`} rows={3} maxLength={limit} value={message} onChange={e=>setMessage(e.target.value)} placeholder="Ask a question, explain where you’re stuck, or attach a homework photo…" aria-describedby={`homework-question-note-${compact?'embedded':'main'}`}/>
      {images.length>0 && <div className="curation-photos" aria-label="Photos attached to your question">{images.map((image,position)=><figure key={`${image.sha256}:${position}`}><img src={image.data_url} alt={`Homework photo ${position+1}: ${image.name}`}/><figcaption><span>{image.name}</span><button type="button" className="icon-button" aria-label={`Remove ${image.name}`} onClick={()=>{setImages(current=>current.filter((_,i)=>i!==position));setConfirmed(false);}}><X size={16}/></button></figcaption></figure>)}</div>}
      {images.length>0 && <label className="checkbox-label curation-photo-consent"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>I want these photos sent to the school-approved {assistant.photo_model_labels?.triage} question assistant, {assistant.photo_model_labels?.tutor} teacher if needed, and {assistant.photo_model_labels?.safety} safety reviewer. Photos are retained privately for authorized review.</label>}
      <div className="homework-chat-composer-actions"><label className="teacher-photo-picker"><ImagePlus size={17}/><span>Add photos</span><input type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={images.length>=2 || !policy?.allow_images} onChange={e=>{const files=e.target.files;void addPhotos(files);e.target.value='';}}/></label><span className="homework-character-count">{photoBusy?'Preparing photos…':`${message.length.toLocaleString()} / ${limit.toLocaleString()}`}</span><button className="button primary" disabled={!!homeworkAIRequestProblem(input,assistant)}><Send size={16}/>{busy?'Sending…':'Send'}</button></div>
      {index.courses.length>0 && <details className="homework-context"><summary>Course context{courseId?' · selected':' · optional'}</summary><HomeworkAICoursePicker courses={index.courses} value={courseId} change={chooseCourse}/></details>}
     </fieldset>
     <p id={`homework-question-note-${compact?'embedded':'main'}`} className="help-text homework-chat-disclosure">AI can make mistakes. Check important claims. Your conversation is saved for authorized school review.{policy?.allow_images?' Up to two PNG, JPEG or WebP photos, 5 MB each.':' Photos are not enabled at this school yet.'}</p>
    </form>
   </section>
  </div>
 </div>;
}

export function releasedHomeworkAIAnswer(item:HomeworkAIMessage):string|null{return ['released','clarify'].includes(item.status) && typeof item.answer==='string' && item.answer.trim() && item.answer.length<=30000?item.answer:null;}

export function HomeworkAIMessageView({item,assistantName='Homework assistant',paused=false,choose,openCourse}:{item:HomeworkAIMessage;assistantName?:string;paused?:boolean;choose:(id:string)=>void;openCourse:(target:HomeworkTarget)=>void}) {
 const answer=releasedHomeworkAIAnswer(item),triage=item.triage,responder=homeworkResponder(item,assistantName);
 return <article className="homework-message">
  <div className="homework-student-message"><small>You · {stamp(item.created_at)}</small>{item.message && <p className="prose">{item.message}</p>}{item.images.length>0 && <HomeworkAIPhotos messageId={item.id} images={item.images}/>}</div>
  <div className="homework-handoff"><span className="homework-agent-avatar"><MessageSquareText size={18}/></span><div><strong>{responder.name}</strong><small className="homework-responder-description">{responder.description}{triage.course_name?` · ${triage.course_name}`:triage.topic?` · ${triage.topic}`:''}</small>{item.status!=='released' && <Badge tone={item.status==='pending'?'amber':''}>{item.status==='clarify'?'Follow-up question':item.status}</Badge>}{triage.prompt && triage.prompt!==answer && <p>{triage.prompt}</p>}{item.status==='clarify' && triage.candidates.length>0 && <div className="homework-course-options" aria-label="Choose context for your next question">{triage.candidates.map(course=><button className="button secondary" type="button" disabled={paused} key={course.id} onClick={()=>choose(course.id)}>{course.name}<ArrowRight size={14}/></button>)}</div>}</div></div>
  {answer?<><div className="homework-ai-answer prose" aria-label={item.status==='clarify'?'AI clarification':'AI explanation'}>{answer}</div>{item.citations.length>0 && <section className="homework-ai-sources" aria-label="Sources used for this explanation"><h4>Course sources</h4><ul>{item.citations.map((citation,index)=><li key={`${citation.document_id}:${index}`}>{citation.title}</li>)}</ul><p className="help-text">These source references were supplied by the school. The explanation itself was generated by AI.</p></section>}{item.status==='released' && <StudentFeedback turnId={item.id} paused={paused}/>}</>:item.status!=='clarify' && <p className="help-text">{item.error || 'No explanation was released. Refresh history to check the request’s status.'}</p>}
  {answer && item.references.length>0 && <section className="homework-references" aria-label="Related coursework"><h4><BookOpen size={15}/>Continue in your coursework</h4>{item.references.map(reference=>{const target=homeworkReferenceTarget(reference,triage.course_id);return target?<button type="button" className="text-button" key={`${reference.kind}:${reference.id}`} disabled={paused} onClick={()=>openCourse(target)}>{reference.title}<ArrowRight size={14}/></button>:null;})}</section>}
 </article>;
}

export function HomeworkAIPhotos({messageId,images}:{messageId:string;images:HomeworkAIMessage['images']}) {
 const [shown,setShown]=useState(false);
 return <section className="homework-saved-photos"><button className="text-button" type="button" aria-expanded={shown} onClick={()=>setShown(value=>!value)}><ImagePlus size={14}/>{shown?'Hide homework photos':`View ${images.length} homework photo${images.length===1?'':'s'}`}</button>{shown && <div className="curation-photos">{images.map((image,index)=>{const url=privateHomeworkAIImageUrl(messageId,image.url);return <figure key={`${image.id}:${index}`}>{url && <img src={url} alt={`Your retained homework photo: ${image.name}`}/>}<figcaption>{image.name}{!url && ' · Photo unavailable'}</figcaption></figure>;})}</div>}</section>;
}

export function HomeworkAICoursePicker({courses,value,change}:{courses:HomeworkAIIndex['courses'];value:string;change:(value:string)=>void}) {
 return <label>Course context · optional<select value={value} onChange={e=>change(e.target.value)}><option value="">Find relevant context automatically</option><option value="__general__">General learning · no course context</option>{courses.map(course=><option value={course.id} key={course.id}>{course.name}</option>)}</select><small>You can ask without a course. Your enrolled classes, including classes with human teachers, are optional context for your next question.</small></label>;
}
