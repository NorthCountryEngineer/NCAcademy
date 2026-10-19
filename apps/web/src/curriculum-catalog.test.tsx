import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AcademicContextOptions, CorpusReview } from './CourseCorpus';
import { CurriculumSubjectPicker, CurriculumSubjectSummary, gradeLabel, groupedSubjects } from './CurriculumSubjects';
import { CurriculumPlanReview, SchoolCurriculumFields } from './SchoolOnboarding';
import { allCurriculumGrades, allCurriculumSubjects, blankSchoolCurriculum, nextPublicationBatch, publicationSelection, publishSchoolCurriculum, validateSchoolCurriculum, type CurriculumRun, type SchoolCurriculumCatalog } from './school-curriculum-api';
import type { CorpusDescriptor, StandardsSubject } from './teacher-api';

afterEach(()=>vi.unstubAllGlobals());
const review:StandardsSubject={code:'ela',name:'English Language Arts',grades:[-1,0,1,2,3,4,5,6,7,8,9,10,11,12],academic_years:['2026-2027'],standards_version:null,coverage:'framework_review',alignment_mapping_available:false,category:'humanities',placement:'grade_band',tutoring_scope:['Reading and research coaching'],limitations:['School reading lists and teaching materials still need review.'],grade_focus:[{grades:[-1],topics:['Shared stories and oral language'],source_ids:['nysed-ela']},{grades:[9,10],topics:['Literary analysis and argument'],source_ids:['nysed-ela']}],source_ids:['nysed-ela']};
const mapped:StandardsSubject={code:'earth_space_science',name:'Earth and space sciences',grades:[9,10,11,12],academic_years:['2026-2027'],standards_version:'nyssls-2016',mapping_version:'reviewed-v1',coverage:'partial',alignment_mapping_available:true,category:'science',curriculum_review:{placement:'grade_band',tutoring_scope:['Geoscience evidence'],limitations:['Reviewed mapping remains partial.'],grade_focus:[{grades:[9,10,11,12],topics:['Earth systems'],source_ids:['nysed-science']}]}};
const catalog:SchoolCurriculumCatalog={states:[{code:'US-NY',name:'New York',subjects:[review,mapped]}]};
function plan(count=1):CurriculumRun{return {id:'scope-run',kind:'school_curriculum_run',current:true,status:'ready',agent:{name:'Reviewed source planner',mode:'reviewed_source_planner',live_model_used:false},steps:[],plan_sha256:'a'.repeat(64),plan:{gaps:[],courses:Array.from({length:count},(_,index)=>({key:`course-${index}`,title:`Reading class ${index}`,grade:-1,academic_year:'2026-2027',subject:'ela',curriculum:{state:'US-NY',subject:'ela',grades:[-1],academic_year:'2026-2027',standards_version:null},units:[{title:'Framework planning references',standard_codes:[],planning_topics:['Shared stories and oral language']}],source_ids:['nysed-ela'],sources:[{id:'nysed-ela',title:'NYSED ELA',url:'https://www.nysed.gov/standards-instruction/ela-guidance-resources'}],catalog_sha256:'b'.repeat(64),coverage:'framework_review',alignment_mapping_available:false,placement:'grade_band',limitations:['Teaching materials require human review.'],gaps:[]}))}};}

