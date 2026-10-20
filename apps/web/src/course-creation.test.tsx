import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { CourseEditor } from './Curriculum';
import { CourseCorpusContext } from './CourseCorpus';
import type { CorpusDescriptor } from './teacher-api';
import { CourseCatalogFields, CourseCatalogResults, CourseCatalogSelection, CourseCreationChoice } from './CourseCatalogFields';
import { courseCatalogLevelLabel, courseCatalogReference, courseCreationContextPayload, getCourseCatalog, courseCatalogPath, safeCatalogSource, type CourseCatalogPage } from './course-catalog-api';
import { SchoolCurriculumFields } from './SchoolOnboarding';
import { blankSchoolCurriculum } from './school-curriculum-api';
import type { School } from './api';

const school:School={id:'school-a',name:'School',description:'',core_resource_id:null,role:'admin',effective_role:'admin',capabilities:{manage_school:true,manage_resources:true,create_course:true,manage_members:true,manage_agents:true,approve_changes:true,manage_connections:true},curriculum:{...blankSchoolCurriculum(),mode:'state',subjects:['earth_space_science'],grades:[10]}};
const page:CourseCatalogPage={state:'US-NY',academic_year:'2026-2027',title:'NYSED Comprehensive Course Catalog',catalog_sha256:'a'.repeat(64),offset:0,limit:20,total:2030,levels:['secondary','prior_to_secondary','all_grades'],subject_areas:['Fine and Performing Arts','Physical, Health, and Safety Education'],sources:[{title:'Official NYSED catalog',url:'https://www.nysed.gov/catalog'}],items:[{course_code:'08001',title:'Physical Education',description:'Movement, fitness and practical learning.',level:'secondary',subject_area:'Physical, Health, and Safety Education',cte:false,ap:false,ib:false,growth_core_indicator:null,source_row:1},{course_code:'05101',title:'Band',description:'Perform and study instrumental music.',level:'prior_to_secondary',subject_area:'Fine and Performing Arts',cte:false,ap:false,ib:false,growth_core_indicator:null,source_row:2},{course_code:'99999',title:'All-grades art study',description:'Studio exploration <script>alert(1)</script>',level:'all_grades',subject_area:'Fine and Performing Arts',cte:false,ap:false,ib:false,growth_core_indicator:null,source_row:3}]};
afterEach(()=>vi.unstubAllGlobals());

