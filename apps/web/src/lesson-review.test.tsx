import {describe,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {LessonEvidence} from './SessionAudit';
describe('lesson session evidence',()=>{
 it('renders the actual teacher and admitted reviewed passage without inventing a draft',()=>{
  const html=renderToStaticMarkup(<LessonEvidence turn={{id:'turn',status:'released',teacher_name:'River',teacher_context_snapshot:{teacher_mode:'managed'},evidence_snapshot:{matches:[{id:'source:carbon',title:'Carbon evidence',excerpt:'Photosynthesis transfers carbon.'}]}}}/>);
  expect(html).toContain('River');expect(html).toContain('Managed AI profile');expect(html).toContain('Photosynthesis transfers carbon.');expect(html).toContain('source:carbon');expect(html).not.toContain('No attempt snapshot');
 });
 it('labels a blocked request with no admitted passage',()=>{
  expect(renderToStaticMarkup(<LessonEvidence turn={{id:'turn',status:'blocked'}}/>)).toContain('No teaching passage was admitted');
 });
});
