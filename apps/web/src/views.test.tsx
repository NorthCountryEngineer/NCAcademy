import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { navigationForRole } from './App';
import { StudentTurn } from './Curriculum';
import { WorkTrail } from './SessionAudit';
describe('role-aware navigation',()=>{
 it('limits students to their coursework and hides review from users without audit permission',()=>{expect(navigationForRole('student').map(n=>n.id)).toEqual(['overview','courses']);expect(navigationForRole('instructor',false).some(n=>n.id==='sessions')).toBe(false);expect(navigationForRole('admin').some(n=>n.id==='administration')).toBe(true);});
});
describe('student tutoring transcript',()=>{
 it('renders approved text while withholding provider response fields',()=>{const html=renderToStaticMarkup(<StudentTurn turn={{id:'t',status:'released',message:'Where am I stuck?',approved_text:'Which premise supports your step?',response:'PRIVATE_PROVIDER_TEXT',prompt:'PRIVATE_PROMPT',policy_snapshot:{hints:['UNRELEASED_HINT']}}}/>);expect(html).toContain('Which premise supports your step?');expect(html).not.toContain('PRIVATE_PROVIDER_TEXT');expect(html).not.toContain('PRIVATE_PROMPT');expect(html).not.toContain('UNRELEASED_HINT');});
 it('escapes student content and shows blocked requests without an answer fallback',()=>{const html=renderToStaticMarkup(<StudentTurn turn={{id:'t',status:'blocked',message:'<script>bad()</script>',error:'Attempt required',response:'ANSWER'}}/>);expect(html).toContain('&lt;script&gt;');expect(html).toContain('Attempt required');expect(html).not.toContain('ANSWER');});
});
describe('instructor work history',()=>{
 it('makes saved attempts and submitted responses readable in chronological audit order',()=>{const html=renderToStaticMarkup(<WorkTrail events={[{id:'e1',event_type:'student.draft_saved',payload:{content:'My initial reasoning'}},{id:'e2',event_type:'student.submitted',payload:{content:'My revised reasoning'}},{id:'e3',event_type:'audit.session_viewed',payload:{}}]}/>);expect(html).toContain('2 records');expect(html.indexOf('My initial reasoning')).toBeLessThan(html.indexOf('My revised reasoning'));expect(html).not.toContain('audit.session_viewed');});
});