describe('independent official and custom course creation',()=>{
 it('offers official catalog discovery and unrestricted custom courses as distinct choices',()=>{
  const html=renderToStaticMarkup(<CourseCreationChoice value="catalog" change={()=>{}}/>);
  for(const text of ['New York course catalog','Custom course','physical education, art, music','career studies and Regents subjects','without a New York catalog code or required state standards'])expect(html).toContain(text);
  expect((html.match(/type="radio"/g)||[])).toHaveLength(2);
 });
 it('defaults a new class to a usable custom course without inheriting a school standards requirement',()=>{
  const html=renderToStaticMarkup(<CourseEditor school={school} close={()=>{}} saved={async()=>{}}/>);
  expect(html).toContain('Custom courses are available in every school');expect(html).toContain('no NYS catalog code, state standards or AI corpus approval is required');expect(html).toContain('Optional standards alignment');
  expect(html).not.toContain('Loading academic catalog');expect(html).not.toContain('Loading the official course catalog');expect(html).toContain('class="button primary">Create course');
  expect(courseCreationContextPayload('custom',null,false,null)).toEqual({course_catalog:null,curriculum:null});
 });
 it('removes old catalog/standards bindings explicitly when changed to a plain custom course',()=>{
  const selected=courseCatalogReference(page,page.items[0]),curriculum={state:'US-NY',subject:'earth_space_science',grades:[10],academic_year:'2026-2027',standards_version:null};
  expect(courseCreationContextPayload('custom',selected,false,curriculum)).toEqual({course_catalog:null,curriculum:null});
  expect(courseCreationContextPayload('custom',selected,true,curriculum)).toEqual({course_catalog:null,curriculum});
 });
 it('requires an exact official selection while keeping standards and teaching readiness optional',()=>{
  expect(()=>courseCreationContextPayload('catalog',null,false,null)).toThrow('Choose a New York catalog course');
  const selected=courseCatalogReference(page,page.items[1]);
  expect(courseCreationContextPayload('catalog',selected,false,null)).toEqual({course_catalog:{state:'US-NY',academic_year:'2026-2027',course_code:'05101',catalog_sha256:'a'.repeat(64)},curriculum:null});
  expect(()=>courseCreationContextPayload('catalog',selected,true,null)).toThrow('optional standards alignment');
 });
 it('includes practical, performing arts and all-grades rows without disabled or tutoring filters',()=>{
  const html=renderToStaticMarkup(<CourseCatalogResults page={page} value={null} change={()=>{}}/>);
  for(const text of ['Physical Education','Band','All-grades art study','Prior to secondary','All grades','2,030 matching courses','08001','05101'])expect(html).toContain(text);
  expect((html.match(/type="radio"/g)||[])).toHaveLength(3);expect(html).not.toContain('disabled');expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');
 });
 it('shows exact official provenance separately from a locally editable course title',()=>{
  const value={...courseCatalogReference(page,page.items[1]),source_url:'https://www.nysed.gov/catalog'},html=renderToStaticMarkup(<CourseCatalogSelection value={value}/>);
  for(const text of ['Selected NYSED course','05101','Band','2026-2027','Official course description','href="https://www.nysed.gov/catalog"'])expect(html).toContain(text);
  expect(safeCatalogSource('javascript:alert(1)')).toBeNull();expect(safeCatalogSource('https://user:password@example.test')).toBeNull();
  expect(['secondary','prior_to_secondary','all_grades'].map(courseCatalogLevelLabel)).toEqual(['Secondary','Prior to secondary','All grades']);
 });
 it('searches and pages the whole school-authorized catalog without silently filtering grades or standards readiness',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(page),{status:200}));vi.stubGlobal('fetch',fetcher);
  await getCourseCatalog('school /&a',{q:' Band & ensemble ',subject_area:'Fine and Performing Arts',level:'all_grades',offset:40,limit:20});
  const url=new URL(fetcher.mock.calls[0][0],'https://academy.example');
  expect(url.pathname).toBe('/api/course-catalog');expect(Object.fromEntries(url.searchParams)).toEqual({state:'US-NY',academic_year:'2026-2027',offset:'40',limit:'20',q:'Band & ensemble',subject_area:'Fine and Performing Arts',level:'all_grades',school_id:'school /&a'});
  expect(fetcher.mock.calls[0][1].credentials).toBe('same-origin');
 });
 it('keeps the Regents exam filter optional and exposes verified assessment-course badges',()=>{
  const html=renderToStaticMarkup(<CourseCatalogFields schoolId="school-a" value={null} change={()=>{}}/>);
  expect(html).toContain('Regents exam courses only');expect(html).toContain('Leave this off to browse every official course');expect(html).not.toContain('type="checkbox" checked');
  expect(courseCatalogPath('school-a')).not.toContain('regents_only');expect(courseCatalogPath('school-a',{regents_only:true})).toContain('regents_only=true');
  const marked=renderToStaticMarkup(<CourseCatalogResults page={{...page,items:[{...page.items[0],course_code:'02050',title:'Algebra I',subject_area:'Mathematics'}],regents_course_codes:['02050']}} value={null} change={()=>{}}/>);
  expect(marked).toContain('Regents exam course');expect((marked.match(/type="radio"/g)||[])).toHaveLength(1);
 });
 it('keeps optional teaching preparation separate from a class that is already available',()=>{
  const corpus:CorpusDescriptor={id:null,course_id:'class-a',school_id:'school-a',status:'unverified',coverage:'none',curriculum:null,standards_version:null,mapping_version:null,snapshot_sha256:null,configuration_sha256:'b'.repeat(64),documents:[],sources:[],gaps:[],approval:{status:'pending'},ready_for_teaching:false,can_refresh:true,can_approve:false,can_retrieve:false};
  const html=renderToStaticMarkup(<CourseCorpusContext corpus={corpus}/>);
  expect(html).toContain('This class is available for coursework, enrollment and teacher assignments');expect(html).toContain('State standards alignment is optional');expect(html).toContain('<details><summary>Optional teaching corpus preparation</summary>');expect(html).not.toContain('Approve teaching corpus');
 });
 it('explains that custom courses work before custom document ingestion exists',()=>{
  const html=renderToStaticMarkup(<SchoolCurriculumFields value={{...blankSchoolCurriculum(),mode:'custom',custom_name:'Local curriculum'}} change={()=>{}} catalog={null}/>);
  expect(html).toContain('Create your own courses now');expect(html).toContain('Custom course');expect(html).toContain('Document ingestion coming later');
 });
});
