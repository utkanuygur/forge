// Settings in userData/settings.json. The API key is encrypted with safeStorage (Keychain backed)
// and stored only as ciphertext; it is never written in plain text or logged.
const fs = require('fs');
const { safeStorage } = require('electron');
const paths = require('./paths');

function read() {
  try {
    return JSON.parse(fs.readFileSync(paths.settingsFile(), 'utf8'));
  } catch {
    return {};
  }
}

function write(data) {
  fs.mkdirSync(paths.userData(), { recursive: true });
  const file = paths.settingsFile();
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(data, null, 2), { mode: 0o600 });
  fs.renameSync(`${file}.tmp`, file);
}

function apiKey() {
  const enc = read().anthropicApiKeyEncrypted;
  if (!enc) return '';
  try {
    return safeStorage.decryptString(Buffer.from(enc, 'base64'));
  } catch {
    return '';
  }
}

// What the server needs: { apiKey, defaultModel, graderModel }
function forServer() {
  const s = read();
  return { apiKey: apiKey(), defaultModel: s.defaultModel || '', graderModel: s.graderModel || '' };
}

// What the Settings window may see: never the key itself.
function forWindow() {
  const s = read();
  const key = s.anthropicApiKeyEncrypted ? apiKey() : '';
  return {
    hasKey: Boolean(key),
    keyHint: key ? key.slice(-4) : '',
    defaultModel: s.defaultModel || '',
    graderModel: s.graderModel || '',
    firstRun: !s.anthropicApiKeyEncrypted && !s.dismissedKeyPrompt,
    encryptionAvailable: safeStorage.isEncryptionAvailable(),
  };
}

// update: { apiKey?: string (empty keeps the saved one), clearKey?: bool, defaultModel, graderModel }
function save(update) {
  const s = read();
  if (update.clearKey) delete s.anthropicApiKeyEncrypted;
  const key = String(update.apiKey || '').trim();
  if (key) {
    if (!safeStorage.isEncryptionAvailable()) throw new Error('Secure storage is not available, so the key was not saved.');
    s.anthropicApiKeyEncrypted = safeStorage.encryptString(key).toString('base64');
  }
  s.defaultModel = String(update.defaultModel || '').trim();
  s.graderModel = String(update.graderModel || '').trim();
  s.dismissedKeyPrompt = true;
  write(s);
}

function dismissFirstRun() {
  const s = read();
  if (!s.dismissedKeyPrompt) write({ ...s, dismissedKeyPrompt: true });
}

module.exports = { forServer, forWindow, save, apiKey, dismissFirstRun };
