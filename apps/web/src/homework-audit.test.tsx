import {describe,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {HomeworkReviewContent,homeworkAuditImageUrl,type HomeworkReview} from './HomeworkAudit';

describe('homework reviewer transcript',()=>{
 it('accepts only private scoped homework image paths',()=>{expect(homeworkAuditImageUrl('/api/homework-help/sessions/s/messages/m/images/i')).toBe('/api/homework-help/sessions/s/messages/m/images/i');for(const value of ['https://foreign.example/student','/api/homework-help/sessions/s/messages/m/images/i?redirect=foreign','/api/homework-help/sessions/../messages/m/images/i'])expect(homeworkAuditImageUrl(value)).toBeNull();});
 it('shows unanswered routing and pre-admission evidence without fetching pictures or media',()=>{const review:HomeworkReview={session:{id:'s',student_id:'student',created_at:'2026-10-18'},messages:[{id:'m',message:'<script>my question</script>',created_at:'2026-10-18',triage:{prompt:'Which course is this for?'},images:[{name:'homework.jpg',sha256:'a'.repeat(64)}],turn:null,outcome:{error:'The request was not sent'}}],events:[{event_type:'homework.triaged',payload:{message:'My question'}}]};const html=renderToStaticMarkup(<HomeworkReviewContent review={review} openCourseSession={()=>{}}/>);expect(html).toContain('Which course is this for?');expect(html).toContain('The request was not sent');expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');expect(html).not.toContain('<img');expect(html).not.toContain('<iframe');expect(html).toContain('homework.triaged');});
});
