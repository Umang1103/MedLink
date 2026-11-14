(() => {
  const search = document.getElementById('medicine-search');
  const results = document.getElementById('search-results');
  const status = document.getElementById('search-status');
  const selected = document.getElementById('selected-medicines');
  const chosen = new Set();
  let timer, controller, sequence = 0;
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function refresh() {
    document.getElementById('empty-selection').hidden = chosen.size > 0;
    document.getElementById('create-prescription').disabled = chosen.size === 0;
  }
  function add(medicine) {
    if (chosen.has(medicine.pzn)) return;
    chosen.add(medicine.pzn);
    const card = element('fieldset', null, 'medicine-selection');
    card.append(element('legend', medicine.product_name));
    card.append(element('p', `PZN ${medicine.pzn} · ${medicine.active_ingredient} · ${medicine.dosage_form}`, 'card-sub'));
    if (medicine.images?.[0]?.url) {
      const img = document.createElement('img');
      img.src = medicine.images[0].url; img.alt = medicine.product_name;
      img.className = 'medicine-thumbnail'; card.append(img);
    }
    const hidden = document.createElement('input');
    hidden.type = 'hidden'; hidden.name = 'pzn'; hidden.value = medicine.pzn; card.append(hidden);
    const grid = element('div', null, 'prescribe-grid');
    for (const [name, title, type, value] of [
      ['units_per_dose', 'Units per dose (e.g. tablets)', 'number', medicine.units_per_dose ?? '1'],
      ['times_per_day', 'Times per day', 'number', medicine.times_per_day ?? '1'],
      ['instructions', 'Additional instructions (optional)', 'text', medicine.instructions ?? '']
    ]) {
      const wrapper = element('div');
      const id = `${name}-${medicine.pzn}`;
      const label = element('label', title); label.htmlFor = id;
      const input = document.createElement('input');
      input.id = id; input.name = name; input.type = type; input.value = value;
      if (type === 'number') {
        input.required = true;
        input.min = name === 'units_per_dose' ? '0.001' : '1';
        input.max = name === 'units_per_dose' ? '1000' : '24';
        input.step = name === 'units_per_dose' ? '0.001' : '1';
      } else input.maxLength = 1000;
      wrapper.append(label, input); grid.append(wrapper);
    }
    card.append(grid);
    const remove = element('button', 'Remove medicine', 'link'); remove.type = 'button';
    remove.addEventListener('click', () => { chosen.delete(medicine.pzn); card.remove(); refresh(); });
    card.append(remove); selected.append(card); refresh();
  }
  search.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); results.querySelector('button:not(:disabled)')?.focus(); }
  });
  search.addEventListener('input', () => {
    clearTimeout(timer); controller?.abort();
    const version = ++sequence;
    results.replaceChildren();
    const query = search.value.trim();
    if (!query) { status.textContent = 'Type at least one character.'; return; }
    status.textContent = 'Searching…';
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch(`/api/medicines?q=${encodeURIComponent(query)}&limit=20`, { signal: controller.signal });
        if (!response.ok) throw new Error('search failed');
        const data = await response.json();
        if (version !== sequence) return;
        status.textContent = data.total ? `${data.total} matches. Showing ${data.results.length}; type more to narrow the results.` : 'No matching medicines.';
        for (const medicine of data.results) {
          const button = element('button', `${medicine.product_name} · PZN ${medicine.pzn}`, 'medicine-result');
          button.type = 'button'; button.disabled = chosen.has(medicine.pzn);
          button.addEventListener('click', () => {
            add(medicine); results.replaceChildren(); search.value = '';
            status.textContent = 'Medicine added. Search again to add another.'; search.focus();
          });
          results.append(button);
        }
      } catch (error) {
        if (error.name !== 'AbortError' && version === sequence) status.textContent = 'Medicine search unavailable. Please try again.';
      }
    }, 180);
  });
  JSON.parse(document.getElementById('initial-medicines').textContent).forEach(add);
  refresh();
})();
