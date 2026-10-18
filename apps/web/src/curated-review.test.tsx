import {describe,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {CuratedEvidence,LearningTrail} from './SessionAudit';

const bundle={version:'curated-lesson-v1',title:'Your course lesson',teacher_name:'River',sections:[{id:'section-1',document_id:'carbon',title:'Carbon evidence',text:'Photosynthesis moves carbon from air into living organisms.',citation:{document_id:'carbon',title:'NASA Carbon Cycle',source_url:'https://science.nasa.gov/earth/earth-observatory/the-carbon-cycle/',content_sha256:'a'.repeat(64),excerpt_sha256:'b'.repeat(64),excerpt_start:0,excerpt_end:70},visual:null,reflection_prompt:'Explain the evidence in your own words.'}],takeaway_prompt:'What do you understand better?',narration_text:'Photosynthesis moves carbon from air into living organisms.'};

describe('curated lesson reviewer evidence',()=>{
 it('renders the released source lesson without student response or automatic photo fetching',()=>{
  const html=renderToStaticMarkup(<CuratedEvidence turn={{id:'lesson',activity:'curated_lesson',status:'released',lesson:bundle,images:[{id:'image-1',name:'task.jpg',sha256:'c'.repeat(64),url:'/api/courses/class/curated-lessons/lesson/images/image-1'}]}}/>);
  expect(html).toContain('Photosynthesis moves carbon');expect(html).toContain('NASA Carbon Cycle');expect(html).toContain('View retained assignment photos');expect(html).toContain('task.jpg');expect(html).not.toContain('<img');expect(html).not.toContain('No attempt snapshot');
 });
 it('withholds an unreleased bundle and never links unsafe photos',()=>{
  const html=renderToStaticMarkup(<CuratedEvidence turn={{id:'lesson',status:'withheld',lesson:bundle,images:[{id:'image-1',name:'task.jpg',sha256:'c'.repeat(64),url:'https://foreign.example/student-photo'}]}}/>);
  expect(html).toContain('No curated lesson was released');expect(html).not.toContain('Photosynthesis moves carbon');expect(html).not.toContain('foreign.example');expect(html).toContain('Binary image not retained');
 });
 it('shows recorded reflection text and distinguishes read-aloud intent from completion',()=>{
  const html=renderToStaticMarkup(<LearningTrail events={[{id:'a',event_type:'curated_lesson.progress',payload:{section_id:'section-1',event:'reflection',response:'I notice plants store carbon. <script>unsafe()</script>'}},{id:'b',event_type:'curated_lesson.progress',payload:{section_id:'section-1',event:'read_aloud'}},{event_type:'student.draft_saved',payload:{content:'Not part of this learning trail'}}]}/>);
  expect(html).toContain('I notice plants store carbon.');expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');expect(html).toContain('Read-aloud requested');expect(html).not.toContain('Not part of this learning trail');expect(html).not.toContain('Read-aloud completed');
 });
});
