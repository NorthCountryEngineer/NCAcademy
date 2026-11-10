import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { BookOpenCheck, RefreshCw, Send } from 'lucide-react';
import { errorText, stamp } from './api';
import { Badge, Empty, Notice, Spinner } from './components';
import { lessonHistory, lessonIdentity, requestLesson, type LessonCitation, type LessonIdentity, type LessonTurn } from './lesson-api';
import type { TeacherCard } from './teacher-api';
import { StudentFeedback } from './StudentFeedback';

export function lessonTeachers(teachers:TeacherCard[],reviewedPractice=false){
 return teachers.filter(teacher=>teacher.kind==='ai'&&(reviewedPractice
  ?teacher.context_ready&&teacher.lesson_mode==='managed'
  :teacher.lesson_tutoring_available===true));
}

type LessonTutorProps={courseId:string;schoolId:string;teachers:TeacherCard[];localPractice?:boolean;practiceLesson?:{id:string;title:string};backToPractice?:()=>void};

export function LessonTutor({courseId,schoolId,teachers,localPractice=false,practiceLesson,backToPractice}:LessonTutorProps){
 const reviewedPractice=localPractice||!!practiceLesson;
 const choices=lessonTeachers(teachers,reviewedPractice);
 const [teacherId,setTeacherId]=useState(choices[0]?.id||''),[message,setMessage]=useState(''),[turns,setTurns]=useState<LessonTurn[]>([]),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const pending=useRef<LessonIdentity|null>(null);
 const load=useCallback(async()=>{setError('');try{setTurns(await lessonHistory(courseId,schoolId));}catch(e){setError(errorText(e));}finally{setLoading(false);}},[courseId,schoolId]);
 useEffect(()=>{void load();},[load]);
 const selected=choices.find(teacher=>teacher.id===teacherId)||choices[0];
 const inputLimit=reviewedPractice?1200:Math.min(10000,Math.max(100,selected?.lesson_max_input_chars||3000));
 async function ask(e:FormEvent){
  e.preventDefault();if(busy||!message.trim()||message.length>inputLimit||!selected)return;
  setBusy(true);setError('');pending.current=lessonIdentity(pending.current,courseId,selected.id,message,undefined,reviewedPractice?{lessonId:practiceLesson?.id}:undefined);
  try{const turn=await requestLesson(pending.current,schoolId);setTurns(current=>[...current.filter(item=>item.id!==turn.id),turn]);if(turn.status!=='pending'){pending.current=null;setMessage('');}}
  catch(e){setError(errorText(e));}finally{setBusy(false);}
 }
 return <section className="panel lesson-qa">
  <div className="panel-head"><div><h2>{reviewedPractice&&selected?`Talk with ${selected.name}`:'Lesson Q&A'}</h2><p>{reviewedPractice?'Ask about a math idea or get help choosing your next step.':'Ask about this class’s reviewed teaching material.'}</p></div><BookOpenCheck size={20}/></div>
  <div className="lesson-boundary"><strong>{practiceLesson?`Lesson: ${practiceLesson.title}`:'Understand the lesson'}</strong><p>This teacher explains reviewed material; it does not write assessed work. Assignment responses belong in your own coursework.</p><small>Your questions, released explanations and source citations are recorded for teacher review.</small>{backToPractice&&<div><button type="button" className="text-button" onClick={backToPractice}>Back to daily practice</button></div>}</div>
  <div className="panel-body"><Notice error={error}/><div className="lesson-history-heading"><h3>{reviewedPractice?'Your conversation':'Your lesson questions'}</h3><button type="button" className="text-button" disabled={busy||loading} onClick={()=>{setLoading(true);void load();}}><RefreshCw size={14}/>Refresh history</button></div>
   {loading?<Spinner text="Loading your lesson history…"/>:!turns.length?<Empty title="Start with a concept">{reviewedPractice?'Try asking how rounding works, or ask to see a math idea in a different way.':'Ask about an idea or source from the lesson, such as how evidence supports an explanation.'}</Empty>:<div className="lesson-transcript" aria-label="Your lesson Q&A history" aria-live="polite">{turns.map(turn=><LessonTurnView key={turn.id} turn={turn} feedback feedbackPaused={busy}/>)}</div>}
   {!choices.length?<div className="lesson-unavailable"><h3>No lesson teacher is available yet</h3><p>{reviewedPractice?'A school administrator must approve a managed teacher and the current class materials.':'A school administrator must approve this teacher’s current profile, model policy and class corpus before Lesson Q&A is available.'}</p>{!reviewedPractice&&teachers.filter(teacher=>teacher.kind==='ai'&&teacher.lesson_unavailable_reason).map(teacher=><p key={teacher.id}><strong>{teacher.name}:</strong> {teacher.lesson_unavailable_reason}</p>)}</div>:<form className="lesson-question-form" onSubmit={ask}>
    <label>Lesson teacher<select value={selected?.id||''} disabled={busy} required onChange={e=>setTeacherId(e.target.value)}>{choices.map(teacher=><option key={teacher.id} value={teacher.id}>{teacher.name} · AI teacher</option>)}</select></label>
    {selected&&<p className="help-text">{selected.title||'Your lesson teacher'} · {reviewedPractice?'Replies use reviewed Grade 3 math references.':'Sources and explanations are limited to this class.'}</p>}
    <label>Your lesson question<textarea required rows={4} maxLength={inputLimit} disabled={busy} value={message} onChange={e=>setMessage(e.target.value)} placeholder={practiceLesson?'What part of this math lesson is confusing?':'Which part of the lesson would you like to understand?'}/></label>
    <div className="action-row"><small className="help-text">{inputLimit.toLocaleString()} characters maximum{selected?.lesson_max_turns?` · ${reviewedPractice?Math.min(24,selected.lesson_max_turns):selected.lesson_max_turns} lesson requests per day`:''}.</small><button className="button primary" disabled={busy||loading||!selected||!message.trim()||message.length>inputLimit}><Send size={15}/>{busy?'Requesting explanation…':reviewedPractice&&selected?`Ask ${selected.name}`:'Ask lesson teacher'}</button></div>
    <p className="help-text">{reviewedPractice?'This chat uses reviewed examples and guiding questions. It does not send your practice answers to an AI provider.':'Daily request and token reservation caps reset at midnight UTC. Changing teachers does not reset your class quota. Pending requests remain pending after the daily reset. If a request is uncertain or pending, refresh its recorded history. Retrying the unchanged question with the same teacher reuses the request identity.'}</p>
   </form>}
  </div>
 </section>;
}

