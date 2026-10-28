import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { CurriculumSubjectSummary, curriculumCoverageLabel } from './CurriculumSubjects';
import { CorpusReview } from './CourseCorpus';
import type { StandardsSubject, CorpusDescriptor } from './teacher-api';

const subject:StandardsSubject={code:'mathematics',name:'Mathematics',grades:[-1,0,1,2,3,4,5,6,7,8],academic_years:['2026-2027'],standards_version:'nys-next-generation-mathematics-2017-updated-2019',coverage:'complete_standards',alignment_mapping_available:true,versions:[{grades:[3],academic_years:['2026-2027'],available:true,coverage:'complete_standards'}]};
describe('grade-specific complete mapping presentation',()=>{
 it('distinguishes complete exact mapping from partial and framework scope',()=>{
  expect(curriculumCoverageLabel(subject)).toBe('Complete installed standards mapping');
  expect(curriculumCoverageLabel({coverage:'partial'})).toBe('Partial standards coverage');
  expect(curriculumCoverageLabel({coverage:'framework_review'})).toBe('Framework review only');
 });
 it('shows mapped grade3 honestly and preserves framework-only status for other or mixed grades',()=>{
  const ready=renderToStaticMarkup(<CurriculumSubjectSummary subject={subject} grades={[3]}/>);
  expect(ready).toContain('Complete installed standards mapping');
  expect(ready).toContain('Human review, classroom practice and assessment remain required');
  for(const grades of [[2],[4],[3,4]]){
   const planning=renderToStaticMarkup(<CurriculumSubjectSummary subject={subject} grades={grades}/>);
   expect(planning).toContain('Framework review only');
   expect(planning).not.toContain('Complete installed standards mapping');
  }
 });
 it('renders the local sequence with exact unit codes without implying human approval',()=>{
  const corpus:CorpusDescriptor={id:'synthetic-corpus',school_id:'school',course_id:'course',status:'aligned',coverage:'complete_standards',curriculum:{state:'US-NY',subject:'mathematics',grades:[3],academic_year:'2026-2027',standards_version:subject.standards_version},standards_version:subject.standards_version,mapping_version:'synthetic',snapshot_sha256:'a'.repeat(64),configuration_sha256:'b'.repeat(64),documents:[],sources:[],gaps:[],approval:{status:'pending'},ready_for_teaching:false,can_refresh:true,can_approve:true,can_retrieve:false,curriculum_review:{coverage:'framework_review',alignment_mapping_available:false,name:'Earlier broad framework review',planning_topics:['Earlier topic summary']},local_sequence:[{order:1,title:'Equal groups',standard_codes:['NY-3.OA.1','NY-3.OA.2'],note:'Locally suggested sequence.'}]};
  const result=renderToStaticMarkup(<CorpusReview corpus={corpus}/>);
  expect(result).toContain('Awaiting corpus review');
  expect(result).toContain('Complete grade standards mapping');
  expect(result).toContain('Local course sequence');
  expect(result).toContain('NY-3.OA.1');
  expect(result).toContain('not a state-authored curriculum or a complete lesson bank');
  expect(result).not.toContain('Approved teaching corpus');
  expect(result).not.toContain('Framework review only');
  expect(result).not.toContain('Earlier topic summary');
 });
});
