/* HF Range Tracker — frontend JS */

// ── Map setup ─────────────────────────────────────────────────────────────

const map = L.map('map', { zoomControl: true }).setView([52.37, 4.9], 4);

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
  maxZoom: 18,
}).addTo(map);

// TX marker (Amsterdam)
let txMarker = null;
let receiverMarkers = {};   // host -> {marker, line}
let activeHighlight = null; // {marker, line} for the receiver currently being sampled

function placeOrMoveTX(lat, lon, callsign) {
  if (txMarker) map.removeLayer(txMarker);
  const icon = L.divIcon({
    className: '',
    html: `<div style="
      width:14px;height:14px;background:#ef4444;border:2px solid #fff;
      border-radius:50%;box-shadow:0 0 6px #ef4444
    "></div>`,
    iconAnchor: [7, 7],
  });
  txMarker = L.marker([lat, lon], { icon })
    .addTo(map)
    .bindPopup(`<b>${callsign}</b><br>Your TX location`);
}

function addReceiverMarker(r, heard) {
  const host = r.receiver.host;

  // Remove old marker if re-checked
  if (receiverMarkers[host]) {
    map.removeLayer(receiverMarkers[host].marker);
    if (receiverMarkers[host].line) map.removeLayer(receiverMarkers[host].line);
  }

  const color = heard ? '#22c55e' : '#555';
  const icon = L.circleMarker([r.receiver.lat, r.receiver.lon], {
    radius: 7,
    color: heard ? '#22c55e' : '#888',
    fillColor: color,
    fillOpacity: 0.8,
    weight: 1.5,
  });

  const popup = `
    <b>${r.receiver.name}</b><br>
    Distance: ${Math.round(r.receiver.distance_km)} km<br>
    RSSI: ${r.rssi_dbm} dBm<br>
    Tone SNR: ${r.tone_snr_db} dB<br>
    <b style="color:${heard ? '#22c55e' : '#888'}">${heard ? '✓ HEARD' : '✗ Not heard'}</b>
  `;
  icon.addTo(map).bindPopup(popup);

  let line = null;
  if (heard && txMarker) {
    const txLatLon = txMarker.getLatLng();
    line = L.polyline([[txLatLon.lat, txLatLon.lng], [r.receiver.lat, r.receiver.lon]], {
      color: '#22c55e', weight: 1.5, opacity: 0.5,
    }).addTo(map);
  }

  receiverMarkers[host] = { marker: icon, line };
}


function setActiveReceiver(host) {
  clearActiveHighlight();
  const entry = receiverMarkers[host];
  if (!entry || !txMarker) return;
  const pos   = entry.marker.getLatLng();
  const txPos = txMarker.getLatLng();

  const icon = L.divIcon({
    className: '',
    html: `<div style="width:16px;height:16px;background:#facc15;border:2px solid #fff;
      border-radius:50%;animation:kiwi-pulse 0.8s ease-in-out infinite alternate"></div>`,
    iconAnchor: [8, 8],
  });
  const marker = L.marker([pos.lat, pos.lng], { icon, zIndexOffset: 1000 }).addTo(map);
  const line = L.polyline([[txPos.lat, txPos.lng], [pos.lat, pos.lng]], {
    color: '#facc15', weight: 1.5, opacity: 0.7, dashArray: '7 6',
  }).addTo(map);
  activeHighlight = { marker, line };
}

function clearActiveHighlight() {
  if (activeHighlight) {
    map.removeLayer(activeHighlight.marker);
    map.removeLayer(activeHighlight.line);
    activeHighlight = null;
  }
}


// ── Port / audio device loading ───────────────────────────────────────────

async function loadPorts() {
  const sel = document.getElementById('port-select');
  sel.innerHTML = '<option value="">Loading…</option>';
  try {
    const { ports } = await fetch('/api/ports').then(r => r.json());
    if (!ports.length) {
      sel.innerHTML = '<option value="">No ports found</option>';
      return;
    }
    sel.innerHTML = ports.map(p =>
      `<option value="${p.port}">${p.port} — ${p.description}</option>`
    ).join('');
  } catch {
    sel.innerHTML = '<option value="">Error loading ports</option>';
  }
}

