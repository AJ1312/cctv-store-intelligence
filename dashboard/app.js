/**
 * Purplle Store Intelligence — Dashboard JavaScript
 * Pure Vanilla JS: no framework, no dependencies.
 * Polls API every 3s, animates KPIs, renders heatmap + funnel + charts.
 */

'use strict';

// ── Config ────────────────────────────────────────────────────────────────────
const API_BASE  = 'http://localhost:8000';
const STORE_ID  = 'ST1008';
const DATE      = '2026-04-10';          // The actual data date
const POLL_MS   = 3000;

// Heatmap color stops (Cold → Hot)
const HEAT_STOPS = [
  [0,   [30, 41, 59]],    // dark slate
  [25,  [29, 78, 216]],   // blue
  [50,  [124, 58, 237]],  // purple
  [75,  [192, 38, 211]],  // magenta
  [100, [249, 115, 22]],  // orange
];

// ── State ─────────────────────────────────────────────────────────────────────
let _lastMetrics   = null;
let _lastFunnel    = null;
let _hourlyData    = [];
let _hourlyCtx     = null;
let _apiOk         = false;

// ── Utilities ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const fmt = {
  pct:   v => `${(v * 100).toFixed(1)}%`,
  sec:   ms => ms >= 60000 ? `${(ms/60000).toFixed(1)}m` : `${(ms/1000).toFixed(0)}s`,
  num:   v => v == null ? '—' : Number(v).toLocaleString(),
  short: ms => ms >= 60000 ? `${(ms/60000).toFixed(0)}m ${((ms%60000)/1000).toFixed(0)}s` : `${(ms/1000).toFixed(0)}s`,
};

function heatColor(score) {
  // Interpolate between HEAT_STOPS based on score 0–100
  score = Math.max(0, Math.min(100, score));
  let lo = HEAT_STOPS[0], hi = HEAT_STOPS[HEAT_STOPS.length - 1];
  for (let i = 0; i < HEAT_STOPS.length - 1; i++) {
    if (score <= HEAT_STOPS[i+1][0]) { lo = HEAT_STOPS[i]; hi = HEAT_STOPS[i+1]; break; }
  }
  const t = (score - lo[0]) / (hi[0] - lo[0] + 0.001);
  const r = lo[1][0] + (hi[1][0] - lo[1][0]) * t;
  const g = lo[1][1] + (hi[1][1] - lo[1][1]) * t;
  const b = lo[1][2] + (hi[1][2] - lo[1][2]) * t;
  return `rgb(${Math.round(r)},${Math.round(g)},${Math.round(b)})`;
}

// Animated counter tick-up
function animateValue(el, targetStr) {
  const prev = el.textContent;
  if (prev === targetStr) return;
  el.textContent = targetStr;
  el.classList.remove('kpi-updated');
  void el.offsetWidth;  // force reflow
  el.classList.add('kpi-updated');
}

// ── Clock & Date ──────────────────────────────────────────────────────────────
function startClock() {
  const update = () => {
    const now = new Date();
    $('clock').textContent = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    $('dateDisplay').textContent = now.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  };
  update();
  setInterval(update, 1000);
}

// ── API Status ────────────────────────────────────────────────────────────────
function setApiStatus(ok, msg) {
  _apiOk = ok;
  const dot  = document.querySelector('#apiStatus .status-dot');
  const text = $('apiStatusText');
  dot.className  = 'status-dot ' + (ok ? 'status-ok' : 'status-error');
  text.textContent = ok ? 'API Connected' : ('API: ' + (msg || 'Offline'));
}

