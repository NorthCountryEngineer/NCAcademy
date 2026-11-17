import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, SESSION_EXPIRED_EVENT, approvedItems, integrationPayload, request, setCsrf, stamp, tutorIdentity, type Connection } from './api';
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
 it('signals current-session expiry once, clears its CSRF token and never replays the failed write',async()=>{
  const dispatchEvent=vi.fn(),fetcher=vi.fn().mockResolvedValueOnce(new Response('{"detail":"Sign in required"}',{status:401})).mockResolvedValueOnce(new Response('{}'));
  vi.stubGlobal('window',{dispatchEvent});vi.stubGlobal('fetch',fetcher);setCsrf('expired-session');
  await expect(request('/admin/family-accounts',{method:'POST',body:'{"name":"Synthetic student"}'})).rejects.toMatchObject({status:401});
  expect(fetcher).toHaveBeenCalledOnce();expect(dispatchEvent).toHaveBeenCalledOnce();expect(dispatchEvent.mock.calls[0][0].type).toBe(SESSION_EXPIRED_EVENT);
  await request('/courses',{method:'POST',body:'{}'});expect(fetcher.mock.calls[1][1].headers.has('X-CSRF-Token')).toBe(false);
 });
 it('does not expire a newer session when an old request returns 401 late',async()=>{
  const dispatchEvent=vi.fn();let finish!:(response:Response)=>void;const fetcher=vi.fn().mockImplementationOnce(()=>new Promise<Response>(resolve=>{finish=resolve;})).mockResolvedValueOnce(new Response('{}'));
  vi.stubGlobal('window',{dispatchEvent});vi.stubGlobal('fetch',fetcher);setCsrf('old-session');const pending=request('/courses');setCsrf('new-session');
  finish(new Response('{"detail":"Sign in required"}',{status:401}));await expect(pending).rejects.toMatchObject({status:401});expect(dispatchEvent).not.toHaveBeenCalled();
  await request('/courses',{method:'POST',body:'{}'});expect(fetcher.mock.calls[1][1].headers.get('X-CSRF-Token')).toBe('new-session');
 });
 it('keeps normal invalid-password feedback on the sign-in form without emitting an expiry event',async()=>{
  const dispatchEvent=vi.fn(),fetcher=vi.fn().mockResolvedValue(new Response('{"detail":"Invalid username or password"}',{status:401}));
  vi.stubGlobal('window',{dispatchEvent});vi.stubGlobal('fetch',fetcher);setCsrf('prior-session');
  await expect(request('/auth/password',{method:'POST',body:'{}'})).rejects.toMatchObject({message:'Invalid username or password',status:401});expect(dispatchEvent).not.toHaveBeenCalled();expect(fetcher).toHaveBeenCalledOnce();
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
