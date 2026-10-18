import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { LessonSource, LessonTutor, LessonTurnView, lessonTeachers } from './LessonTutor';
import { TeacherTutorPolicyFields, TeacherTutorPolicySummary } from './TeacherTutorPolicy';
import { teacherProfileLabel } from './Teachers';
import { lessonHistory, lessonIdentity, requestLesson, type LessonTurn } from './lesson-api';
import { blankTeacher, blankTutorModel, teacherMetadata, type TeacherCard } from './teacher-api';
afterEach(()=>vi.unstubAllGlobals());
const card=(id='River'):TeacherCard=>({id,kind:'ai',name:id,title:'Environmental science teacher',pronouns:'they/them',biography:'Learn from evidence.',picture:null,context_ready:true,available_for_tutoring:false,lesson_tutoring_available:true,lesson_mode:'managed',lesson_max_input_chars:500,lesson_max_turns:4});
const turn=():LessonTurn=>({id:'turn-a',session_id:'session-a',teacher_profile_id:'river',teacher_name:'River',message:'How does the carbon cycle connect plants and air?',status:'released',approved_text:'From the reviewed source: Plants take up carbon dioxide during photosynthesis.',citations:[{document_id:'carbon',title:'NASA carbon cycle',source_url:'https://science.nasa.gov/earth/carbon/',source_page:12,content_sha256:'c'.repeat(64),excerpt_sha256:'e'.repeat(64),excerpt_start:40,excerpt_end:112}],error:null,usage:{prompt_tokens:30,completion_tokens:10,total_tokens:40},created_at:'2026-10-18T12:00:00Z'});

describe('lesson question request boundaries',()=>{
 it('retains an uncertain request identity and separates course, teacher and question changes',()=>{
  const make=vi.fn().mockReturnValueOnce('first').mockReturnValueOnce('second').mockReturnValueOnce('third').mockReturnValueOnce('fourth');
  const initial=lessonIdentity(null,'class-a','river','Explain this concept',make);expect(lessonIdentity(initial,'class-a','river','Explain this concept',make)).toBe(initial);
  expect(lessonIdentity(initial,'class-b','river','Explain this concept',make).key).toBe('second');expect(lessonIdentity(initial,'class-a','other','Explain this concept',make).key).toBe('third');expect(lessonIdentity(initial,'class-a','river','A new question',make).key).toBe('fourth');
 });
 it('sends no assignment draft, system instructions, citation selection or provider configuration',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(turn()),{status:200}));vi.stubGlobal('fetch',fetcher);
  await requestLesson({...lessonIdentity(null,'class a','river','Question',()=> 'same-request'),draft:'PRIVATE_DRAFT',system_prompt:'BYPASS',model:'UNAPPROVED'} as ReturnType<typeof lessonIdentity>,'school-a');
  const [url,options]=fetcher.mock.calls[0];expect(url).toBe('/api/courses/class%20a/teacher-tutor?school_id=school-a');expect(JSON.parse(options.body)).toEqual({teacher_profile_id:'river',message:'Question',idempotency_key:'same-request'});expect(JSON.stringify(options)).not.toContain('PRIVATE_DRAFT');
 });
 it('does not automatically retry a failed request and reads only the selected class history',async()=>{
  const fetcher=vi.fn().mockRejectedValueOnce(new TypeError('Network lost')).mockResolvedValueOnce(new Response('[]',{status:200}));vi.stubGlobal('fetch',fetcher);
  await expect(requestLesson(lessonIdentity(null,'class-a','river','Question',()=> 'same-request'),'school-a')).rejects.toThrow('Network lost');expect(fetcher).toHaveBeenCalledTimes(1);
  await lessonHistory('class-b','school-b');expect(fetcher.mock.calls[1][0]).toBe('/api/courses/class-b/teacher-tutor?school_id=school-b');expect(fetcher.mock.calls[1][1].method).toBe('GET');
 });
});

