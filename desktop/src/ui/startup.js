// Renders the state the main process sends: { title, steps: [{label, state, detail}], problem }.
const $ = (id) => document.getElementById(id);

window.forgeStartup.onUpdate((state) => {
  $('title').textContent = state.title || 'Starting Forge';
  const steps = $('steps');
  steps.replaceChildren(...state.steps.map((s) => {
    const li = document.createElement('li');
    li.className = s.state;
    const dot = document.createElement('span');
    dot.className = 'dot';
    const body = document.createElement('div');
    body.textContent = s.label;
    if (s.detail) {
      const d = document.createElement('div');
      d.className = 'detail';
      d.textContent = s.detail;
      body.appendChild(d);
    }
    li.append(dot, body);
    return li;
  }));

  const p = state.problem;
  $('problem').classList.toggle('show', Boolean(p));
  steps.style.display = p ? 'none' : '';
  if (!p) return;
  $('problem-title').textContent = p.title;
  $('problem-message').textContent = p.message;
  $('problem-detail').textContent = p.detail || '';
  $('problem-detail').style.display = p.detail ? '' : 'none';
  $('actions').replaceChildren(...p.actions.map((a, i) => {
    if (a.spacer) {
      const s = document.createElement('span');
      s.className = 'spacer';
      return s;
    }
    const b = document.createElement('button');
    b.textContent = a.label;
    if (a.primary) b.className = 'primary';
    b.addEventListener('click', () => window.forgeStartup.action(a.id));
    return b;
  }));
  const primary = $('actions').querySelector('button.primary');
  if (primary) primary.focus();
});
