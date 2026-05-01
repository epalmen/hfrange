/* HF Range Tracker — frontend JS */

// ── Scan state (shared between map + waterfall) ───────────────────────────
let currentScanFreqKhz = 14200;
let currentScanMode    = 'usb';

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
    <b style="color:${heard ? '#22c55e' : '#888'}">${heard ? '✓ HEARD' : '✗ Not heard'}</b><br>
    <button onclick="connectWaterfall('${r.receiver.host}',${r.receiver.port || 8073},${currentScanFreqKhz},'${currentScanMode}','${r.receiver.name.replace(/'/g, '')}')"
      style="margin-top:6px;padding:2px 10px;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px">
      ◉ Waterfall
    </button>
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
  const txDuration = parseFloat(document.getElementById('tx-duration').value) || 30;
  const driveLevel = parseFloat(document.getElementById('drive-level').value) || 0.7;

  setStatus('running');
  clearLog();
  clearResults();
  clearMarkers();

  const resp = await fetch('/api/scan/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ port, bands, tone_hz: toneHz, no_radio: noRadio, audio_device: audioDevice, min_km: minKm, max_km: maxKm, tx_duration_s: txDuration, drive_level: driveLevel }),
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
      currentScanFreqKhz = data.frequency_hz / 1000;
      currentScanMode    = data.mode.toLowerCase();
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
      if (document.getElementById('rx-listen').checked) {
        connectWaterfall(data.host, data.port || 8073, currentScanFreqKhz, currentScanMode, data.name);
      }
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
  const safeName = (r.receiver.name || '').replace(/'/g, '');
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>
      <a href="http://${r.receiver.host}:${r.receiver.port}/" target="_blank" style="color:inherit">${r.receiver.name}</a>
      <button class="btn-watch" onclick="connectWaterfall('${r.receiver.host}',${r.receiver.port || 8073},${currentScanFreqKhz},'${currentScanMode}','${safeName}')" title="Open waterfall">◉</button>
    </td>
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


// ── No-radio toggle ───────────────────────────────────────────────────────

function onNoRadioChange() {
  const checked = document.getElementById('no-radio').checked;
  document.getElementById('rx-listen-label').style.display = checked ? '' : 'none';
  if (!checked) document.getElementById('rx-listen').checked = false;
}


// ── Waterfall + Audio ─────────────────────────────────────────────────────

const FFT_SIZE   = 1024;
const KIWI_SR    = 12000;
const WF_MAX_HZ  = 3200;   // display USB audio band (0–3.2 kHz)
const FLOOR_DB   = -120;
const CEIL_DB    = -20;

// Hann window coefficients
const _hann = new Float32Array(FFT_SIZE);
for (let i = 0; i < FFT_SIZE; i++) {
  _hann[i] = 0.5 * (1 - Math.cos(2 * Math.PI * i / (FFT_SIZE - 1)));
}

let wfWs        = null;
let audioCtx    = null;
let gainNode    = null;
let nextPlayAt  = 0;
let _pcmBuf     = new Int16Array(0);

function connectWaterfall(host, port, freqKhz, mode, rxName) {
  disconnectWaterfall();

  const section = document.getElementById('waterfall-section');
  section.classList.remove('wf-hidden');

  document.getElementById('wf-rx-name').textContent  = rxName || host;
  document.getElementById('wf-rx-freq').textContent  =
    `${Number(freqKhz).toFixed(3)} kHz  ${(mode || 'usb').toUpperCase()}`;

  const canvas = document.getElementById('waterfall-canvas');
  canvas.width  = canvas.clientWidth || 800;
  canvas.height = 160;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  _pcmBuf = new Int16Array(0);

  const params = new URLSearchParams({
    host, port, freq_khz: Number(freqKhz).toFixed(3), mode: (mode || 'usb').toLowerCase(),
  });
  wfWs = new WebSocket(`ws://${location.host}/ws/kiwi?${params}`);
  wfWs.binaryType = 'arraybuffer';

  wfWs.onmessage = (e) => {
    if (typeof e.data === 'string') return;             // keepalive JSON
    if (e.data.byteLength < 13) return;
    const tag = new Uint8Array(e.data, 0, 3);
    if (tag[0] !== 83 || tag[1] !== 78 || tag[2] !== 68) return; // "SND"

    const sampleCount = (e.data.byteLength - 12) / 2;
    const samples     = new Int16Array(e.data, 12, sampleCount);

    _playPCM(samples);
    _accumulateFFT(ctx, canvas, samples);
  };

  wfWs.onerror = () => {
    document.getElementById('wf-rx-name').textContent += ' (error)';
  };
  wfWs.onclose = () => {};
}

function _accumulateFFT(ctx, canvas, samples) {
  const merged = new Int16Array(_pcmBuf.length + samples.length);
  merged.set(_pcmBuf);
  merged.set(samples, _pcmBuf.length);
  _pcmBuf = merged;

  while (_pcmBuf.length >= FFT_SIZE) {
    const win = _pcmBuf.slice(0, FFT_SIZE);
    _pcmBuf   = _pcmBuf.slice(FFT_SIZE >> 1);   // 50% overlap
    _drawWFLine(ctx, canvas, _computeFFT(win));
  }
}

function _computeFFT(samples) {
  const real = new Float32Array(FFT_SIZE);
  const imag = new Float32Array(FFT_SIZE);
  for (let i = 0; i < FFT_SIZE; i++) real[i] = (samples[i] / 32768) * _hann[i];

  _fft(real, imag);

  const half = FFT_SIZE >> 1;
  const mag  = new Float32Array(half);
  for (let i = 0; i < half; i++) {
    const p = real[i] * real[i] + imag[i] * imag[i];
    mag[i]  = p > 0 ? 10 * Math.log10(p / (FFT_SIZE * FFT_SIZE)) : FLOOR_DB;
  }
  return mag;
}

function _fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = -2 * Math.PI / len;
    const wRe = Math.cos(ang), wIm = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let uRe = 1, uIm = 0;
      for (let k = 0; k < (len >> 1); k++) {
        const j   = i + k + (len >> 1);
        const tRe = uRe * re[j] - uIm * im[j];
        const tIm = uRe * im[j] + uIm * re[j];
        re[j]     = re[i + k] - tRe;
        im[j]     = im[i + k] - tIm;
        re[i + k] += tRe;
        im[i + k] += tIm;
        const nr = uRe * wRe - uIm * wIm;
        uIm = uRe * wIm + uIm * wRe;
        uRe = nr;
      }
    }
  }
}