describe('broad curriculum discovery and honest coverage',()=>{
 it('renders Pre-K and Kindergarten using grade labels instead of negative or zero grade numbers',()=>{
  expect([gradeLabel(-1),gradeLabel(0),gradeLabel(12)]).toEqual(['Pre-K','Kindergarten','Grade 12']);
  const html=renderToStaticMarkup(<SchoolCurriculumFields value={{...blankSchoolCurriculum(),mode:'state',subjects:['ela'],grades:[-1]}} change={()=>{}} catalog={catalog}/>);
  for(const text of ['Pre-K','Kindergarten','Grade 12','Search subjects','Framework review only','Grade-band scope','Shared stories and oral language','School reading lists'])expect(html).toContain(text);
  expect(html).not.toContain('Grade -1');expect(html).not.toContain('Grade 0');
 });
 it('groups a large catalog and finds a subject by name, code or tutoring topic',()=>{
  const many=Array.from({length:60},(_,index)=>({...review,code:`subject_${index}`,name:`Subject ${index}`,category:index%2?'humanities':'science',tutoring_scope:[`Unique topic ${index}`]}));
  expect(groupedSubjects(many).map(group=>group.label)).toEqual(['Humanities','Science']);
  expect(groupedSubjects(many,' unique topic 59 ').flatMap(group=>group.subjects).map(subject=>subject.code)).toEqual(['subject_59']);
  expect(groupedSubjects(many,'subject_42').flatMap(group=>group.subjects).map(subject=>subject.code)).toEqual(['subject_42']);
  const html=renderToStaticMarkup(<CurriculumSubjectPicker multiple subjects={many} selected={[]} change={()=>{}}/>);
  expect(html).toContain('0 selected · 60 of 60 subjects shown');expect((html.match(/type="checkbox"/g)||[])).toHaveLength(60);
 });
 it('finds grade-level topics from direct descriptors and nested mapping review metadata',()=>{
  const math={...review,code:'mathematics',name:'Mathematics',tutoring_scope:['Concept coaching'],grade_focus:[{grades:[3],topics:['Fraction models and equivalent fractions'],source_ids:['math-source']}]};
  const science={...mapped,code:'life_science',name:'Life science',tutoring_scope:['Scientific reasoning'],curriculum_review:{...mapped.curriculum_review,grade_focus:[{grades:[3,4],topics:['Pollination and plant life cycles'],source_ids:['science-source']}]}};
  expect(groupedSubjects([math,science],'fractions').flatMap(group=>group.subjects).map(subject=>subject.code)).toEqual(['mathematics']);
  expect(groupedSubjects([math,science],'pollination').flatMap(group=>group.subjects).map(subject=>subject.code)).toEqual(['life_science']);
 });
 it('offers bounded select-all and clear controls for all subjects and grades independently of search',()=>{
  const subjects=Array.from({length:67},(_,index)=>({...review,code:`subject_${index}`,name:`Subject ${index}`})),allCatalog={states:[{code:'US-NY',name:'New York',subjects}]};
  const html=renderToStaticMarkup(<SchoolCurriculumFields value={{...blankSchoolCurriculum(),mode:'state'}} change={()=>{}} catalog={allCatalog}/>);
  for(const label of ['Select all subjects','Clear subjects','Select all grades','Clear grades','including subjects hidden by search'])expect(html).toContain(label);
  expect(allCurriculumSubjects([...subjects,subjects[0]])).toEqual(subjects.map(subject=>subject.code));
  expect(allCurriculumGrades([12,0,-1,...Array.from({length:14},(_,index)=>index-1)])).toEqual(Array.from({length:14},(_,index)=>index-1));
  expect(()=>allCurriculumSubjects(Array.from({length:81},(_,index)=>({code:`subject_${index}`})))).toThrow('80 subjects');
  expect(()=>allCurriculumGrades([-2,13])).toThrow('Pre-K through grade 12');
  const overflow=renderToStaticMarkup(<SchoolCurriculumFields value={{...blankSchoolCurriculum(),mode:'state'}} change={()=>{}} catalog={{states:[{code:'US-NY',name:'New York',subjects:Array.from({length:81},(_,index)=>({...review,code:`subject_${index}`}))}]}}/>);
  expect(overflow).toContain('disabled="">Select all subjects');expect(overflow).toContain('80-subject limit');
 });
 it('supports a grouped single-course picker while retaining partial mapping versions and review metadata',()=>{
  const html=renderToStaticMarkup(<AcademicContextOptions catalog={catalog} value={{state:'US-NY',subject:'ela',grades:[-1],academic_year:'2026-2027',standards_version:null}} change={()=>{}}/>);
  expect(html).toContain('<optgroup label="Humanities">');expect(html).toContain('Pre-K');expect(html).toContain('Framework review only');expect(html).not.toContain('Grade -1');
  const mappedHtml=renderToStaticMarkup(<CurriculumSubjectSummary subject={mapped} grades={[10]}/>);
  for(const text of ['Partial standards coverage','Geoscience evidence','Earth systems','Reviewed mapping remains partial'])expect(mappedHtml).toContain(text);
  expect(mappedHtml).not.toContain('Framework review only');expect(mapped.standards_version).toBe('nyssls-2016');expect(mapped.mapping_version).toBe('reviewed-v1');
 });
 it('keeps framework planning topics distinct from numbered teaching standards',()=>{
  const html=renderToStaticMarkup(<CurriculumPlanReview run={plan()} selected={['course-0']} change={()=>{}} openCourse={()=>{}}/>);
  for(const text of ['Pre-K','Framework review only','Planning topics','Shared stories and oral language','No numbered standards mapping','Teaching materials require human review','Grade-band scope'])expect(html).toContain(text);
  expect(html).not.toContain('Partial standards coverage');expect(html).not.toContain('Grade -1');expect(html).not.toContain('corpus approved');
 });
 it('collapses a large placement-gap list while retaining every note for inspection',()=>{
  const run=plan();run.plan.gaps=Array.from({length:468},(_,index)=>({code:'grade_scope',message:`Placement note ${index}`}));
  const html=renderToStaticMarkup(<CurriculumPlanReview run={run} selected={[]} change={()=>{}} openCourse={()=>{}}/>);
  expect(html).toContain('Some subjects apply only to selected grades or proficiency bands');expect(html).toContain('<details><summary>Review 468 planning notes</summary>');
  expect(html).toContain('Placement note 0');expect(html).toContain('Placement note 467');expect(html).toContain('Reading class 0');expect((html.match(/Placement note /g)||[])).toHaveLength(468);
 });
 it('shows unverified corpus source evidence, editions, grade topics and limits without turning references into teaching standards',()=>{
  const corpus:CorpusDescriptor={id:'reference-corpus',course_id:'class',school_id:'school',status:'unverified',coverage:'framework_review',curriculum:null,standards_version:null,mapping_version:null,snapshot_sha256:'a'.repeat(64),configuration_sha256:'b'.repeat(64),documents:[],sources:[],gaps:[{code:'alignment_mapping_required',message:'An exact standards mapping is required.'}],approval:{status:'pending'},ready_for_teaching:false,can_refresh:true,can_approve:false,can_retrieve:false,curriculum_review:{name:review.name,academic_year:'2026-2027',reviewed_at:'2026-10-18',review_valid_through:'2027-08-31',placement:review.placement,grade_focus:review.grade_focus,tutoring_scope:review.tutoring_scope,limitations:review.limitations,frameworks:[{source_id:'nysed-ela',version:'Revised 2017',grades:[-1,0,1],notes:'Full grade-band statements require separate mapping.'}],sources:[{id:'nysed-ela',title:'NYSED English Language Arts',edition:'2017',verification:'exact_bytes_review',url:'https://www.nysed.gov/standards-instruction/ela-guidance-resources'},{id:'unsafe',title:'Unsafe source',url:'javascript:alert(1)'}]}};
  const html=renderToStaticMarkup(<CorpusReview corpus={corpus}/>);
  for(const text of ['Reviewed curriculum scope','Framework review only','Framework editions','Revised 2017','Planning topics by grade scope','Pre-K','Shared stories and oral language','Scope and limits','School reading lists','Framework source evidence (2)','Exact source bytes reviewed','2027-08-31','An exact standards mapping is required'])expect(html).toContain(text);
  expect(html).toContain('href="https://www.nysed.gov/standards-instruction/ela-guidance-resources"');expect(html).not.toContain('href="javascript:');expect(html).not.toContain('Approved teaching corpus');expect(html).not.toContain('Reviewed standard</span>');expect(html).not.toContain('Approve teaching corpus');
 });
});

