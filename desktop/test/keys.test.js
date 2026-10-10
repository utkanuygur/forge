// Unit tests for the pure key helpers: `npm test` in desktop/ (node --test, no Electron needed).
const test = require('node:test');
const assert = require('node:assert/strict');
const keys = require('../src/keys');

const enc = (t) => `enc(${t})`;

test('key variable names are checked', () => {
  assert.equal(keys.keyNameError('VLLM_API_KEY'), null);
  assert.match(keys.keyNameError('vllm-key'), /capital letters/);
  assert.match(keys.keyNameError('PATH'), /cannot hold/);
  assert.match(keys.keyNameError('FORGE_DATA_DIR'), /cannot hold/);
  assert.match(keys.keyNameError('DYLD_INSERT_LIBRARIES'), /cannot hold/);
});

test('applyKeyUpdate sets, replaces and removes keys without touching other fields', () => {
  let s = keys.applyKeyUpdate({ dismissedKeyPrompt: true }, { apiKey: ' sk-ant-1 ', openaiKey: 'o1', add: { VLLM_KEY: 'v1' } }, enc);
  assert.deepEqual(s, {
    dismissedKeyPrompt: true,
    anthropicApiKeyEncrypted: 'enc(sk-ant-1)',
    secretsEncrypted: { OPENAI_API_KEY: 'enc(o1)', VLLM_KEY: 'enc(v1)' },
  });
  s = keys.applyKeyUpdate(s, { apiKey: '', remove: ['VLLM_KEY'] }, enc); // empty keeps the saved Claude key
  assert.equal(s.anthropicApiKeyEncrypted, 'enc(sk-ant-1)');
  assert.deepEqual(s.secretsEncrypted, { OPENAI_API_KEY: 'enc(o1)' });
  s = keys.applyKeyUpdate(s, { clearKey: true, clearOpenaiKey: true, add: { '': '' } }, enc);
  assert.deepEqual(s, { dismissedKeyPrompt: true });
});

test('applyKeyUpdate refuses bad names and missing values', () => {
  assert.throws(() => keys.applyKeyUpdate({}, { add: { 'bad name': 'x' } }, enc), /capital letters/);
  assert.throws(() => keys.applyKeyUpdate({}, { add: { VLLM_KEY: '' } }, enc), /Enter the key for VLLM_KEY/);
  assert.throws(() => keys.applyKeyUpdate({}, { add: { ANTHROPIC_API_KEY: 'x' } }, enc), /Claude API key field/);
});

test('secretEnv passes keys to the server and names them', () => {
  assert.deepEqual(keys.secretEnv('sk', { OPENAI_API_KEY: 'o', VLLM_KEY: 'v', PATH: 'evil' }), {
    ANTHROPIC_API_KEY: 'sk',
    OPENAI_API_KEY: 'o',
    VLLM_KEY: 'v',
    FORGE_DESKTOP_SECRETS: 'ANTHROPIC_API_KEY,OPENAI_API_KEY,VLLM_KEY',
  });
  assert.deepEqual(keys.secretEnv('', {}), { FORGE_DESKTOP_SECRETS: '' });
});

test('models from the old native window become a settings PATCH once', () => {
  const old = { defaultModel: 'fake:hello', graderModel: '', anthropicApiKeyEncrypted: 'x' };
  assert.deepEqual(keys.legacyModels(old), { default_model: 'fake:hello' });
  assert.equal(keys.legacyModels({ anthropicApiKeyEncrypted: 'x' }), null);
  assert.deepEqual(keys.withoutLegacyModels(old), { anthropicApiKeyEncrypted: 'x' });
});