async function loadAudioDevices() {
  const sel = document.getElementById('audio-select');
  try {
    const { devices } = await fetch('/api/audio-devices').then(r => r.json());
    sel.innerHTML = '<option value="">System default</option>' +
      devices.map(d => `<option value="${d.name}">${d.name}</option>`).join('');
  } catch {
    // Non-critical, keep default option
  }
}

async function loadBands() {
  try {
    const { bands } = await fetch('/api/bands').then(r => r.json());
    const container = document.getElementById('band-checks');
    container.innerHTML = bands.map(b => `
      <label>
        <input type="checkbox" class="band-check" value="${b.name}" checked>
        ${b.name} (${(b.frequency_hz / 1e6).toFixed(3)} MHz)
      </label>
    `).join('');
  } catch {
    document.getElementById('band-checks').textContent = 'Error loading bands';
  }
}

async function loadConfig() {
  try {
    const cfg = await fetch('/api/config').then(r => r.json());
    // Update callsign in header
    document.querySelector('.callsign').textContent = cfg.callsign;
    document.title = `HF Range Tracker — ${cfg.callsign}`;
    // Place TX marker (we don't expose lat/lon via API, use Amsterdam default)
    // The station lat/lon isn't in /api/config for privacy; hardcode Amsterdam
    // (user can change in config.yaml)
    placeOrMoveTX(52.3676, 4.9041, cfg.callsign);
  } catch { /* config not critical for map */ }
}


// ── Scan control ──────────────────────────────────────────────────────────

let eventSource = null;

async function startScan() {
  const port = document.getElementById('port-select').value;
  if (!port) { alert('Please select a serial port first.'); return; }

  const bands = [...document.querySelectorAll('.band-check:checked')].map(c => c.value);
  if (!bands.length) { alert('Select at least one band.'); return; }

  const toneHz = parseFloat(document.getElementById('tone-hz').value) || 1000;
  const noRadio = document.getElementById('no-radio').checked;
  const audioDevice = document.getElementById('audio-select').value;
  const minKmVal = document.getElementById('min-km').value;
  const maxKmVal = document.getElementById('max-km').value;
  const minKm = minKmVal !== '' ? parseFloat(minKmVal) : null;
  const maxKm = maxKmVal !== '' ? parseFloat(maxKmVal) : null;

  setStatus('running');
  clearLog();
  clearResults();
  clearMarkers();

  const resp = await fetch('/api/scan/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ port, bands, tone_hz: toneHz, no_radio: noRadio, audio_device: audioDevice, min_km: minKm, max_km: maxKm }),
  });

  if (!resp.ok) {
    const err = await resp.json();
    addLog(`Error: ${err.detail}`, 'error');
    setStatus('idle');
    return;
  }

  openEventStream();
}

async function stopScan() {
  await fetch('/api/scan/stop', { method: 'POST' });
  addLog('Stop requested…', 'info');
}

function openEventStream() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/api/scan/stream');

  eventSource.onmessage = (e) => {
    const payload = JSON.parse(e.data);
    handleEvent(payload.type, payload.data);
  };

  eventSource.onerror = () => {
    addLog('Stream disconnected', 'error');
    setStatus('idle');
  };
}

