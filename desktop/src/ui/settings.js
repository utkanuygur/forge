const $ = (id) => document.getElementById(id);
let state = null;
let removing = new Set();

function status(text, kind) {
  $('status').textContent = text;
  $('status').className = `status ${kind || ''}`;
}

function busy(on) {
  for (const id of ['save', 'test', 'cancel', 'clear', 'clear-openai', 'open-all']) $(id).disabled = on;
}

function renderKeys() {
  const list = $('endpoint-keys');
  list.textContent = '';
  for (const name of state.endpointKeys) {
    const li = document.createElement('li');
    const label = document.createElement('code');
    label.textContent = name;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = removing.has(name) ? 'Keep' : 'Remove';
    btn.setAttribute('aria-label', `${removing.has(name) ? 'Keep' : 'Remove'} ${name}`);
    btn.addEventListener('click', () => {
      if (removing.has(name)) removing.delete(name);
      else removing.add(name);
      renderKeys();
    });
    if (removing.has(name)) li.className = 'removing';
    li.append(label, btn);
    list.append(li);
  }
  list.hidden = state.endpointKeys.length === 0;
}

async function load() {
  state = await window.forgeSettings.get();
  removing = new Set();
  $('first-run').hidden = !state.firstRun;
  $('clear').hidden = !state.hasKey;
  $('clear-openai').hidden = !state.hasOpenaiKey;
  for (const id of ['key', 'openai-key', 'add-name', 'add-value']) $(id).value = '';
  $('key').placeholder = state.hasKey ? `Saved key ending in ${state.keyHint}. Type to replace it.` : 'sk-ant-...';
  $('openai-key').placeholder = state.hasOpenaiKey ? 'Saved. Type to replace it.' : 'Optional';
  renderKeys();
  if (!state.encryptionAvailable) status('Secure storage is not available on this Mac, so a key cannot be saved.', 'err');
  $('key').focus();
}

async function save(extra) {
  busy(true);
  status('Saving and restarting the server...');
  const name = $('add-name').value.trim().toUpperCase();
  const res = await window.forgeSettings.save({
    apiKey: $('key').value,
    openaiKey: $('openai-key').value,
    add: name || $('add-value').value ? { [name]: $('add-value').value } : {},
    remove: [...removing],
    ...extra,
  });
  busy(false);
  if (res.ok) {
    await load();
    status('Saved. The server restarted with the new keys.', 'ok');
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
$('clear-openai').addEventListener('click', () => {
  $('openai-key').value = '';
  save({ clearOpenaiKey: true });
});
$('cancel').addEventListener('click', () => window.forgeSettings.close());
$('open-all').addEventListener('click', () => window.forgeSettings.openAll());
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
