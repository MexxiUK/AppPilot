const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
const connectionEl = document.getElementById('connection');
const sessionIdEl = document.getElementById('session-id');
const stepCountEl = document.getElementById('step-count');
const liveImage = document.getElementById('live-image');
const stage = document.getElementById('stage');
const cursor = document.getElementById('cursor');
const clickRing = document.getElementById('click-ring');
const highlight = document.getElementById('target-highlight');
const narration = document.getElementById('narration');
const humanInputOverlay = document.getElementById('human-input-overlay');
const humanInputText = document.getElementById('human-input-text');
const taskEl = document.getElementById('task');
const actionLog = document.getElementById('action-log');
const filmstrip = document.getElementById('filmstrip');

const STAGE_W = 1280;
const STAGE_H = 720;

let latestStep = null;
let ws;
let reconnectTimer = null;

function fitStage() {
  const viewport = stage.parentElement;
  const rect = viewport.getBoundingClientRect();
  const pad = 48;
  const scale = Math.min(
    (rect.width - pad) / STAGE_W,
    (rect.height - pad) / STAGE_H
  );
  stage.style.transform = `scale(${Math.max(scale, 0.25)})`;
}

window.addEventListener('resize', fitStage);
window.addEventListener('load', fitStage);
fitStage();

function connect() {
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    connectionEl.textContent = 'Connected';
    connectionEl.className = 'connected';
  };

  ws.onclose = () => {
    connectionEl.textContent = 'Disconnected';
    connectionEl.className = 'disconnected';
    showNarration('Session ended. Last screenshot retained.');
    if (!reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, 2000);
    }
  };

  ws.onerror = () => {
    connectionEl.textContent = 'Connection error';
    connectionEl.className = 'error';
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'step') {
      handleStep(msg);
    } else if (msg.type === 'session') {
      handleSession(msg);
    } else if (msg.type === 'reset') {
      resetDashboard();
    }
  };
}

connect();

function handleSession(session) {
  sessionIdEl.textContent = session.session_id || '—';
  taskEl.textContent = session.task || 'No task';
  stepCountEl.textContent = `${session.step_count} steps`;
}

function resetDashboard() {
  latestStep = null;
  sessionIdEl.textContent = '—';
  taskEl.textContent = 'No task';
  stepCountEl.textContent = '0 steps';
  liveImage.src = '';
  filmstrip.innerHTML = '';
  actionLog.innerHTML = '';
  hideHighlight();
  hideHumanInputPrompt();
  showNarration('New test started. Waiting for steps...');
}

function handleStep(step) {
  latestStep = step;
  stepCountEl.textContent = `${step.number} steps`;

  const imageB64 = step.after_screenshot_b64 || step.before_screenshot_b64;
  if (imageB64) {
    liveImage.src = `data:image/jpeg;base64,${imageB64}`;
    addFilmstripThumb(step.number, imageB64);
  }

  addLogEntry(step);

  if (step.action.type === 'human_input') {
    showHumanInputPrompt(step.action.prompt, step.action.reason);
    return;
  }
  hideHumanInputPrompt();

  if (step.action.type === 'click') {
    animateCursor(step.cursor_start, step.cursor_end, () => {
      cursor.classList.add('clicking');
      popClickRing(step.cursor_end);
      setTimeout(() => cursor.classList.remove('clicking'), 160);
    });
  } else {
    animateCursor(step.cursor_start, step.cursor_end);
  }

  if (step.target_box) {
    showHighlight(step.target_box);
  } else {
    hideHighlight();
  }

  if (step.action.reason) {
    showNarration(`${capitalize(step.action.type)}: ${step.action.reason}`);
  }
}

function animateCursor(start, end, onArrived) {
  if (!start || !end) return;

  cursor.style.transition = 'none';
  cursor.style.transform = `translate(${start.x}px, ${start.y}px)`;

  const distance = Math.hypot(end.x - start.x, end.y - start.y);
  const duration = Math.min(Math.max(distance / 2.5, 180), 900);

  requestAnimationFrame(() => {
    cursor.style.transition = `transform ${duration}ms cubic-bezier(0.25, 0.46, 0.45, 0.94)`;
    cursor.style.transform = `translate(${end.x}px, ${end.y}px)`;
    if (onArrived) {
      setTimeout(onArrived, duration);
    }
  });
}

function popClickRing(point) {
  if (!point) return;
  clickRing.style.transform = 'translate(0, 0) scale(0.5)';
  clickRing.style.left = `${point.x}px`;
  clickRing.style.top = `${point.y}px`;
  clickRing.classList.remove('popping');
  void clickRing.offsetWidth;
  clickRing.classList.add('popping');
}

function showHighlight(box) {
  highlight.style.left = `${box.x}px`;
  highlight.style.top = `${box.y}px`;
  highlight.style.width = `${box.width}px`;
  highlight.style.height = `${box.height}px`;
  highlight.classList.add('visible');
  setTimeout(hideHighlight, 1400);
}

function hideHighlight() {
  highlight.classList.remove('visible');
}

function showNarration(text) {
  narration.textContent = text;
  narration.classList.add('visible');
  setTimeout(() => narration.classList.remove('visible'), 3200);
}

function showHumanInputPrompt(prompt, reason) {
  humanInputText.textContent = prompt || reason || 'Human input required in terminal.';
  humanInputOverlay.classList.add('visible');
}

function hideHumanInputPrompt() {
  humanInputOverlay.classList.remove('visible');
}

function addLogEntry(step) {
  const entry = document.createElement('div');
  entry.className = `action-entry${step.error ? ' error' : ''}${step.action.type === 'human_input' ? ' human' : ''}`;
  entry.innerHTML = `
    <div class="header">
      <span class="number">#${step.number}</span>
      <span class="type">${step.action.type}</span>
    </div>
    ${step.error ? `<div class="reason">Error: ${escapeHtml(step.error)}</div>` : ''}
    ${step.action.reason ? `<div class="reason">${escapeHtml(step.action.reason)}</div>` : ''}
  `;
  actionLog.prepend(entry);
}

function addFilmstripThumb(number, b64) {
  const img = document.createElement('img');
  img.src = `data:image/jpeg;base64,${b64}`;
  img.title = `Step ${number}`;
  img.addEventListener('click', () => {
    liveImage.src = img.src;
  });
  filmstrip.appendChild(img);
  filmstrip.scrollLeft = filmstrip.scrollWidth;
}

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