function _drawWFLine(ctx, canvas, magDb) {
  const w       = canvas.width;
  const h       = canvas.height;
  const maxBin  = Math.floor(WF_MAX_HZ * FFT_SIZE / KIWI_SR);

  // Scroll existing content down by 1 px
  const existing = ctx.getImageData(0, 0, w, h - 1);
  ctx.putImageData(existing, 0, 1);

  // New row at top
  const row = ctx.createImageData(w, 1);
  for (let x = 0; x < w; x++) {
    const bin = Math.floor(x * maxBin / w);
    const db  = magDb[bin] !== undefined ? magDb[bin] : FLOOR_DB;
    const v   = Math.max(0, Math.min(255,
                  Math.round((db - FLOOR_DB) * 255 / (CEIL_DB - FLOOR_DB))));
    const [r, g, b] = _heat(v);
    const p = x << 2;
    row.data[p]     = r;
    row.data[p + 1] = g;
    row.data[p + 2] = b;
    row.data[p + 3] = 255;
  }
  ctx.putImageData(row, 0, 0);
}

function _heat(v) {
  if (v <  64) return [0,          0,          Math.min(255, v * 4)];
  if (v < 128) return [0,          (v - 64) * 4,  255];
  if (v < 192) return [0,          255,           255 - (v - 128) * 4];
  if (v < 224) return [(v - 192) * 8, 255,        0];
  return             [255,         Math.max(0, 255 - (v - 224) * 8), 0];
}

function _playPCM(samples) {
  if (!document.getElementById('wf-listen').checked) return;
  if (!audioCtx) {
    audioCtx   = new (window.AudioContext || window.webkitAudioContext)();
    gainNode   = audioCtx.createGain();
    gainNode.gain.value = parseFloat(document.getElementById('wf-volume').value);
    gainNode.connect(audioCtx.destination);
    nextPlayAt = audioCtx.currentTime;
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();

  const f32 = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) f32[i] = samples[i] / 32768;

  const buf = audioCtx.createBuffer(1, f32.length, KIWI_SR);
  buf.copyToChannel(f32, 0);
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(gainNode);
  const startAt = Math.max(audioCtx.currentTime + 0.05, nextPlayAt);
  src.start(startAt);
  nextPlayAt = startAt + buf.duration;
}

function disconnectWaterfall() {
  if (wfWs) { wfWs.close(); wfWs = null; }
  _pcmBuf = new Int16Array(0);
}

function closeWaterfall() {
  disconnectWaterfall();
  document.getElementById('waterfall-section').classList.add('wf-hidden');
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('wf-volume').addEventListener('input', (e) => {
    if (gainNode) gainNode.gain.value = parseFloat(e.target.value);
  });
});


// ── Init ──────────────────────────────────────────────────────────────────

(async function init() {
  await Promise.all([loadPorts(), loadBands(), loadAudioDevices(), loadConfig()]);
})();
