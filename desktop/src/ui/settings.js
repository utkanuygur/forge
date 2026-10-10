const $ = (id) => document.getElementById(id);
let state = null;

function status(text, kind) {
  $('status').textContent = text;
  $('status').className = `status ${kind || ''}`;
}

function busy(on) {
  for (const id of ['save', 'test', 'cancel', 'clear']) $(id).disabled = on;
}

async function load() {
  state = await window.forgeSettings.get();
  $('first-run').hidden = !state.firstRun;
  $('default-model').value = state.defaultModel;
  $('grader-model').value = state.graderModel;
  $('clear').hidden = !state.hasKey;
  $('key').value = '';
  $('key').placeholder = state.hasKey ? `Saved key ending in ${state.keyHint}. Type to replace it.` : 'sk-ant-...';
  if (!state.encryptionAvailable) status('Secure storage is not available on this Mac, so a key cannot be saved.', 'err');
  $('key').focus();
}

async function save(extra) {
  busy(true);
  status('Saving and restarting the server...');
  const res = await window.forgeSettings.save({
    apiKey: $('key').value,
    defaultModel: $('default-model').value,
    graderModel: $('grader-model').value,
    ...extra,
  });
  busy(false);
  if (res.ok) {
    await load();
    status('Saved. The server restarted with the new settings.', 'ok');
  } else {
    status(res.error, 'err');
  }
}

$('form').addEventListener('submit', (e) => {
  e.preventDefault();
  save({});
});
$('clear').addEventListener('click', () => {
  $('key').value = '';
  save({ clearKey: true });
});
$('cancel').addEventListener('click', () => window.forgeSettings.close());
$('test').addEventListener('click', async () => {
  if (!$('key').value && !(state && state.hasKey)) return status('Enter a key to test.', 'err');
  busy(true);
  status('Testing the key...');
  const res = await window.forgeSettings.test($('key').value);
  busy(false);
  status(res.message, res.ok ? 'ok' : 'err');
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') window.forgeSettings.close();
});

load();
