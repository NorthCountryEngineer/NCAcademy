import { useId, useState, type KeyboardEvent } from 'react';

export type LessonVisual={id:string;version:string;title:string;caption:string;nodes:{id:string;label:string;detail:string}[];edges:{from:string;to:string;label:string}[]};
type Point={x:number;y:number};

/** Fixed geometry is application code; no provider markup, coordinates or URLs. */
export function diagramLayout(nodes:LessonVisual['nodes']):Record<string,Point>{
 const count=nodes.length;
 return Object.fromEntries(nodes.map((node,index)=>{const angle=-Math.PI/2+index*2*Math.PI/Math.max(count,1);return [node.id,{x:300+175*Math.cos(angle),y:215+150*Math.sin(angle)}];}));
}
function edgeGeometry(start:Point,end:Point,curved:boolean){
 const dx=end.x-start.x,dy=end.y-start.y,length=Math.hypot(dx,dy)||1,nx=dx/length,ny=dy/length;
 const reach=Math.min(Math.abs(nx)<0.001?Infinity:69/Math.abs(nx),Math.abs(ny)<0.001?Infinity:30/Math.abs(ny));
 const a={x:start.x+nx*(reach+3),y:start.y+ny*(reach+3)},b={x:end.x-nx*(reach+7),y:end.y-ny*(reach+7)};
 const control={x:(a.x+b.x)/2-ny*(curved?35:0),y:(a.y+b.y)/2+nx*(curved?35:0)};
 return {path:`M${a.x},${a.y} Q${control.x},${control.y} ${b.x},${b.y}`,label:{x:(a.x+2*control.x+b.x)/4,y:(a.y+2*control.y+b.y)/4}};
}
function labelLines(label:string){
 const words=label.split(' '),lines:string[]=[];let line='';
 for(const word of words){if(line&&`${line} ${word}`.length>19){lines.push(line);line=word;}else line=line?`${line} ${word}`:word;}if(line)lines.push(line);return lines.slice(0,3);
}

export function LessonMedia({visual,onExplore}:{visual:LessonVisual;onExplore?:()=>void}){
 const [selectedId,setSelectedId]=useState(visual.nodes[0]?.id||'');
 const selected=visual.nodes.find(node=>node.id===selectedId)||visual.nodes[0];
 const prefix=`lesson-media-${useId().replace(/:/g,'')}`,positions=diagramLayout(visual.nodes);
 function choose(id:string){setSelectedId(id);onExplore?.();}
 function keyChoose(event:KeyboardEvent<SVGGElement>,id:string){if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(id);}}
 if(!selected||visual.nodes.length>8)return null;
 return <figure className="lesson-media" style={{margin:'1rem 0',padding:'1rem',border:'1px solid #cbd8d2',borderRadius:12,background:'#f7fbf9'}}>
  <figcaption><h4 id={`${prefix}-title`} style={{margin:'0 0 .35rem'}}>{visual.title}</h4><p id={`${prefix}-caption`} style={{margin:'0 0 .5rem'}}>{visual.caption}</p><p className="help-text" id={`${prefix}-instructions`}>Select a labeled part of the diagram to explore it. Use Tab, then Enter or Space with a keyboard.</p></figcaption>
  <svg className="lesson-media-diagram" viewBox="0 0 600 435" role="group" aria-labelledby={`${prefix}-title`} aria-describedby={`${prefix}-caption ${prefix}-instructions`} style={{width:'100%',maxHeight:450,display:'block',overflow:'visible'}}>
   <defs><marker id={`${prefix}-arrow`} markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L6,3 z" fill="#59786d"/></marker></defs>
   {visual.edges.map((edge,index)=>{const from=positions[edge.from],to=positions[edge.to];if(!from||!to||edge.from===edge.to)return null;const geometry=edgeGeometry(from,to,visual.edges.some(other=>other.from===edge.to&&other.to===edge.from));return <g key={`${edge.from}-${edge.to}-${index}`} aria-hidden="true"><path d={geometry.path} fill="none" stroke="#59786d" strokeWidth="1.7" markerEnd={`url(#${prefix}-arrow)`}/><rect x={geometry.label.x-Math.min(edge.label.length*2.75+7,90)} y={geometry.label.y-9} width={Math.min(edge.label.length*5.5+14,180)} height="18" rx="5" fill="#f7fbf9"/><text x={geometry.label.x} y={geometry.label.y+3} textAnchor="middle" fill="#345247" fontSize="10">{edge.label}</text></g>;})}
   {visual.nodes.map(node=>{const point=positions[node.id],active=selected.id===node.id,lines=labelLines(node.label);return <g key={node.id} className="lesson-media-node" role="button" tabIndex={0} aria-label={`Explore ${node.label}`} aria-pressed={active} aria-controls={`${prefix}-detail`} onClick={()=>choose(node.id)} onKeyDown={event=>keyChoose(event,node.id)} style={{cursor:'pointer',outlineOffset:4}}><rect x={point.x-69} y={point.y-30} width="138" height="60" rx="12" fill={active?'#174b39':'#ffffff'} stroke={active?'#174b39':'#8eafa0'} strokeWidth={active?3:1.5}/>{lines.map((line,index)=><text key={index} x={point.x} y={point.y+(index-(lines.length-1)/2)*15+4} textAnchor="middle" fill={active?'#ffffff':'#183d2e'} fontSize="12" fontWeight="600" pointerEvents="none">{line}</text>)}</g>;})}
  </svg>
  <section className="lesson-media-detail" id={`${prefix}-detail`} aria-live="polite" aria-atomic="true" style={{background:'#fff',borderLeft:'4px solid #24694c',padding:'.8rem 1rem',marginTop:'.4rem'}}><h5 style={{fontSize:'1rem',margin:'0 0 .3rem'}}>{selected.label}</h5><p style={{margin:0}}>{selected.detail}</p></section>
  <details style={{marginTop:'.7rem'}}><summary>Diagram connections as text</summary><ul>{visual.edges.map((edge,index)=><li key={index}>{visual.nodes.find(node=>node.id===edge.from)?.label||edge.from} → {visual.nodes.find(node=>node.id===edge.to)?.label||edge.to}: {edge.label}</li>)}</ul></details>
 </figure>;
}