function handleEvent(type, data) {
  switch (type) {
    case 'radio':
      if (data.status === 'connected')     addLog('Radio: connected via rigctld', 'info');
      else if (data.status === 'tuned')    addLog(`Tuned → ${(data.freq_hz/1e6).toFixed(4)} MHz ${data.mode}`, 'info');
      else if (data.status === 'disconnected') addLog(`Radio: ${data.error}`, 'error');
      break;

    case 'band_start':
      addLog(`▶ Band ${data.band} — ${(data.frequency_hz/1e6).toFixed(4)} MHz, tone ${data.tone_hz} Hz`, 'band');
      addLog(`  Skip zone: ${Math.round(data.skip_zone.min_km)}–${Math.round(data.skip_zone.max_km)} km`, 'info');
      break;

    case 'receivers_found':
      addLog(`  ${data.count} KiwiSDR receivers in range`, 'info');
      // Pre-place grey markers
      data.receivers.forEach(r => {
        const fakeResult = { receiver: { ...r }, rssi_dbm: 0, tone_snr_db: 0 };
        addReceiverMarker(fakeResult, false);
      });
      break;

    case 'receiver_start':
      addLog(`  [${data.index + 1}/${data.total}] ${data.name} (${Math.round(data.distance_km)} km)…`);
      setActiveReceiver(data.host);
      break;

    case 'receiver_result':
      const heard = data.heard;
      clearActiveHighlight();
      addLog(
        `    ${heard ? '✓ HEARD' : '✗'} RSSI ${data.rssi_dbm} dBm  SNR ${data.tone_snr_db} dB  noise ${data.noise_floor_db} dB`,
        heard ? 'heard' : '',
      );
      addReceiverMarker(data, heard);
      addResultRow(data);
      break;

    case 'receiver_error':
      clearActiveHighlight();
      addLog(`    ✗ timeout/error`, 'error');
      break;

    case 'band_complete':
      addLog(`◼ Band ${data.band} complete`, 'band');
      break;

    case 'scan_complete':
      clearActiveHighlight();
      addLog(`✓ Scan done — heard on ${data.heard}/${data.total} receivers`, 'heard');
      setStatus('done', `${data.heard}/${data.total}`);
      if (eventSource) eventSource.close();
      break;

    case 'status':
      addLog(`  ${data.message}`, 'info');
      break;
  }
}


// ── UI helpers ────────────────────────────────────────────────────────────

function setStatus(state, extra) {
  const badge = document.getElementById('status-badge');
  const btnStart = document.getElementById('btn-start');
  const btnStop  = document.getElementById('btn-stop');

  const map = {
    idle:    ['Idle',          'badge-idle'],
    running: ['Scanning…',     'badge-running'],
    done:    [`Done ${extra || ''}`, 'badge-done'],
    error:   ['Error',         'badge-error'],
  };
  const [label, cls] = map[state] || ['Idle', 'badge-idle'];
  badge.textContent = label;
  badge.className = `badge ${cls}`;

  btnStart.disabled = (state === 'running');
  btnStop.disabled  = (state !== 'running');
}

function addLog(text, cls) {
  const el = document.createElement('div');
  el.className = 'log-entry' + (cls ? ` log-${cls}` : '');
  el.textContent = text;
  const out = document.getElementById('log-output');
  out.appendChild(el);
  out.scrollTop = out.scrollHeight;
}

function clearLog() {
  document.getElementById('log-output').innerHTML = '';
}

function addResultRow(r) {
  const heard = r.heard;
  const rssiClass = r.rssi_dbm > -90 ? 'rssi-good' : r.rssi_dbm > -110 ? 'rssi-mid' : 'rssi-bad';
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><a href="http://${r.receiver.host}:${r.receiver.port}/" target="_blank" style="color:inherit">${r.receiver.name}</a></td>
    <td>${Math.round(r.receiver.distance_km)} km</td>
    <td class="${rssiClass}">${r.rssi_dbm}</td>
    <td>${r.tone_snr_db}</td>
    <td class="${heard ? 'heard-yes' : 'heard-no'}">${heard ? '✓ YES' : 'no'}</td>
  `;
  document.getElementById('results-body').prepend(tr);

  // Update summary
  const rows = document.querySelectorAll('#results-body tr');
  const heardCount = [...rows].filter(r => r.querySelector('.heard-yes')).length;
  document.getElementById('results-summary').textContent = `(${heardCount}/${rows.length} heard)`;
}

function clearResults() {
  document.getElementById('results-body').innerHTML = '';
  document.getElementById('results-summary').textContent = '';
}

function clearMarkers() {
  Object.values(receiverMarkers).forEach(({ marker, line }) => {
    if (marker) map.removeLayer(marker);
    if (line) map.removeLayer(line);
  });
  receiverMarkers = {};
}


// ── Init ──────────────────────────────────────────────────────────────────

(async function init() {
  await Promise.all([loadPorts(), loadBands(), loadAudioDevices(), loadConfig()]);
})();
