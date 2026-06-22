  const roster = new Map();              // component_id -> {name, status, engine, group, sub}
  const $ = id => document.getElementById(id);
  const out = $('output'), modelSel = $('model'), promptEl = $('prompt'), sendBtn = $('send');
  const sidebar = $('sidebar'), connEl = $('conn'), countsEl = $('counts'), alertsEl = $('alerts');
  const GROUPS = [['mode','Orchestrations'], ['agent','Agents'], ['demo','Demo']];
  let sessionId = 'sess-' + Math.random().toString(36).slice(2, 10);  // conversation thread
  const selected = new Set();   // checked agent names = roles (multi-select)
  const runs = new Map();       // request_id -> {model, hdr, content, t0, tfirst, chunks}
  const rolesMap = {};          // role name -> system prompt (role×model grid)
  const reps = {};              // server_group -> representative agent (bus target)
  const pickedModels = new Set(); // selected model server-groups (grid columns)
  const modesMap = {};          // mode name -> {agents, structure, description} (orchestration wiring)
  const liveAgents = new Set(); // agents seen firing in the active mode run (topology overlay)
  let liveMode = null;          // which orchestration is currently running

  function groupOf(engine) {
    if (engine === 'cofiswarm-mode') return 'mode';
    if (engine === 'llama' || engine === 'mlx') return 'agent';
    return 'demo';
  }
  function applyPresence(d) {
    const info = d.info || {};
    roster.set(d.component_id, {
      name: info.name || d.model || d.component_id, status: d.status,
      engine: info.engine || '?', group: groupOf(info.engine),
      model: info.server_group || info.model || info.engine || '?'
    });
    render();
  }
  function render() {
    const online = [...roster.values()].filter(r => r.status === 'online');
    const byGroup = g => online.filter(r => r.group === g).sort((a,b)=>a.name.localeCompare(b.name));
    sidebar.innerHTML = '';
    for (const [g, label] of GROUPS) {
      const items = byGroup(g);
      if (!items.length) continue;
      const h = document.createElement('div'); h.className = 'group-label';
      h.innerHTML = `${label} <span class="n">${items.length}</span>`; sidebar.appendChild(h);
      for (const r of items) {
        const el = document.createElement('div'); el.className = 'item';
        if (g === 'mode') {
          // orchestrations: single-select (click → dropdown)
          el.innerHTML = `<span class="dot online"></span>${r.name}<span class="sub">${r.model}</span>`;
          if (r.name === modelSel.value && !selected.size) el.classList.add('sel');
          el.onclick = () => { modelSel.value = r.name; selected.clear(); render(); };
        } else {
          // agents/demo: multi-select checkbox + the model→engine connection inline
          const ck = selected.has(r.name) ? 'checked' : '';
          el.innerHTML = `<input type="checkbox" class="pick" ${ck}>` +
            `<span class="dot online"></span>${r.name}` +
            `<span class="sub">${r.model} · ${r.engine}</span>`;
          el.querySelector('.pick').onchange = e => {
            if (e.target.checked) selected.add(r.name); else selected.delete(r.name);
            updateSendLabel();
          };
        }
        sidebar.appendChild(el);
      }
    }
    const cur = modelSel.value; modelSel.innerHTML = '';
    for (const [g, label] of GROUPS) {
      const items = byGroup(g); if (!items.length) continue;
      const og = document.createElement('optgroup'); og.label = label;
      for (const r of items) { const o = document.createElement('option'); o.value = r.name; o.textContent = r.name; og.appendChild(o); }
      modelSel.appendChild(og);
    }
    if (online.some(r => r.name === cur)) modelSel.value = cur;
    const na = online.filter(r=>r.group==='agent').length, nm = online.filter(r=>r.group==='mode').length;
    countsEl.textContent = `${na} agents · ${nm} modes online`;
    updateSendLabel();
  }
  function gridCells() {
    return (selected.size && pickedModels.size) ? selected.size * pickedModels.size : selected.size;
  }
  function updateSendLabel() {
    if (runs.size) { sendBtn.textContent = `Stop (${runs.size})`; sendBtn.className = 'stop'; return; }
    sendBtn.className = '';
    const c = gridCells();
    sendBtn.textContent = c > 1 ? `Send ×${c}` : 'Send';
  }
  async function loadRoles() {
    let d; try { d = await (await fetch('/roles')).json(); } catch (e) { return; }
    (d.roles || []).forEach(r => { rolesMap[r.name] = r.system_prompt || ''; });
    const bar = $('modelbar');
    if (!(d.models || []).length) { bar.innerHTML = '<span class="mlbl">check agents to prompt them on their own model</span>'; return; }
    bar.innerHTML = '<span class="mlbl">run checked roles on model(s):</span>';
    d.models.forEach(m => {
      reps[m.server_group] = m.representative;
      const c = document.createElement('span'); c.className = 'chip'; c.textContent = m.server_group;
      c.onclick = () => {
        c.classList.toggle('on');
        pickedModels.has(m.server_group) ? pickedModels.delete(m.server_group) : pickedModels.add(m.server_group);
        updateSendLabel();
      };
      bar.appendChild(c);
    });
  }

  async function loadModes() {
    let d; try { d = await (await fetch('/modes')).json(); } catch (e) { return; }
    Object.assign(modesMap, d);   // mode -> {agents, structure, ...}; empty on failure = name-only fallback
  }

  // ---- per-run output lanes (one per selected agent) ----
  function laneFor(rid, model) {
    let r = runs.get(rid);
    if (r) return r;
    const lane = document.createElement('div'); lane.className = 'lane';
    const hdr = document.createElement('div'); hdr.className = 'lane-hdr'; hdr.textContent = `▸ ${model} · running…`;
    const content = document.createElement('div'); content.className = 'lane-body';
    lane.appendChild(hdr); lane.appendChild(content); out.appendChild(lane);
    r = { model, hdr, content, t0: performance.now(), tfirst: null, chunks: 0 };
    runs.set(rid, r); out.scrollTop = out.scrollHeight; updateSendLabel();
    return r;
  }
  function finishLane(rid, label, tok) {
    const r = runs.get(rid); if (!r) return;
    const secs = (performance.now() - r.t0) / 1000;
    let stat = `${label} · ${secs.toFixed(1)}s`;
    if (tok && tok.tokens != null) {
      const tps = tok.tokens_per_sec != null ? tok.tokens_per_sec : (secs > 0 ? tok.tokens / secs : 0);
      stat += ` · ${tok.tokens} tok · ${tps.toFixed(1)} tok/s`;
    }
    r.hdr.textContent = `▸ ${r.model}  (${stat})`;
    runs.delete(rid); updateSendLabel();
    if (r.model === liveMode) {                 // orchestration finished: drop the live overlay
      liveAgents.clear(); liveMode = null;
      if ($('topology').classList.contains('show')) renderTopology();
    }
  }

  function addAlert(msg) {
    const el = document.createElement('div'); el.className = 'alert';
    const ts = new Date().toLocaleTimeString();
    el.innerHTML = `<span class="t">${ts}</span>⚠ ${msg}`; alertsEl.prepend(el);
  }

  // ---- websocket ----
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { connEl.textContent = 'connected'; connEl.className = 'up'; };
  ws.onclose = () => { connEl.textContent = 'disconnected'; connEl.className = ''; };
  ws.onmessage = (ev) => {
    const { type, data } = JSON.parse(ev.data);
    if (type === 'snapshot') data.forEach(applyPresence);
    else if (type === 'presence') applyPresence(data);
    else if (type === 'alert') addAlert(data.message);
    else if (type === 'start') laneFor(data.request_id, data.model);
    else if (type === 'token') {
      const r = runs.get(data.request_id); if (!r) return;
      if (data.error === 'cancelled') finishLane(data.request_id, 'cancelled', data);
      else if (data.error) { addAlert(`${r.model}: ${data.error}`); finishLane(data.request_id, 'error', data); }
      else if (data.done) finishLane(data.request_id, 'done', data);
      else { if (r.tfirst === null) r.tfirst = performance.now(); r.chunks++; r.content.textContent += data.text;
             trackLiveAgents(r.model, data.text); out.scrollTop = out.scrollHeight; }
    }
  };

  // Scan dispatch's agent markers (■ agent / [stage N/T · agent] / [router → a, b]) to
  // light up the topology as an orchestration run flows through its agents.
  function trackLiveAgents(model, text) {
    if (!model || !model.startsWith('swarm-') || !text) return;
    liveMode = model;
    let m, hit = false;
    const re = /■\s+([\w-]+)|·\s+([\w-]+)\]|router → ([^\]]+)/g;
    while ((m = re.exec(text))) {
      if (m[1]) { if (m[1] !== 'synthesis') liveAgents.add(m[1]); hit = true; }
      else if (m[2]) { liveAgents.add(m[2]); hit = true; }
      else if (m[3]) { m[3].split(',').forEach(a => liveAgents.add(a.trim())); hit = true; }
    }
    if (hit && $('topology').classList.contains('show')) renderTopology();
  }

  function mkYou(targets, prompt) {
    const you = document.createElement('div'); you.className = 'you';
    you.textContent = `You → ${targets}: ${prompt}`; out.appendChild(you);
  }
  function send() {
    if (runs.size) { cancel(); return; }              // button doubles as Stop while running
    const prompt = promptEl.value.trim(); if (!prompt) return;
    const models = [...pickedModels];
    if (selected.size && models.length) {             // ── role × model grid ──
      if (gridCells() > 12) { addAlert(`grid too large (${gridCells()} cells) — pick fewer`); return; }
      mkYou(`${[...selected].join(', ')} × ${models.join(', ')}`, prompt);
      for (const role of selected) for (const m of models) {
        const target = reps[m]; if (!target) continue;
        ws.send(JSON.stringify({ action: 'prompt', model: target, prompt,
          system: rolesMap[role] || null, label: `${role}@${m}`, session_id: sessionId }));
      }
    } else {                                          // multi-select (native model) or single
      const tgts = selected.size ? [...selected] : (modelSel.value ? [modelSel.value] : []);
      if (!tgts.length) return;
      mkYou(tgts.join(', '), prompt);
      for (const model of tgts) ws.send(JSON.stringify({ action: 'prompt', model, prompt, session_id: sessionId }));
    }
    promptEl.value = ''; updateSendLabel();
  }
  loadRoles();
  loadModes();
  function cancel() {
    for (const rid of runs.keys()) ws.send(JSON.stringify({ action: 'cancel', request_id: rid }));
  }
  $('newBtn').onclick = () => {
    cancel();
    sessionId = 'sess-' + Math.random().toString(36).slice(2, 10);
    out.innerHTML = ''; runs.clear(); liveAgents.clear(); liveMode = null; updateSendLabel();
  };
  sendBtn.onclick = send;
  promptEl.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });

  // ---- history ----
  $('histBtn').onclick = async () => {
    const h = $('history');
    if (h.classList.contains('show')) { h.classList.remove('show'); return; }
    let rows = [];
    try { rows = await (await fetch('/history?limit=40')).json(); }
    catch (e) { addAlert('failed to load history'); return; }
    h.innerHTML = '';
    rows.filter(r => r.kind === 'run').reverse().forEach(rec => {
      const el = document.createElement('div'); el.className = 'hrow';
      const t = new Date(rec.started_at * 1000).toLocaleTimeString();
      const tok = rec.tokens != null ? rec.tokens + 'tok' : '–';
      el.innerHTML = `<span class="t">${t}</span><b>${rec.model}</b> · `
        + `<span class="st-${rec.status}">${rec.status}</span> · ${tok} · ${rec.latency_ms}ms`
        + `<div class="hp">${(rec.prompt || '').slice(0, 90)}</div>`;
      el.onclick = () => {
        const lane = document.createElement('div'); lane.className = 'lane';
        lane.innerHTML = `<div class="lane-hdr">▸ replay ${rec.model} (${t})</div>`;
        const body = document.createElement('div'); body.className = 'lane-body';
        body.textContent = `You: ${rec.prompt}\n${rec.text || ''}`;
        lane.appendChild(body); out.appendChild(lane);
        out.scrollTop = out.scrollHeight; h.classList.remove('show');
      };
      h.appendChild(el);
    });
    if (!h.children.length) h.innerHTML = '<div class="hrow">no runs recorded yet</div>';
    h.classList.add('show');
  };

  // ---- stats ----
  $('statsBtn').onclick = async () => {
    const s = $('stats');
    if (s.classList.contains('show')) { s.classList.remove('show'); return; }
    let rows = [];
    try { rows = await (await fetch('/stats')).json(); }
    catch (e) { addAlert('failed to load stats'); return; }
    if (!rows.length) { s.innerHTML = '<div>no runs recorded yet</div>'; s.classList.add('show'); return; }
    const head = '<tr><th>model</th><th>runs</th><th>err</th><th>avg ms</th><th>avg tok</th><th>tok/s</th></tr>';
    const body = rows.map(r => `<tr><td>${r.model}</td><td>${r.runs}</td>`
      + `<td class="${r.errors ? 'err' : ''}">${r.errors}</td>`
      + `<td>${r.avg_latency_ms ?? '–'}</td><td>${r.avg_tokens ?? '–'}</td>`
      + `<td>${r.avg_tps ?? '–'}</td></tr>`).join('');
    s.innerHTML = `<table>${head}${body}</table>`;
    s.classList.add('show');
  };

  // ---- topology panel (agent → model wiring + orchestration → agent relationships) ----
  function groupOfAgent(name) {                 // role name -> its model server-group (if online)
    for (const r of roster.values()) if (r.group === 'agent' && r.name === name) return r.model;
    return null;
  }
  function renderTopology() {
    const t = $('topology');
    const online = [...roster.values()].filter(r => r.status === 'online');
    const agents = online.filter(r => r.group === 'agent');
    const modes = online.filter(r => r.group === 'mode');
    const byModel = {};
    agents.forEach(a => { (byModel[a.model] = byModel[a.model] || { engine: a.engine, names: [] }).names.push(a.name); });
    let html = '<div class="topo-hdr">● NATS middle man → model servers</div>';
    Object.keys(byModel).sort().forEach(m => {
      const g = byModel[m];
      html += `<div class="topo-node"><b>${m}</b> <span class="topo-eng">(${g.engine}) · ${g.names.length} role(s)</span>`
            + `<div class="topo-roles">${g.names.sort().join(' · ')}</div></div>`;
    });
    if (modes.length) {
      html += '<div class="topo-hdr">● orchestrations → agents</div>';
      modes.slice().sort((a, b) => a.name.localeCompare(b.name)).forEach(x => {
        const key = x.name.replace(/^swarm-/, '');         // swarm-pipeline -> pipeline
        const m = modesMap[key] || {};
        const struct = m.structure ? ` <span class="topo-struct">(${m.structure})</span>` : '';
        const isLive = liveMode === x.name;
        const roles = (m.agents || []).map(a => {
          const grp = groupOfAgent(a);
          const lbl = grp ? `${a} <span class="topo-grp">→ ${grp}</span>` : a;
          return (isLive && liveAgents.has(a)) ? `<span class="topo-live">${lbl}</span>` : lbl;
        });
        const body = roles.length ? roles.join(' · ') : '<span class="topo-grp">no mapping</span>';
        html += `<div class="topo-node"><b>${x.name}</b>${struct}<div class="topo-roles">${body}</div></div>`;
      });
    }
    t.innerHTML = html || '<div>no components online</div>';
  }
  $('topoBtn').onclick = () => {
    const t = $('topology');
    if (t.classList.contains('show')) { t.classList.remove('show'); return; }
    renderTopology();
    t.classList.add('show');
  };

  // ---- draggable vertical splitter (resize roster vs. output) ----
  (function () {
    const sp = $('splitter');
    let dragging = false;
    sp.addEventListener('mousedown', e => {
      dragging = true; sp.classList.add('drag');
      document.body.style.userSelect = 'none'; e.preventDefault();
    });
    window.addEventListener('mousemove', e => {
      if (!dragging) return;
      const w = Math.min(Math.max(e.clientX, 160), 600);   // clamp sidebar width
      document.documentElement.style.setProperty('--sb', w + 'px');
    });
    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false; sp.classList.remove('drag'); document.body.style.userSelect = '';
    });
  })();
