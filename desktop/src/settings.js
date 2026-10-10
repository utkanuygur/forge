// Settings in userData/settings.json. API keys are encrypted with safeStorage (Keychain backed) and stored
// only as ciphertext; they are never written in plain text or logged. Every other setting lives in the
// server (web Settings page, /v1/settings); see keys.js for the pure helpers.
const fs = require('fs');
const { safeStorage } = require('electron');
const paths = require('./paths');
const keys = require('./keys');

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

function decrypt(enc) {
  if (!enc) return '';
  try {
    return safeStorage.decryptString(Buffer.from(enc, 'base64'));
  } catch {
    return '';
  }
}

function encrypt(text) {
  if (!safeStorage.isEncryptionAvailable()) throw new Error('Secure storage is not available, so the key was not saved.');
  return safeStorage.encryptString(text).toString('base64');
}

function apiKey() {
  return decrypt(read().anthropicApiKeyEncrypted);
}

function secrets() {
  const out = {};
  for (const [name, enc] of Object.entries(read().secretsEncrypted || {})) {
    const value = decrypt(enc);
    if (value) out[name] = value;
  }
  return out;
}

// What the server needs: { apiKey, secrets: { NAME: key } }
function forServer() {
  return { apiKey: apiKey(), secrets: secrets() };
}

// What the Settings window may see: never a key itself.
function forWindow() {
  const s = read();
  const key = s.anthropicApiKeyEncrypted ? apiKey() : '';
  const names = Object.keys(s.secretsEncrypted || {});
  return {
    hasKey: Boolean(key),
    keyHint: key ? key.slice(-4) : '',
    hasOpenaiKey: names.includes('OPENAI_API_KEY'),
    endpointKeys: names.filter((n) => n !== 'OPENAI_API_KEY').sort(),
    firstRun: !s.anthropicApiKeyEncrypted && !s.dismissedKeyPrompt,
    encryptionAvailable: safeStorage.isEncryptionAvailable(),
  };
}

// update: see keys.applyKeyUpdate
function save(update) {
  write({ ...keys.applyKeyUpdate(read(), update, encrypt), dismissedKeyPrompt: true });
}

function dismissFirstRun() {
  const s = read();
  if (!s.dismissedKeyPrompt) write({ ...s, dismissedKeyPrompt: true });
}

// Models chosen in the old native window move to the server once (PATCH /v1/settings), then leave this file.
function legacyModels() {
  return keys.legacyModels(read());
}

function dropLegacyModels() {
  write(keys.withoutLegacyModels(read()));
}

module.exports = { forServer, forWindow, save, apiKey, dismissFirstRun, legacyModels, dropLegacyModels };
