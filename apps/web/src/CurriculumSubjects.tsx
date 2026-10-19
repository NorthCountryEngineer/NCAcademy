import { useId, useState } from 'react';
import { Badge } from './components';
import type { CurriculumReviewMetadata, StandardsSubject } from './teacher-api';

export const gradeLabel=(grade:number)=>grade===-1?'Pre-K':grade===0?'Kindergarten':`Grade ${grade}`;
export function curriculumCoverageLabel(value:{coverage?:string;alignment_mapping_available?:boolean}){
 if(value.coverage==='framework_review'||value.alignment_mapping_available===false)return 'Framework review only';
 if(value.coverage==='partial')return 'Partial standards coverage';
 return value.alignment_mapping_available===true?'Standards mapping available':'Review mapping coverage';
}
const placementLabels:Record<string,string>={grade_specific:'Grade-specific scope',grade_band:'Grade-band scope',proficiency:'Proficiency-based placement',local_elective:'School-selected elective',cross_curricular:'Across-subject support'};
const categoryLabels:Record<string,string>={humanities:'Humanities',language:'Languages',literacy_support:'Literacy & learning support',local_elective:'School-selected electives',mathematics:'Mathematics',science:'Science',technology:'Technology',arts:'Arts',health:'Health',career:'Career learning'};
export const placementLabel=(placement?:string)=>placementLabels[placement||'']||'Review grade placement';
const categoryLabel=(category:string)=>categoryLabels[category]||category.replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase());
export function groupedSubjects(subjects:StandardsSubject[],query=''){
 const search=query.trim().toLocaleLowerCase(),groups=new Map<string,StandardsSubject[]>();
 for(const subject of subjects){
  const focus=subject.grade_focus||subject.curriculum_review?.grade_focus||[],scope=subject.tutoring_scope||subject.curriculum_review?.tutoring_scope||[];
  const searchable=[subject.name,subject.code,subject.category||'',...scope,...focus.flatMap(item=>item.topics)].join(' ').toLocaleLowerCase();
  if(search&&!searchable.includes(search))continue;
  const label=subject.category?categoryLabel(subject.category):'Reviewed subjects';
  groups.set(label,[...(groups.get(label)||[]),subject]);
 }
 return [...groups].sort(([left],[right])=>left.localeCompare(right)).map(([label,items])=>({label,subjects:items.sort((left,right)=>left.name.localeCompare(right.name))}));
}

export function CurriculumSubjectPicker({subjects,selected,change,multiple=false,limit=80}:{subjects:StandardsSubject[];selected:string[];change:(codes:string[])=>void;multiple?:boolean;limit?:number}){
 const [query,setQuery]=useState(''),id=useId(),groups=groupedSubjects(subjects,query),selectedSubject=subjects.find(subject=>subject.code===selected[0]),visible=groups.flatMap(group=>group.subjects);
 return <div className="curriculum-subject-picker">
  <label htmlFor={`${id}-search`}>Search subjects<input id={`${id}-search`} type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="Name, subject or tutoring topic"/></label>
  {multiple?<>
   <p className="help-text" aria-live="polite">{selected.length} selected · {visible.length} of {subjects.length} subjects shown</p>
   {!groups.length?<p className="help-text">No subjects match this search. Selected subjects are retained.</p>:<div className="curriculum-subject-groups">{groups.map(group=><section key={group.label} aria-label={group.label}><h4>{group.label}</h4>{group.subjects.map(subject=>{const checked=selected.includes(subject.code);return <label className="checkbox-label curriculum-subject-option" key={subject.code}><input type="checkbox" checked={checked} disabled={!checked&&selected.length>=limit} onChange={event=>change(event.target.checked?[...new Set([...selected,subject.code])]:selected.filter(code=>code!==subject.code))}/><span><strong>{subject.name}</strong><small>{curriculumCoverageLabel(subject)} · {placementLabel(subject.placement)}</small></span></label>;})}</section>)}</div>}
   {selected.length>=limit&&<p className="help-text">The {limit}-subject limit is reached. Clear a selection to choose another subject.</p>}
  </>:<label htmlFor={`${id}-subject`}>Subject<select id={`${id}-subject`} required value={selected[0]||''} onChange={event=>change([event.target.value])}>
   {(!selectedSubject||!visible.some(subject=>subject.code===selectedSubject.code))&&<option value={selected[0]||''}>{selectedSubject?.name||selected[0]?.replaceAll('_',' ')||'Select subject'}</option>}
   {groups.map(group=><optgroup key={group.label} label={group.label}>{group.subjects.map(subject=><option key={subject.code} value={subject.code}>{subject.name}</option>)}</optgroup>)}
  </select>{!groups.length&&<small>No subjects match this search. Your current subject is retained.</small>}</label>}
 </div>;
}