export function LessonTurnView({turn,feedback=false,feedbackPaused=false}:{turn:LessonTurn;feedback?:boolean;feedbackPaused?:boolean}){
 const released=turn.status==='released'&&!!turn.approved_text&&turn.citations.length>0;
 const status=turn.status==='released'&&!released?'Withheld':{pending:'Pending',blocked:'Refused',released:'Released',withheld:'Withheld'}[turn.status]||'Unavailable';
 const fallback=turn.status==='pending'?'This request is still pending. Refresh history to check its recorded result.':turn.status==='blocked'?'No explanation was released because this question could not be answered within the lesson boundaries.':'No explanation was released for this request.';
 return <article className="lesson-turn"><div className="lesson-student-message"><small>You · {stamp(turn.created_at)}</small><p className="prose">{turn.message}</p></div><div className="lesson-teacher-message"><div className="section-heading"><strong>{turn.teacher_name||'Lesson teacher'} <span className="muted">· AI teacher</span></strong><Badge tone={released?'green':turn.status==='pending'?'amber':''}>{status}</Badge></div>{released?<><p className="prose">{turn.approved_text}</p>{turn.citations.length>0?<section className="lesson-citations" aria-label="Sources for this explanation"><h4>Sources</h4>{turn.citations.map((citation,index)=><LessonSource key={`${citation.document_id}-${index}`} citation={citation}/>)}</section>:<p className="help-text">No source citation was recorded for this explanation.</p>}</>:<p className="help-text">{turn.error||fallback}</p>}</div>{released&&feedback&&<StudentFeedback turnId={turn.id} paused={feedbackPaused}/>}</article>;
}
export function LessonSource({citation}:{citation:LessonCitation}){
 let url:string|null=null,videoSource=false;try{const parsed=new URL(citation.source_url||'');videoSource=['youtube.com','www.youtube.com','m.youtube.com','youtu.be','youtube-nocookie.com','www.youtube-nocookie.com'].includes(parsed.hostname);if(!videoSource&&parsed.protocol==='https:'&&!parsed.username&&!parsed.password)url=citation.source_url!;}catch{/* Non-web class materials retain their source title. */}
 return <div className="lesson-citation"><p>{url?<a href={url} target="_blank" rel="noopener noreferrer">{citation.title}</a>:<strong>{citation.title}</strong>}{citation.source_page!==null&&citation.source_page!==undefined&&<span> · page {citation.source_page}</span>}</p>{videoSource&&<p className="help-text">Open this video through Reviewed resources.</p>}<details><summary>Source verification</summary><dl className="compact-facts"><div><dt>Source fingerprint</dt><dd><code>{citation.content_sha256}</code></dd></div><div><dt>Quoted excerpt fingerprint</dt><dd><code>{citation.excerpt_sha256}</code></dd></div><div><dt>Excerpt characters</dt><dd>{citation.excerpt_start}–{citation.excerpt_end}</dd></div></dl></details></div>;
}
