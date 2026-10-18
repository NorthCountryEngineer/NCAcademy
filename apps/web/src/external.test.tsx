import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AuditAgent, AutomaticPublicationPolicy, ExternalReviewQueue } from './AuditAgent';
import { ExternalApprovalControls, ExternalFindings, ExternalResourceCard, ExternalResourceEditor, ExternalResources, ReviewedVideo } from './ExternalResources';
import { approveExternal, auditAgentPayload, blankAuditAgent, blankExternal, externalPayload, externalReviewIdentity, importCorpus, listExternal, requestPlayback, reviewedEmbed, reviewExternal, revokeExternal, saveAuditAgent, saveExternal, type ExternalResource } from './external-api';
import type { School } from './api';
import { CuratedLessonContent, CuratedLessonView } from './CuratedLessons';
import type { CuratedLessonBundle, CuratedTurn } from './curated-api';
afterEach(()=>vi.unstubAllGlobals());
const item=():ExternalResource=>({id:'resource-a',resource_id:'resource-a',course_id:'course-a',kind:'youtube',title:'Reviewed carbon lesson',source_url:'https://www.youtube.com/watch?v=AbCdEfG123_',version:3,snapshot_sha256:'a'.repeat(64),status:'approved',approved:true,teaching_notes:'Carbon moves among reservoirs.',transcript:'PRIVATE_TRANSCRIPT',review:{id:'review-a',verdict:'approve',risk_codes:[],findings:[],coverage:'supplied_text',created_at:'2026-10-18T12:00:00Z'},can_edit:true});
const embed='https://www.youtube-nocookie.com/embed/AbCdEfG123_?controls=1&autoplay=0&rel=0&playsinline=1&origin=https%3A%2F%2Fncacademy.app&start=10&end=50';
const school:School={id:'school-a',name:'School A',description:'',core_resource_id:null,role:'school',effective_role:'school',capabilities:{manage_school:true,manage_members:true,manage_agents:true,manage_resources:true,create_course:true,approve_changes:true,manage_connections:true}};

describe('explicit reviewed YouTube playback',()=>{
 it('renders no third-party request, iframe, thumbnail, prefetch or playable link before explicit acknowledgement and load',()=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);const html=renderToStaticMarkup(<ReviewedVideo resource={{resource_id:'resource-a',course_id:'course-a',title:'Carbon lesson'}} schoolId="school-a"/>);
  for(const text of ['Carbon lesson','Video is not loaded','network and browser information','Load reviewed video','disabled=""'])expect(html).toContain(text);
  for(const text of ['<iframe','<img','youtube.com','youtube-nocookie.com','href=','preload','prefetch'])expect(html).not.toContain(text);expect(fetcher).not.toHaveBeenCalled();
 });
 it('requests authenticated current playback using only a resource ID and no client-chosen embed URL',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({embed_url:embed,review_only:false}),{status:200}));vi.stubGlobal('fetch',fetcher);
  await requestPlayback('video / a','school-b');const [url,options]=fetcher.mock.calls[0];expect(url).toBe('/api/external-resources/video%20%2F%20a/playback?school_id=school-b');expect(options.method).toBe('POST');expect(options.credentials).toBe('same-origin');expect(JSON.parse(options.body)).toEqual({});
 });
 it('does not retry unavailable or stale playback automatically',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Source changed; review again'}),{status:409}));vi.stubGlobal('fetch',fetcher);await expect(requestPlayback('resource-a','school-a')).rejects.toThrow('Source changed');expect(fetcher).toHaveBeenCalledTimes(1);});
 it('accepts only the generated privacy-enhanced embed, no autoplay and exact reviewed segment',()=>{expect(reviewedEmbed(embed,'https://ncacademy.app')).toBe(embed);});
 it('rejects arbitrary hosts, encoded/traversal paths, executable URLs, credentials, duplicate or unknown params and altered playback controls',()=>{
  const invalid=['javascript:alert(1)',embed.replace('www.youtube-nocookie.com','www.youtube.com'),embed.replace('www.youtube-nocookie.com','www.youtube-nocookie.com.evil.test'),embed.replace('https://','https://user:pass@'),embed.replace('/embed/','/path/../embed/'),embed.replace('AbCdEfG123_','%41bCdEfG123_'),embed.replace('autoplay=0','autoplay=1'),embed.replace('controls=1','controls=0'),embed+'&student_id=PRIVATE',embed+'&autoplay=0',embed+'#private',embed.replace('start=10','start=1.5'),embed.replace('end=50','end=9'),embed.replace('end=50','end=999999'),embed.replace('origin=https%3A%2F%2Fncacademy.app','origin=https%3A%2F%2Fevil.test')];
  for(const value of invalid)expect(reviewedEmbed(value,'https://ncacademy.app'),value).toBeNull();
 });
});