// ── Fetch helpers ─────────────────────────────────────────────────────────────
async function apiFetch(path) {
  const res = await fetch(`${API_BASE}${path}`, { signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── KPI Rendering ─────────────────────────────────────────────────────────────
function renderMetrics(m) {
  animateValue($('kpiVisitorsVal'),    fmt.num(m.unique_visitors));
  animateValue($('kpiConversionVal'),  fmt.pct(m.conversion_rate));
  animateValue($('kpiQueueVal'),       fmt.num(m.queue_depth_now));
  animateValue($('kpiAbandonmentVal'), fmt.pct(m.abandonment_rate));
  animateValue($('kpiGMVVal'),         fmt.num(m.converted_visitors));

  // Queue card color — warn if queue > 4
  const qCard = $('kpiQueue');
  if (m.queue_depth_now > 7)      qCard.style.borderColor = 'rgba(239,68,68,0.5)';
  else if (m.queue_depth_now > 4) qCard.style.borderColor = 'rgba(245,158,11,0.4)';
  else                             qCard.style.borderColor = '';

  // Hourly traffic sparkline
  if (m.hourly_traffic && m.hourly_traffic.length > 0) {
    _hourlyData = m.hourly_traffic;
    renderHourlyChart(_hourlyData);
  }
}

// ── Funnel Rendering ──────────────────────────────────────────────────────────
function renderFunnel(f) {
  const container = $('funnelContainer');
  const stages = f.funnel || [];
  const overall = f.overall_conversion || 0;
  const maxVisitors = stages[0]?.visitors || 1;

  $('overallConversion').textContent = fmt.pct(overall);

  let html = '';
  stages.forEach((s, i) => {
    const pct = Math.round((s.visitors / maxVisitors) * 100);
    html += `
      <div class="funnel-stage">
        <div class="funnel-label-row">
          <span class="funnel-stage-name">${s.stage}</span>
          <span class="funnel-stage-count">${fmt.num(s.visitors)}</span>
        </div>
        <div class="funnel-bar-track">
          <div class="funnel-bar-fill stage-${i}" data-pct="${pct}" style="width:0%"></div>
        </div>
        ${s.drop_off_pct > 0 ? `<div class="funnel-dropoff">▼ ${fmt.pct(s.drop_off_pct)} drop-off</div>` : ''}
      </div>
    `;
  });

  html += `
    <div class="funnel-overall">
      <div class="funnel-overall-label">Overall Conversion</div>
      <div class="funnel-overall-value">${fmt.pct(overall)}</div>
    </div>
  `;

  container.innerHTML = html;

  // Animate bars after render
  requestAnimationFrame(() => {
    container.querySelectorAll('.funnel-bar-fill').forEach(bar => {
      const pct = bar.dataset.pct;
      setTimeout(() => { bar.style.width = pct + '%'; }, 80);
    });
  });
}

// ── Heatmap Rendering ─────────────────────────────────────────────────────────
const ZONE_DATA_MAP = {};  // zone_id → {visit_count, avg_dwell_ms, visit_score}

function renderHeatmap(data) {
  const zones = data.zones || [];
  zones.forEach(z => { ZONE_DATA_MAP[z.zone_id] = z; });

  // Update confidence badge
  const conf = data.data_confidence || 'LOW';
  const badge = $('heatmapConfidence');
  badge.textContent = `${conf} CONFIDENCE · ${data.total_sessions || 0} sessions`;
  badge.style.background = conf === 'HIGH' ? 'rgba(16,185,129,0.15)' : 'rgba(245,158,11,0.15)';
  badge.style.color       = conf === 'HIGH' ? '#34d399' : '#fbbf24';
  badge.style.borderColor = conf === 'HIGH' ? 'rgba(16,185,129,0.3)' : 'rgba(245,158,11,0.3)';

  // Paint each zone rect
  document.querySelectorAll('.map-zone').forEach(g => {
    const zoneId = g.dataset.zone;
    const rect   = g.querySelector('rect');
    if (!rect) return;

    const zd = ZONE_DATA_MAP[zoneId];
    if (zd && zd.visit_score > 0) {
      rect.style.fill   = heatColor(zd.visit_score);
      rect.style.filter = `brightness(1) drop-shadow(0 0 ${Math.round(zd.visit_score/10)}px ${heatColor(zd.visit_score)})`;
    } else {
      rect.style.fill   = '';
      rect.style.filter = '';
    }
  });
}

// ── SVG Zone Tooltip ──────────────────────────────────────────────────────────
function initMapTooltips() {
  const tooltip = $('zoneTooltip');

  document.querySelectorAll('.map-zone').forEach(g => {
    g.addEventListener('mousemove', e => {
      const zoneId = g.dataset.zone;
      const label  = g.dataset.label || zoneId;
      const zd     = ZONE_DATA_MAP[zoneId];

      $('ztName').textContent = label;
      if (zd) {
        $('ztStats').innerHTML =
          `${fmt.num(zd.visit_count)} visits<br>Avg dwell: ${fmt.short(zd.avg_dwell_ms)}<br>Score: ${zd.visit_score}/100`;
      } else {
        $('ztStats').textContent = 'No data yet';
      }

      tooltip.style.display = 'block';
      tooltip.style.left = (e.clientX + 14) + 'px';
      tooltip.style.top  = (e.clientY - 10) + 'px';
    });

    g.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
    });
  });
}

// ── Dwell Table ───────────────────────────────────────────────────────────────
function renderDwellTable(avgDwell) {
  const tbody = $('dwellTableBody');
  const entries = Object.entries(avgDwell)
    .sort((a, b) => b[1].avg_dwell_ms - a[1].avg_dwell_ms)
    .slice(0, 12);

  if (entries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="loading-cell">No dwell data yet</td></tr>';
    return;
  }

  const maxDwell = entries[0][1].avg_dwell_ms || 1;
  const ZONE_LABELS = {
    ZONE_MAYBELLINE:  'Maybelline', ZONE_FACES: 'Faces Canada',
    ZONE_DERMDOC:     'DermDoc',    ZONE_MINIMALIST: 'Minimalist',
    ZONE_GV:          'Good Vibes', ZONE_SWISS_RENEE: 'Swiss+Renee',
    ZONE_ALPS:        'Alps Goodness', ZONE_LAKME: 'Lakme Makeup',
    ZONE_MAKEUP_UNIT: 'Makeup Unit', ZONE_CASH_COUNTER: 'Cash Counter',
    ZONE_ACCESSORIES: 'Accessories', ZONE_AQUALOGICA: 'Aqualogica',
    ZONE_FRAGRANCE:   'Fragrance',  ZONE_NAIL: 'Nail Unit',
    ZONE_PMU:         'PMU',        ZONE_LAKME_SKIN: 'Lakme Skin',
    ZONE_COLORBAR:    'Colorbar+Sugar', ZONE_STREAX: 'Streax',
    ZONE_TFS:         'The Face Shop', ZONE_EB_KOREAN: 'EB Korean',
  };

  tbody.innerHTML = entries.map(([zid, stats]) => {
    const heatPct  = Math.round((stats.avg_dwell_ms / maxDwell) * 100);
    const heatRgb  = heatColor(heatPct);
    const label    = ZONE_LABELS[zid] || zid;
    return `
      <tr>
        <td>${label}</td>
        <td>${fmt.short(stats.avg_dwell_ms)}</td>
        <td>${fmt.num(stats.visit_count)}</td>
        <td style="position:relative;padding-left:8px;">
          <div class="dwell-heat-bar" style="width:${heatPct}%;background:${heatRgb};"></div>
          ${heatPct}%
        </td>
      </tr>
    `;
  }).join('');
}

// ── Hourly Chart (vanilla Canvas) ─────────────────────────────────────────────
function renderHourlyChart(hourly) {
  const canvas = $('hourlyChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  // Ensure all hours 12–21 are present
  const allHours = Array.from({ length: 10 }, (_, i) => i + 12);
  const dataMap  = {};
  hourly.forEach(h => { dataMap[h.hour] = h.visitors; });

  const labels = allHours.map(h => `${h}:00`);
  const values = allHours.map(h => dataMap[h] || 0);
  const maxVal = Math.max(...values, 1);

  const W = canvas.offsetWidth  || 340;
  const H = canvas.offsetHeight || 180;
  canvas.width  = W * window.devicePixelRatio;
  canvas.height = H * window.devicePixelRatio;
  ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

  ctx.clearRect(0, 0, W, H);

  const PAD = { top: 20, right: 16, bottom: 40, left: 28 };
  const chartW = W - PAD.left - PAD.right;
  const chartH = H - PAD.top  - PAD.bottom;
  const barW   = Math.max(4, (chartW / values.length) - 8);

  // Gradient for bars
  const grad = ctx.createLinearGradient(0, PAD.top, 0, PAD.top + chartH);
  grad.addColorStop(0, 'rgba(192,38,211,0.9)');
  grad.addColorStop(1, 'rgba(124,58,237,0.3)');

  // Grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = PAD.top + (chartH * i / 4);
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(PAD.left + chartW, y); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,0.25)';
    ctx.font = '10px Inter, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(Math.round(maxVal * (4 - i) / 4), PAD.left - 4, y + 3);
  }

  // Bars
  values.forEach((v, i) => {
    const x   = PAD.left + i * (chartW / values.length) + (chartW / values.length - barW) / 2;
    const barH = (v / maxVal) * chartH;
    const y   = PAD.top + chartH - barH;

    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.roundRect(x, y, barW, barH, [3, 3, 0, 0]);
    ctx.fill();

    // Peak highlight
    if (v === maxVal && maxVal > 0) {
      ctx.fillStyle = 'rgba(249,115,22,0.5)';
      ctx.beginPath();
      ctx.roundRect(x, y, barW, barH, [3, 3, 0, 0]);
      ctx.fill();
    }

    // Label
    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.font = '9px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(labels[i], x + barW / 2, H - PAD.bottom + 14);

    // Value on bar
    if (v > 0) {
      ctx.fillStyle = 'rgba(255,255,255,0.7)';
      ctx.font = '9px Inter, sans-serif';
      ctx.fillText(v, x + barW / 2, y - 4);
    }
  });
}

