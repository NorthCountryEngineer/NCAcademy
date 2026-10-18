import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { diagramLayout, LessonMedia, type LessonVisual } from './LessonMedia';

const visual:LessonVisual={id:'carbon-reservoirs-v1',version:'2026-10-18.1',title:'Carbon reservoirs and transfers',caption:'Selected transfers are not to scale.',nodes:[{id:'air',label:'Atmosphere',detail:'Carbon dioxide participates in carbon transfers.'},{id:'life',label:'Living organisms',detail:'Photosynthesis takes up carbon dioxide.'},{id:'soil',label:'Soils',detail:'Soils store carbon.'}],edges:[{from:'air',to:'life',label:'Photosynthesis'},{from:'life',to:'air',label:'Respiration'},{from:'life',to:'soil',label:'Organic matter'}]};

describe('source-linked interactive diagrams',()=>{
 it('offers a keyboard control for every node, selected detail and a text equivalent of edges',()=>{
  const explore=vi.fn(),html=renderToStaticMarkup(<LessonMedia visual={visual} onExplore={explore}/>);
  expect(html.match(/role="button"/g)).toHaveLength(3);expect(html.match(/tabindex="0"/g)).toHaveLength(3);
  for(const node of visual.nodes)expect(html).toContain(`aria-label="Explore ${node.label}"`);
  for(const text of ['Enter or Space','aria-pressed="true"','aria-controls=','aria-live="polite"','Carbon dioxide participates','Diagram connections as text','Photosynthesis','Respiration'])expect(html).toContain(text);
  expect(explore).not.toHaveBeenCalled();
 });
 it('never inserts model SVG/HTML, source URLs or remote media from descriptor strings',()=>{
  const html=renderToStaticMarkup(<LessonMedia visual={{...visual,title:'<script>unsafe()</script>',caption:'<img src="https://remote.test">',nodes:[{...visual.nodes[0],detail:'<iframe src="https://remote.test"/>'},...visual.nodes.slice(1)]}}/>);
  expect(html).toContain('&lt;script&gt;');expect(html).toContain('&lt;iframe');for(const forbidden of ['<script>','<iframe','<img','dangerouslySetInnerHTML','https://remote.test" src='])expect(html).not.toContain(forbidden);
  expect(html).not.toContain('href="https://');
 });
 it('uses fixed bounded geometry for diagram nodes and rejects unbounded node sets',()=>{
  const layout=diagramLayout(visual.nodes);expect(Object.keys(layout)).toEqual(['air','life','soil']);for(const point of Object.values(layout)){expect(point.x).toBeGreaterThanOrEqual(125);expect(point.x).toBeLessThanOrEqual(475);expect(point.y).toBeGreaterThanOrEqual(65);expect(point.y).toBeLessThanOrEqual(365);}
  expect(renderToStaticMarkup(<LessonMedia visual={{...visual,nodes:[]}}/>)).toBe('');
  expect(renderToStaticMarkup(<LessonMedia visual={{...visual,nodes:Array.from({length:9},(_,i)=>({...visual.nodes[0],id:String(i)}))}}/>)).toBe('');
 });
});