describe('teacher and student external source presentation',()=>{
 it('never displays a pending, stale or revoked source to students even in a malformed list',()=>{for(const status of ['pending','stale','revoked','rejected','needs_human'] as const){expect(renderToStaticMarkup(<ExternalResourceCard resource={{...item(),status,title:'PRIVATE_UNREVIEWED'}} schoolId="school-a"/>)).toBe('');}});
 it('student video cards show only reviewed public fields, never transcript, private review findings or source links',()=>{
  const html=renderToStaticMarkup(<ExternalResourceCard resource={{...item(),review:{...item().review!,findings:['PRIVATE_FINDING']}}} schoolId="school-a"/>);expect(html).toContain('Reviewed carbon lesson');expect(html).not.toContain('PRIVATE_TRANSCRIPT');expect(html).not.toContain('PRIVATE_FINDING');expect(html).not.toContain('youtube.com');expect(html).not.toContain('Review evidence');expect(html).not.toContain('<iframe');
 });
 it('shows staff evidence actions while keeping pending video unembedded',()=>{const html=renderToStaticMarkup(<ExternalResourceCard resource={{...item(),status:'pending'}} schoolId="school-a" staff open={()=>{}} edit={()=>{}}/>);expect(html).toContain('pending');expect(html).toContain('Review evidence');expect(html).toContain('Edit source');expect(html).not.toContain('<iframe');});
 it('escapes approved text and never creates credential-bearing or executable attribution links',()=>{
  for(const source_url of ['javascript:alert(1)','https://user:secret@example.test/path','https://www.youtube.com/watch?v=AbCdEfG123_','https://youtu.be/AbCdEfG123_','https://www.youtube-nocookie.com/embed/AbCdEfG123_']){const html=renderToStaticMarkup(<ExternalResourceCard resource={{...item(),kind:'external_text',source_url,content:'<script>external()</script>'}} schoolId="school-a"/>);expect(html).not.toContain('href=');expect(html).not.toContain('secret');expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');}
 });
 it('teacher form collects permitted supplied evidence, explicit segments and rights without scraping or student content inputs',()=>{
  const html=renderToStaticMarkup(<ExternalResourceEditor courseId="course-a" schoolId="school-a" close={()=>{}} saved={async()=>{}}/>);for(const text of ['YouTube video link','Supplied transcript','not automatically downloaded','Start at seconds','End at seconds','I am permitted','private review candidate','Do not include student work'])expect(html).toContain(text);for(const text of ['type="file"','api_key','system_prompt','<iframe'])expect(html).not.toContain(text);
 });
 it('does not run audit, playback or provider requests during initial resource/admin rendering',()=>{const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);renderToStaticMarkup(<ExternalResources courseId="course-a" schoolId="school-a"/>);renderToStaticMarkup(<AuditAgent school={school}/>);expect(fetcher).not.toHaveBeenCalled();});

 it('requires a current clean result and both human video attestations before offering publication',()=>{
  const props={resource:{...item(),can_approve:true},busy:false,watched:false,complete:false,release:true,changeWatched:()=>{},changeComplete:()=>{},changeRelease:()=>{},approve:()=>{}};
  for(const values of [{watched:false,complete:false},{watched:true,complete:false},{watched:false,complete:true},{watched:true,complete:true,release:false},{watched:true,complete:true,resource:{...item(),can_approve:true,review:{...item().review!,verdict:'reject' as const}}}]){const html=renderToStaticMarkup(<ExternalApprovalControls {...props} {...values}/>);expect(html).toMatch(/<button[^>]*disabled=""/);expect(html).toContain('including audio and visuals');expect(html).toContain('transcript is complete');}
  const html=renderToStaticMarkup(<ExternalApprovalControls {...props} watched complete/>);expect(html).not.toMatch(/<button[^>]*disabled=""/);
 });
 it('renders queue status and trusted verdict without exposing private transcript or initiating review',()=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);const html=renderToStaticMarkup(<ExternalReviewQueue resources={[{...item(),status:'needs_human',review:{...item().review!,verdict:'needs_human'}}]} open={()=>{}}/>);for(const text of ['Reviewed carbon lesson','needs human','Open evidence'])expect(html).toContain(text);expect(html).not.toContain('PRIVATE_TRANSCRIPT');expect(html).not.toContain('<iframe');expect(fetcher).not.toHaveBeenCalled();
 });
 it('keeps curated video sections behind the same explicit player gate and the reviewer renderer inert',()=>{
  const lesson:CuratedLessonBundle={version:'curated-lesson-v1',title:'Curated video lesson',teacher_name:'River',sections:[{id:'section-1',document_id:'external:a',title:'Carbon reservoirs',text:'Carbon moves among reservoirs.',visual:null,citation:{document_id:'external:a',title:'Reviewed carbon source',source_url:'https://www.youtube.com/watch?v=AbCdEfG123_',content_sha256:'c'.repeat(64),excerpt_sha256:'e'.repeat(64),excerpt_start:0,excerpt_end:30},video:{resource_id:'resource-a',course_id:'course-a',title:'Reviewed carbon lesson'},reflection_prompt:'How are the reservoirs connected?'}],takeaway_prompt:'What did you learn?',narration_text:'Carbon moves among reservoirs.'};
  const value:CuratedTurn={id:'turn-a',session_id:'session-a',activity:'curated_lesson',teacher_profile_id:'river',teacher_name:'River',message:'Explain reservoirs.',status:'released',approved_text:null,citations:lesson.sections.map(section=>section.citation),lesson,error:null,created_at:'2026-10-18T12:00:00Z',preferences:{pace:'standard',formats:['read']},images:[]};
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);const student=renderToStaticMarkup(<CuratedLessonView turn={value}/>);expect(student).toContain('Load reviewed video');expect(student).not.toContain('<iframe');expect(student).not.toContain('youtube.com');const review=renderToStaticMarkup(<CuratedLessonContent bundle={lesson}/>);expect(review).not.toContain('Load reviewed video');expect(review).not.toContain('<iframe');expect(fetcher).not.toHaveBeenCalled();
 });
 it('shows a durable withheld review warning without inventing a clean verdict',()=>{
  const html=renderToStaticMarkup(<ExternalFindings resource={{...item(),review:null,latest_review_job:{id:'job-a',status:'withheld',error:'Provider outcome uncertain',uncertainty:'Never automatically retry'}}}/>);for(const text of ['Review withheld','Provider outcome uncertain','Never automatically retry','No current audit agent result'])expect(html).toContain(text);expect(html).not.toContain('No rubric risks reported');
 });
 it('makes automated review coverage limits clear and escapes finding labels',()=>{
  const html=renderToStaticMarkup(<ExternalFindings resource={{...item(),review:{...item().review!,verdict:'needs_human',findings:['<script>finding</script>']}}}/>);for(const text of ['needs human','supplied text only','video bytes','outside this automated review','&lt;script&gt;'])expect(html).toContain(text);expect(html).not.toContain('<script>');
 });
});