// ── Anomaly Feed ──────────────────────────────────────────────────────────────
function renderAnomalies(data) {
  const feed    = $('anomalyFeed');
  const counter = $('anomalyCount');
  const anomalies = data.anomalies || [];

  counter.textContent = anomalies.length;
  counter.style.background = anomalies.some(a => a.severity === 'CRITICAL')
    ? 'rgba(239,68,68,0.25)' : 'rgba(245,158,11,0.15)';

  if (anomalies.length === 0) {
    feed.innerHTML = `
      <div class="anomaly-empty">
        <span class="anomaly-ok">✅ No active anomalies</span>
      </div>
    `;
    return;
  }

  feed.innerHTML = anomalies.map(a => `
    <div class="anomaly-item severity-${a.severity}">
      <span class="anomaly-badge badge-${a.severity}">${a.severity}</span>
      <div class="anomaly-body">
        <div class="anomaly-type">${a.type.replace(/_/g, ' ')}</div>
        <div class="anomaly-action">${a.suggested_action}</div>
      </div>
    </div>
  `).join('');
}

// ── Camera Status ─────────────────────────────────────────────────────────────
function renderCameraStatus(feeds) {
  const ST = feeds[STORE_ID] || {};
  const CAM_NAMES = {
    CAM_ENTRY_01:      'cam-CAM_ENTRY_01',
    CAM_FOH_01:        'cam-CAM_FOH_01',
    CAM_NORTH_WALL_01: 'cam-CAM_NORTH_WALL_01',
    CAM_SOUTH_WALL_01: 'cam-CAM_SOUTH_WALL_01',
    CAM_BILLING_01:    'cam-CAM_BILLING_01',
  };

  Object.entries(CAM_NAMES).forEach(([camId, elId]) => {
    const el  = $(elId);
    const cam = ST[camId];
    if (!el) return;
    el.classList.remove('status-ok', 'status-stale');
    if (cam) {
      el.classList.add(cam.status === 'OK' ? 'status-ok' : 'status-stale');
      el.title = `Last event: ${cam.last_event} · Lag: ${cam.lag_minutes}m`;
    }
  });
}

