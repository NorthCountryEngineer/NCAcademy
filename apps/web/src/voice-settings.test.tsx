import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { VoiceSettingsEditor, voiceSettingsPayload, type VoiceSettingsState } from './VoiceSettings';

const settings:VoiceSettingsState={version:2,enabled:false,voice_ids:['stock-a'],key_configured:true,zero_retention:false,provider_retention_acknowledged:false,max_daily_operations:20,max_daily_recording_seconds:300,max_daily_speech_characters:12000,catalog:[{id:'stock-a',name:'Public stock voice',category:'premade',retirement_date:'2026-12-31'}]};
describe('school voice administration',()=>{
 it('preserves the saved credential without returning it or resubmitting server catalog metadata',()=>{const value=voiceSettingsPayload(settings,'',false);expect(value).not.toHaveProperty('api_key');expect(value).not.toHaveProperty('key_configured');expect(value).not.toHaveProperty('catalog');expect(value).toMatchObject({version:2,voice_ids:['stock-a'],clear_key:false});});
 it('submits only an explicit write-only replacement or clear action',()=>{expect(voiceSettingsPayload(settings,'synthetic-only-key',false)).toHaveProperty('api_key','synthetic-only-key');expect(voiceSettingsPayload(settings,'',true)).toMatchObject({clear_key:true});});
 it('offers supplied stock choices and an empty password input, with no arbitrary voice or endpoint entry',()=>{const html=renderToStaticMarkup(<VoiceSettingsEditor initial={{...settings,enabled:true}} close={()=>{}} save={async()=>{}}/>);expect(html).toContain('Public stock voice');expect(html).toContain('2026-12-31');expect(html).toContain('type="password"');expect(html).toContain('Leave empty to keep saved key');expect(html).not.toContain('Manual voice');expect(html).not.toContain('Endpoint URL');expect(html).toMatch(/disabled="">Save voice integration/);});
});