describe('review and assignment request boundaries',()=>{
 it('whitelists resource evidence and omits provider overrides and unused transcript types',()=>{
  const value={...blankExternal(),kind:'external_text' as const,source_url:' https://example.test/source ',title:' Reading ',content:' Text ',transcript:'PRIVATE_UNUSED',teaching_notes:' Notes ',rights_confirmed:true,system_prompt:'BYPASS',api_key:'SECRET',embed_url:embed};const payload=externalPayload(value);expect(payload).toEqual({kind:'external_text',source_url:'https://example.test/source',title:'Reading',teaching_notes:'Notes',content:'Text',rights_confirmed:true});expect(JSON.stringify(payload)).not.toContain('BYPASS');expect(JSON.stringify(payload)).not.toContain('SECRET');
 });
 it('keeps approval exact to resource version, agent review and source hash with explicit human video attestations',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response('{}',{status:200}));vi.stubGlobal('fetch',fetcher);await approveExternal(item(),'school-a',true,true);const [url,options]=fetcher.mock.calls[0];expect(url).toBe('/api/external-resources/resource-a/approve?school_id=school-a');expect(JSON.parse(options.body)).toEqual({expected_version:3,review_id:'review-a',snapshot_sha256:'a'.repeat(64),watched_video:true,transcript_complete:true});
 });
 it('keeps uncertain review identity stable and changes it only for another resource/version',()=>{
  const make=vi.fn().mockReturnValueOnce('first').mockReturnValueOnce('second').mockReturnValueOnce('third');const value=externalReviewIdentity(null,item(),make);expect(externalReviewIdentity(value,item(),make)).toBe(value);expect(externalReviewIdentity(value,{id:'other',version:3},make).key).toBe('second');expect(externalReviewIdentity(value,{id:'resource-a',version:4},make).key).toBe('third');
 });
 it('sends one review request, no retries or client-specified model, and scopes edits/revocation/import to class and exact version',async()=>{
  const fetcher=vi.fn().mockImplementation(()=>Promise.resolve(new Response('{}',{status:200})));vi.stubGlobal('fetch',fetcher);
  await reviewExternal(item(),'school-a','same-operation');expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({expected_version:3,idempotency_key:'same-operation'});
  await saveExternal('course-a','school-a',{...blankExternal(),source_url:'https://example.test',rights_confirmed:true},item());expect(fetcher.mock.calls[1][1].method).toBe('PUT');expect(JSON.parse(fetcher.mock.calls[1][1].body).expected_version).toBe(3);
  await revokeExternal(item(),'school-a','  Changed content  ');expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({expected_version:3,reason:'Changed content'});
  await importCorpus('class a','school-a');expect(fetcher.mock.calls[3][0]).toBe('/api/courses/class%20a/external-resources/import-corpus?school_id=school-a');
 });
 it('gets only the selected course student-safe collection',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response('{"resources":[]}',{status:200}));vi.stubGlobal('fetch',fetcher);await listExternal('course a','school-b');expect(fetcher.mock.calls[0][0]).toBe('/api/courses/course%20a/external-resources?school_id=school-b');expect(fetcher.mock.calls[0][1].method).toBe('GET');});
 it('allows an administrator to choose automatic clean-text publication while retaining human video review',()=>{
  const initial=renderToStaticMarkup(<AutomaticPublicationPolicy requireHuman change={()=>{}}/>);expect(initial).toContain('checked=""');expect(initial).not.toContain('disabled=""');const automatic=renderToStaticMarkup(<AutomaticPublicationPolicy requireHuman={false} change={()=>{}}/>);expect(automatic).not.toContain('checked=""');for(const text of ['automatically approve','external-text or corpus-document','YouTube always requires a human','Rejected, uncertain or flagged'])expect(automatic).toContain(text);expect(auditAgentPayload({...blankAuditAgent(),require_human_approval:false},2).require_human_approval).toBe(false);
 });
 it('assigns a bounded named school audit model without keys, roles, grants, or automatic review calls',async()=>{
  const draft={...blankAuditAgent(),connection_id:'school-review-model',model:'review-selector',name:' Audit guide ',api_key:'PRIVATE_SECRET',role:'admin',endpoint:'https://unapproved.test',system_prompt:'OVERRIDE'};const payload=auditAgentPayload(draft,null);expect(payload.expected_version).toBeNull();expect(payload.name).toBe('Audit guide');expect(payload.require_human_approval).toBe(true);for(const text of ['PRIVATE_SECRET','OVERRIDE','unapproved.test','"role"'])expect(JSON.stringify(payload)).not.toContain(text);
  const fetcher=vi.fn().mockResolvedValue(new Response('{}',{status:200}));vi.stubGlobal('fetch',fetcher);await saveAuditAgent('school a',draft,2);expect(fetcher.mock.calls[0][0]).toBe('/api/schools/school%20a/audit-agent');expect(fetcher.mock.calls[0][1].method).toBe('PUT');expect(fetcher).toHaveBeenCalledTimes(1);
 });
});
