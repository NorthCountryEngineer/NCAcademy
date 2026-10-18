import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, approvedItems, integrationPayload, request, setCsrf, stamp, tutorIdentity, type Connection } from './api';
afterEach(()=>{vi.unstubAllGlobals();setCsrf('');});
describe('calendar dates and timestamps',()=>{
 it('preserves date-only values while localizing full timestamps',()=>{expect(stamp('2026-10-18')).toBe('2026-10-18');const timestamp='2026-10-18T02:30:00Z';expect(stamp(timestamp)).toBe(new Date(timestamp).toLocaleString());expect(stamp(timestamp)).not.toBe(timestamp);});
});
describe('authenticated API requests',()=>{
 it('includes the session CSRF header only on writes and uses same-origin cookies',async()=>{
  const fetcher=vi.fn().mockImplementation(async()=>new Response('{}',{status:200}));vi.stubGlobal('fetch',fetcher);setCsrf('session-token');
  await request('/courses');await request('/courses',{method:'POST',body:JSON.stringify({title:'Course'})});
  const read=fetcher.mock.calls[0][1],write=fetcher.mock.calls[1][1];expect(read.headers.has('X-CSRF-Token')).toBe(false);expect(write.headers.get('X-CSRF-Token')).toBe('session-token');expect(write.credentials).toBe('same-origin');expect(write.headers.get('Content-Type')).toBe('application/json');
 });
 it('reports server authorization errors without returning a fake result',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Audit permission required'}),{status:403})));
  await expect(request('/audit/sessions')).rejects.toMatchObject({message:'Audit permission required',status:403});
 });
 it('does not automatically retry an uncertain provider request',async()=>{
  const fetcher=vi.fn().mockRejectedValue(new TypeError('Connection lost'));vi.stubGlobal('fetch',fetcher);
  await expect(request('/assignments/a/tutor',{method:'POST',body:'{}'})).rejects.toThrow('Connection lost');expect(fetcher).toHaveBeenCalledTimes(1);
 });
});
describe('integration credential mutations',()=>{
 const connection:Connection={id:'c',name:'Model endpoint',endpoint:'https://model.example/v1',models:['allowed'],enabled:false,version:7,key_configured:true};
 it('preserves blank keys and carries optimistic version without status fields',()=>{expect(integrationPayload({...connection,api_key:'   '})).toEqual({name:connection.name,endpoint:connection.endpoint,models:['allowed'],enabled:false,version:7});});
 it('makes clearing explicit and never submits a replacement simultaneously',()=>{const payload=integrationPayload({...connection,api_key:'replacement',clear_key:true});expect(payload).toHaveProperty('clear_key',true);expect(payload).not.toHaveProperty('api_key');});
});
describe('tutoring request identity',()=>{
 it('reuses the same key after an uncertain response and changes it for a revised attempt',()=>{const create=vi.fn().mockReturnValueOnce('first').mockReturnValueOnce('second');const initial=tutorIdentity(null,'Question','Own attempt',create);expect(tutorIdentity(initial,'Question','Own attempt',create)).toBe(initial);expect(tutorIdentity(initial,'Question','Revised attempt',create).key).toBe('second');expect(create).toHaveBeenCalledTimes(2);});
 it('builds explicit instructor item IDs from nonempty lines',()=>{expect(approvedItems('  Consider the premise.\n\n Check your assumptions.','hint')).toEqual([{id:'hint-1',text:'Consider the premise.'},{id:'hint-2',text:'Check your assumptions.'}]);});
});
