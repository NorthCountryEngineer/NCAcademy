import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { HomeworkAIExecutionDetails } from './HomeworkAI';
import { HomeworkAIConfigurationReview } from './HomeworkAISettings';
import { blankHomeworkAIPolicy, homeworkAIConfigurationPayload, type HomeworkAIConfiguration, type HomeworkAIExecution } from './homework-ai-api';

const execution=():HomeworkAIExecution=>({phases:[{phase:'triage',model:'school-triage',connection_name:'School integration',source:'school_policy',policy_revision_id:'school-policy-1',usage:{prompt_tokens:40,completion_tokens:20,total_tokens:60}},{phase:'tutor',model:'teacher-primary',connection_name:'Teacher integration',source:'teacher_policy',policy_revision_id:'teacher-model-policy-2',usage:{prompt_tokens:80,completion_tokens:50,total_tokens:130}},{phase:'safety',model:'school-safety',connection_name:'School integration',source:'school_policy',policy_revision_id:'school-policy-1',usage:{prompt_tokens:60,completion_tokens:10,total_tokens:70}}],mode:'preview',selection_reason:'approved_teacher_primary',preview:{task:'reasoning',reason_code:'reasoning_terms',suggested_model:'candidate-reasoner',suggested_label:'Reasoning candidate'},automatic_routing_enabled:false,estimated_cost:null,pricing_status:'unknown'});
const configuration=():HomeworkAIConfiguration=>({school_id:'school-a',version:2,revision_id:'school-policy-2',approved_revision_id:null,approved:false,available:false,live_models_enabled:false,configuration:{...blankHomeworkAIPolicy(),name:'Study helper',allow_teacher_models:true},configuration_sha256:'c'.repeat(64),approval_required:true});

describe('safe per-answer model execution details',()=>{
 it('shows actual model phases, policy sources, usage and preview-only selection without inventing a cost',()=>{
  const value={...execution(),api_key:'PRIVATE_KEY',endpoint:'PRIVATE_ENDPOINT',prompt:'PRIVATE_PROMPT',hidden_reasoning:'PRIVATE_REASONING'};
  const html=renderToStaticMarkup(<HomeworkAIExecutionDetails execution={value}/>);
  for(const expected of ['Model details','Question assistant','Teacher response','Safety review','school-triage','teacher-primary','school-safety','Teacher model policy','School model policy','prompt tokens','130','teacher-model-policy-2','candidate-reasoner','Preview only','approved primary model was used','No extra model call','Not available'])expect(html).toContain(expected);
  for(const forbidden of ['PRIVATE_KEY','PRIVATE_ENDPOINT','PRIVATE_PROMPT','PRIVATE_REASONING','$0','Automatically switched'])expect(html).not.toContain(forbidden);
 });
 it('explains that homework photographs use the school models named in student confirmation',()=>{
  const value={...execution(),mode:'fixed' as const,selection_reason:'homework_photos_use_school_models',preview:null};const html=renderToStaticMarkup(<HomeworkAIExecutionDetails execution={value}/>);
  expect(html).toContain('School models named in your photo confirmation');expect(html).not.toContain('Routing preview');expect(html).not.toContain('candidate-reasoner');
 });
 it('renders only normalized finite token counts from execution usage',()=>{
  const value=execution();value.phases[0].usage={prompt_tokens:40,completion_tokens:-20,total_tokens:NaN,api_key:'PRIVATE_KEY' as unknown as number,hidden_reasoning:'PRIVATE_REASONING' as unknown as number,endpoint:'PRIVATE_ENDPOINT' as unknown as number};
  const html=renderToStaticMarkup(<HomeworkAIExecutionDetails execution={value}/>);expect(html).toContain('prompt tokens: 40');for(const forbidden of ['PRIVATE_KEY','PRIVATE_REASONING','PRIVATE_ENDPOINT','NaN','-20','api key','hidden reasoning'])expect(html).not.toContain(forbidden);
 });
});

describe('explicit school approval for teacher model assignments',()=>{
 it('defaults teacher model use off and serializes only the explicit teacher-model opt-in',()=>{
  expect(blankHomeworkAIPolicy().allow_teacher_models).toBe(false);const policy={...blankHomeworkAIPolicy(),allow_teacher_models:true,teacher_model:{api_key:'PRIVATE_KEY',endpoint:'PRIVATE_ENDPOINT'}};const body=homeworkAIConfigurationPayload(policy,3);expect(body.allow_teacher_models).toBe(true);expect(body.expected_version).toBe(3);expect(JSON.stringify(body)).not.toContain('PRIVATE_KEY');expect(JSON.stringify(body)).not.toContain('PRIVATE_ENDPOINT');
 });
 it('includes teacher model use and photo limits in the exact saved configuration under review',()=>{
  const state=configuration(),html=renderToStaticMarkup(<HomeworkAIConfigurationReview state={state}/>);expect(html).toContain('Teacher model policies');expect(html).toContain('Approved teacher models for text; school models for homework photos');expect(html).toContain('Not permitted');expect(html).toContain('1,500 output tokens');
  const disabled=renderToStaticMarkup(<HomeworkAIConfigurationReview state={{...state,configuration:{...state.configuration!,allow_teacher_models:false}}}/>);expect(disabled).toContain('School models for every response');expect(disabled).not.toContain('Approved teacher models for text');
 });
});