describe('student lesson presentation',()=>{
 it('uses server lesson availability for managed and Core-bound teachers without inferring authority',()=>{
  const cards=[card(),{...card('External'),lesson_mode:'core_agent' as const},{...card('Assignment only'),lesson_tutoring_available:false,available_for_tutoring:true},{...card('Human'),kind:'human' as const}];expect(lessonTeachers(cards).map(item=>item.name)).toEqual(['River','External']);
 });
 it('separates lesson questions from assessed work and applies the safe card question limit',()=>{
  const html=renderToStaticMarkup(<LessonTutor courseId="class-a" schoolId="school-a" teachers={[card()]}/>);for(const text of ['Lesson Q&amp;A','does not write assessed work','Your lesson question','maxLength="500"','4 lesson requests per day','midnight UTC','Changing teachers does not reset your class quota','River'])expect(html).toContain(text);expect(html).not.toContain('<label>Assignment response');expect(html).not.toContain('Save draft');expect(html).not.toContain('Submit work');
 });
 it('renders released source text, teacher identity and exact citation provenance while ignoring hidden fields',()=>{
  const value={...turn(),response:'PRIVATE_PROVIDER_PROSE',prompt:'PRIVATE_PROMPT',teacher_context_snapshot:{contact_email:'PRIVATE_CONTACT',boundaries:['PRIVATE_BEHAVIOR']}};
  const html=renderToStaticMarkup(<LessonTurnView turn={value}/>);for(const text of ['River','Plants take up carbon dioxide','NASA carbon cycle','page 12','Source verification','c'.repeat(64),'e'.repeat(64),'40–112'])expect(html).toContain(text);for(const privateText of ['PRIVATE_PROVIDER_PROSE','PRIVATE_PROMPT','PRIVATE_CONTACT','PRIVATE_BEHAVIOR'])expect(html).not.toContain(privateText);
 });
 it('withholds all explanation text and citations for refused, pending and withheld results',()=>{
  for(const status of ['pending','blocked','withheld'] as const){const html=renderToStaticMarkup(<LessonTurnView turn={{...turn(),status,approved_text:'DO_NOT_RELEASE',error:'Recorded boundary response'}}/>);expect(html).toContain('Recorded boundary response');expect(html).not.toContain('DO_NOT_RELEASE');expect(html).not.toContain('NASA carbon cycle');}
 });
 it('does not display uncited text even if a malformed result claims release, and escapes authored messages',()=>{
  const html=renderToStaticMarkup(<LessonTurnView turn={{...turn(),citations:[],approved_text:'UNCITED_TEXT',message:'<script>unsafe()</script>'}}/>);expect(html).not.toContain('UNCITED_TEXT');expect(html).toContain('Withheld');expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');
 });
 it('keeps YouTube citations behind authenticated Reviewed resources playback, including legacy Q&A citations',()=>{
  for(const source_url of ['https://www.youtube.com/watch?v=AbCdEfG123_','https://youtube.com/watch?v=AbCdEfG123_','https://m.youtube.com/watch?v=AbCdEfG123_','https://youtu.be/AbCdEfG123_','https://www.youtube-nocookie.com/embed/AbCdEfG123_','https://youtube-nocookie.com/embed/AbCdEfG123_']){
   const citation={...turn().citations[0],source_url};const html=renderToStaticMarkup(<LessonSource citation={citation}/>);expect(html).toContain('NASA carbon cycle');expect(html).toContain('Open this video through Reviewed resources.');expect(html).not.toContain('href=');expect(html).not.toContain('<iframe');
   const qa=renderToStaticMarkup(<LessonTurnView turn={{...turn(),citations:[citation]}}/>);expect(qa).toContain('Open this video through Reviewed resources.');expect(qa).not.toContain('href=');
  }
 });
 it('never creates an executable or credential-bearing source link',()=>{
  for(const url of ['javascript:alert(1)','https://username:password@example.test/source']){const html=renderToStaticMarkup(<LessonSource citation={{...turn().citations[0],source_url:url}}/>);expect(html).not.toContain('href=');expect(html).not.toContain('password');expect(html).toContain('NASA carbon cycle');}
 });
});

describe('reviewed profile model policy',()=>{
 it('labels unbound personas as managed and keeps optional external agents distinct',()=>{expect(teacherProfileLabel({kind:'ai',principal_id:null,agent_placement_id:null})).toBe('Managed AI teacher');expect(teacherProfileLabel({kind:'ai',principal_id:'principal',agent_placement_id:'placement'})).toBe('External AI teacher');});
 it('keeps lesson configuration absent by default and whitelists bounded policy fields',()=>{
  expect(teacherMetadata(blankTeacher()).tutor_model).toBeNull();const policy={...blankTutorModel(),connection_id:'school-model',model:'selector',api_key:'PRIVATE_KEY',endpoint:'UNAPPROVED',system_prompt:'BYPASS',enabled:true};const payload=teacherMetadata({...blankTeacher(),kind:'ai',tutor_model:policy});expect(payload.tutor_model).toEqual({connection_id:'school-model',model:'selector',max_turns:8,max_input_chars:3000,max_output_tokens:80,max_total_tokens:20000,allow_lesson_curation:false,allow_assignment_images:false,curation_max_output_tokens:700});expect(JSON.stringify(payload)).not.toContain('PRIVATE_KEY');expect(JSON.stringify(payload)).not.toContain('BYPASS');expect(teacherMetadata({...blankTeacher(),tutor_model:policy}).tutor_model).toBeNull();
 });
 it('shows named policy and all approval limits without exposing connection IDs or live switches',()=>{
  const policy={...blankTutorModel(),connection_id:'PRIVATE_CONNECTION_ID',model:'approved-selector'};
  const review=renderToStaticMarkup(<TeacherTutorPolicySummary policy={policy} connectionName="School learning model"/>);for(const text of ['School learning model','approved-selector','Daily lesson requests','Maximum question characters','Maximum selector output tokens','Daily token reservation limit','20000','does not write assessed work','midnight UTC','Changing teachers does not reset'])expect(review).toContain(text);expect(review).not.toContain('PRIVATE_CONNECTION_ID');
  const form=renderToStaticMarkup(<TeacherTutorPolicyFields schoolId="school-a" value={null} change={()=>{}}/>);expect(form).toContain('Configure a lesson tutoring policy');expect(form).toContain('not shown');expect(form).not.toContain('type="password"');expect(form).not.toContain('Enable live models');
 });
});