export function CurriculumSubjectSummary({subject,grades=[]}:{subject:StandardsSubject;grades?:number[]}){
 const focus=(subject.grade_focus||subject.curriculum_review?.grade_focus||[]).filter(item=>!grades.length||item.grades.some(grade=>grades.includes(grade))),reviewOnly=subject.coverage==='framework_review'||subject.alignment_mapping_available===false,scope=subject.tutoring_scope||subject.curriculum_review?.tutoring_scope||[],limitations=subject.limitations||subject.curriculum_review?.limitations||[];
 return <div className="curriculum-subject-summary"><div className="scope-line"><Badge tone={reviewOnly?'amber':''}>{curriculumCoverageLabel(subject)}</Badge><Badge>{placementLabel(subject.placement||subject.curriculum_review?.placement)}</Badge></div>
  {reviewOnly?<p className="help-text">Reviewed framework references support class planning. A standards alignment and approved teaching materials still need to be prepared.</p>:<p className="help-text">The installed mapping covers reviewed subsets. Selecting a grade does not establish complete standards or teaching coverage.</p>}
  {scope.length>0&&<p className="help-text">Tutoring scope: {scope.join(' · ')}</p>}
  {(focus.length>0||limitations.length>0)&&<details><summary>Review grade scope and limits</summary>{focus.map((item,index)=><div key={index}><strong>{item.grades.map(gradeLabel).join(', ')}</strong><ul>{item.topics.map(topic=><li key={topic}>{topic}</li>)}</ul></div>)}{limitations.length>0&&<ul>{limitations.map(note=><li key={note}>{note}</li>)}</ul>}</details>}
 </div>;
}

function safeReviewSource(value:string){try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password;}catch{return false;}}
export function CurriculumReviewEvidence({review}:{review:CurriculumReviewMetadata}){
 const sources=review.sources||[];
 return <section className="corpus-scope-review"><h3>Reviewed curriculum scope</h3><div className="scope-line"><Badge tone="amber">Framework review only</Badge>{review.placement&&<Badge>{placementLabel(review.placement)}</Badge>}</div>
  <p className="help-text">These source references and planning topics help prepare this class. They do not provide a numbered standards mapping or an approved teaching corpus.</p>
  <p className="help-text">{[review.name,review.academic_year,review.reviewed_at?`Reviewed ${review.reviewed_at}`:'',review.review_valid_through?`Review valid through ${review.review_valid_through}`:''].filter(Boolean).join(' · ')}</p>
  {!!review.tutoring_scope?.length&&<p className="help-text">Tutoring scope: {review.tutoring_scope.join(' · ')}</p>}
  {!!review.frameworks?.length&&<><h4>Framework editions</h4><ul>{review.frameworks.map((framework,index)=>{const source=sources.find(item=>item.id===framework.source_id);return <li key={`${framework.source_id}-${index}`}><strong>{source?.title||framework.source_id}</strong> · {framework.version}<small>{framework.grades.map(gradeLabel).join(', ')}{framework.notes&&` · ${framework.notes}`}</small></li>;})}</ul></>}
  {!!review.grade_focus?.length&&<><h4>Planning topics by grade scope</h4>{review.grade_focus.map((focus,index)=><div key={index}><strong>{focus.grades.map(gradeLabel).join(', ')}</strong><ul>{focus.topics.map(topic=><li key={topic}>{topic}</li>)}</ul></div>)}</>}
  {!!review.planning_topics?.length&&<><h4>Planning topics</h4><ul>{review.planning_topics.map(topic=><li key={topic}>{topic}</li>)}</ul></>}
  {!!review.limitations?.length&&<><h4>Scope and limits</h4><ul>{review.limitations.map(note=><li key={note}>{note}</li>)}</ul></>}
  {sources.length>0&&<details><summary>Framework source evidence ({sources.length})</summary><ul>{sources.map(source=><li key={source.id}>{source.url&&safeReviewSource(source.url)?<a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a>:source.title}{source.edition&&<small>{source.edition}</small>}{source.verification&&<small>{source.verification==='exact_bytes_review'?'Exact source bytes reviewed':'Official source page reviewed'}</small>}{source.notes&&<p>{source.notes}</p>}</li>)}</ul></details>}
 </section>;
}
