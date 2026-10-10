// Pure helpers for API keys kept in settings.json (encrypted with safeStorage by settings.js) and for the
// one time move of model choices from the old native Settings window into the server's settings.
// No Electron imports here, so `npm test` covers this file with node:test.

// Environment variable names a key may be stored under: what an OpenAI compatible endpoint's "key variable"
// names in the web Settings page, e.g. VLLM_API_KEY.
const NAME_RE = /^[A-Z_][A-Z0-9_]*$/;
const BLOCKED = new Set(['PATH', 'HOME', 'USER', 'SHELL', 'TMPDIR', 'PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS']);

function keyNameError(name) {
  if (!NAME_RE.test(name || '')) return 'A key variable must look like MY_ENDPOINT_KEY: capital letters, digits and underscores.';
  if (BLOCKED.has(name) || name.startsWith('FORGE_') || name.startsWith('DYLD_') || name.startsWith('LD_')) {
    return `${name} cannot hold an API key.`;
  }
  return null;
}

// stored: the parsed settings.json. update: { apiKey, clearKey, openaiKey, clearOpenaiKey, add: {NAME: key},
// remove: [NAME] }. encrypt(text) -> base64. Returns a new settings object; throws Error with a plain message.
function applyKeyUpdate(stored, update, encrypt) {
  const s = { ...stored, secretsEncrypted: { ...(stored.secretsEncrypted || {}) } };
  if (update.clearKey) delete s.anthropicApiKeyEncrypted;
  const claude = String(update.apiKey || '').trim();
  if (claude) s.anthropicApiKeyEncrypted = encrypt(claude);
  if (update.clearOpenaiKey) delete s.secretsEncrypted.OPENAI_API_KEY;
  const openai = String(update.openaiKey || '').trim();
  if (openai) s.secretsEncrypted.OPENAI_API_KEY = encrypt(openai);
  for (const name of update.remove || []) delete s.secretsEncrypted[name];
  for (const [rawName, rawValue] of Object.entries(update.add || {})) {
    const name = String(rawName).trim();
    const value = String(rawValue || '').trim();
    if (!name && !value) continue;
    const err = name === 'ANTHROPIC_API_KEY' ? 'Use the Claude API key field for ANTHROPIC_API_KEY.' : keyNameError(name);
    if (err) throw new Error(err);
    if (!value) throw new Error(`Enter the key for ${name}.`);
    s.secretsEncrypted[name] = encrypt(value);
  }
  if (!Object.keys(s.secretsEncrypted).length) delete s.secretsEncrypted;
  return s;
}

// The environment the server gets for these keys, plus FORGE_DESKTOP_SECRETS naming them so the web
// Settings page can say a key comes from the Forge app. secrets: { NAME: plain key }.
function secretEnv(apiKey, secrets) {
  const env = {};
  if (apiKey) env.ANTHROPIC_API_KEY = apiKey;
  for (const [name, value] of Object.entries(secrets || {})) {
    if (value && !keyNameError(name)) env[name] = value;
  }
  env.FORGE_DESKTOP_SECRETS = Object.keys(env).join(',');
  return env;
}

// Model choices saved by the old native Settings window, as a PATCH /v1/settings body, or null.
function legacyModels(stored) {
  const body = {};
  if (stored.defaultModel) body.default_model = stored.defaultModel;
  if (stored.graderModel) body.grader_model = stored.graderModel;
  return Object.keys(body).length ? body : null;
}

function withoutLegacyModels(stored) {
  const s = { ...stored };
  delete s.defaultModel;
  delete s.graderModel;
  return s;
}

module.exports = { keyNameError, applyKeyUpdate, secretEnv, legacyModels, withoutLegacyModels };