describe('large school configuration and bounded publication batches',()=>{
 it('accepts 80 subjects across all 14 grades and rejects invalid grades and subject overflow',()=>{
  const value={...blankSchoolCurriculum(),mode:'state' as const,subjects:Array.from({length:80},(_,index)=>`subject_${index}`),grades:Array.from({length:14},(_,index)=>index-1)};
  expect(validateSchoolCurriculum(value)).toBe(value);
  expect(()=>validateSchoolCurriculum({...value,subjects:[...value.subjects,'one_more']})).toThrow('80 subjects');
  for(const grades of [[-2],[13],[1.5],Array.from({length:15},()=>1)])expect(()=>validateSchoolCurriculum({...value,grades})).toThrow('Pre-K through grade 12');
 });
 it('selects the first 100 unpublished classes, then the remaining batch without duplicates',()=>{
  const run=plan(154),first=nextPublicationBatch(run),second=nextPublicationBatch(run,first);
  expect(first).toHaveLength(100);expect(first[0]).toBe('course-0');expect(first[99]).toBe('course-99');expect(second).toHaveLength(54);expect(second[0]).toBe('course-100');
  expect(nextPublicationBatch(run,[...first,...second])).toEqual([]);
  expect(publicationSelection(run,['missing','course-0','course-100','course-100'],first)).toEqual(['course-100']);
  expect(publicationSelection(run,run.plan.courses.map(course=>course.key))).toHaveLength(100);
 });
 it('prevents selecting an extra class at the publication limit while leaving selected choices removable',()=>{
  const run=plan(101),html=renderToStaticMarkup(<CurriculumPlanReview run={run} selected={nextPublicationBatch(run)} change={()=>{}} openCourse={()=>{}}/>);
  expect(html).toContain('100 selected · 101 not created · up to 100 classes per batch');expect(html).toContain('Select next batch');
  expect((html.match(/type="checkbox" checked=""/g)||[])).toHaveLength(100);expect((html.match(/type="checkbox" disabled=""/g)||[])).toHaveLength(1);
 });
 it('blocks oversized or empty publication commands before network access and submits exact 100-key batches',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({id:'publication'}),{status:201}));vi.stubGlobal('fetch',fetcher);
  const run=plan(101),keys=run.plan.courses.map(course=>course.key);
  await expect(publishSchoolCurriculum('school',1,run,keys,'synthetic-key')).rejects.toThrow('1 and 100');
  await expect(publishSchoolCurriculum('school',1,run,[],'synthetic-key')).rejects.toThrow('1 and 100');expect(fetcher).not.toHaveBeenCalled();
  await publishSchoolCurriculum('school',1,run,[...keys.slice(0,100),'course-0'],'synthetic-key');
  expect(JSON.parse(fetcher.mock.calls[0][1].body).course_keys).toEqual(keys.slice(0,100));expect(fetcher).toHaveBeenCalledTimes(1);
 });
});