// ── Main Poll Loop ────────────────────────────────────────────────────────────
async function poll() {
  try {
    const [metrics, funnel, heatmap, anomalies, health] = await Promise.all([
      apiFetch(`/stores/${STORE_ID}/metrics?date=${DATE}`),
      apiFetch(`/stores/${STORE_ID}/funnel?date=${DATE}`),
      apiFetch(`/stores/${STORE_ID}/heatmap?date=${DATE}`),
      apiFetch(`/stores/${STORE_ID}/anomalies`),
      apiFetch('/health'),
    ]);

    setApiStatus(true);

    renderMetrics(metrics);
    renderFunnel(funnel);
    renderHeatmap(heatmap);
    renderDwellTable(metrics.avg_dwell_per_zone || {});
    renderAnomalies(anomalies);
    renderCameraStatus(health.event_feeds || {});

    $('lastRefresh').textContent = 'Updated ' + new Date().toLocaleTimeString('en-IN', { hour12: false });

  } catch (err) {
    setApiStatus(false, err.message);
    console.warn('Poll error:', err.message);
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
function init() {
  startClock();
  initMapTooltips();

  // Initial poll immediately, then every POLL_MS
  poll();
  setInterval(poll, POLL_MS);

  // Resize hourly chart on window resize
  window.addEventListener('resize', () => {
    if (_hourlyData.length > 0) renderHourlyChart(_hourlyData);
  });
}

document.addEventListener('DOMContentLoaded', init);
