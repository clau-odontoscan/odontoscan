// ════════════════════════════════════════
// UTIL
// ════════════════════════════════════════
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const lsGet = (k, d) => { try { return localStorage.getItem(k) ?? d } catch(e) { return d } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch(e) {} };
async function api(path, opts = {}) {
  const r = await fetch(API + path, opts);
  let d = null; try { d = await r.json() } catch(e) {}
  if (!r.ok) throw new Error((d && d.error) || ('HTTP ' + r.status));
  return d;
}
const fmtDate = ts => new Date(ts * 1000).toLocaleString('pt-BR', {day:'2-digit', month:'2-digit', year:'2-digit', hour:'2-digit', minute:'2-digit'});

// ════════════════════════════════════════
// THREE.JS
// ════════════════════════════════════════
let scene, cam3d, rend3d, modelGroup = null, cloudObj = null, meshObj = null;
let cDist = 3, cTheta = .3, cPhi = 1.1;
let drag = false, pm = {x:0, y:0}, prevT = null, pinch0 = null;

function initThree(){
  const canvas = $('c3d'), cont = $('centerView');
  scene = new THREE.Scene();
  const W = cont.clientWidth || 800, H = cont.clientHeight || 600;
  cam3d = new THREE.PerspectiveCamera(45, W/H, .01, 200);
  updateCam3d();
  rend3d = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true, preserveDrawingBuffer:true});
  rend3d.setSize(W, H);
  rend3d.setPixelRatio(Math.min(devicePixelRatio, 2));
  scene.add(new THREE.HemisphereLight(0xffffff, 0x334455, .75));
  const d1 = new THREE.DirectionalLight(0xffffff, .65); d1.position.set(3, 4, 3); scene.add(d1);
  const d2 = new THREE.DirectionalLight(0xffffff, .3); d2.position.set(-3, 2, -2); scene.add(d2);
  const grid = new THREE.GridHelper(6, 30, 0x1a2035, 0x1a2035); grid.position.y = -1.3; scene.add(grid);

  canvas.addEventListener('mousedown', e => { drag = true; pm = {x:e.clientX, y:e.clientY}; stopSpin() });
  window.addEventListener('mouseup', () => drag = false);
  window.addEventListener('mousemove', e => {
    if (!drag) return;
    cTheta -= (e.clientX - pm.x) * .008;
    cPhi = Math.max(.15, Math.min(Math.PI - .15, cPhi + (e.clientY - pm.y) * .008));
    pm = {x:e.clientX, y:e.clientY}; updateCam3d();
  });
  canvas.addEventListener('wheel', e => { e.preventDefault(); cDist = Math.max(.5, Math.min(12, cDist + e.deltaY * .004)); updateCam3d() }, {passive:false});
  canvas.addEventListener('touchstart', e => {
    stopSpin();
    if (e.touches.length === 2) pinch0 = {d: touchDist(e), c: cDist};
    else prevT = e.touches[0];
    e.preventDefault();
  }, {passive:false});
  canvas.addEventListener('touchmove', e => {
    if (e.touches.length === 2 && pinch0) {
      cDist = Math.max(.5, Math.min(12, pinch0.c * pinch0.d / touchDist(e))); updateCam3d();
    } else if (prevT && e.touches.length === 1) {
      const t = e.touches[0];
      cTheta -= (t.clientX - prevT.clientX) * .01;
      cPhi = Math.max(.15, Math.min(Math.PI - .15, cPhi + (t.clientY - prevT.clientY) * .01));
      prevT = t; updateCam3d();
    }
    e.preventDefault();
  }, {passive:false});
  canvas.addEventListener('touchend', () => { prevT = null; pinch0 = null });
  window.addEventListener('resize', resize3d);
  (function loop(){
    requestAnimationFrame(loop);
    if (modelGroup && modelGroup._spin) modelGroup.rotation.y += .003;
    rend3d.render(scene, cam3d);
  })();
}
const touchDist = e => Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
function stopSpin(){ if (modelGroup) modelGroup._spin = false }
function resize3d(){
  const cont = $('centerView'), W = cont.clientWidth, H = cont.clientHeight;
  if (!W || !H) return;
  cam3d.aspect = W/H; cam3d.updateProjectionMatrix(); rend3d.setSize(W, H);
}
function updateCam3d(){
  cam3d.position.set(cDist*Math.sin(cPhi)*Math.sin(cTheta), cDist*Math.cos(cPhi), cDist*Math.sin(cPhi)*Math.cos(cTheta));
  cam3d.lookAt(0, 0, 0);
}
function zoom3d(f){ cDist = Math.max(.5, Math.min(12, cDist / f)); updateCam3d() }
function reset3dCam(){ cDist = 3; cTheta = .3; cPhi = 1.1; updateCam3d() }

function clearModel(){
  if (!modelGroup) return;
  scene.remove(modelGroup);
  modelGroup.traverse(o => { o.geometry?.dispose(); o.material?.dispose() });
  modelGroup = cloudObj = meshObj = null;
}

// Os pontos já chegam centralizados e alinhados (Y = para cima). Aqui só ajusta o tamanho na tela.
function loadModel(res, sid){
  clearModel();
  $('ph').style.display = 'none';
  modelGroup = new THREE.Group(); modelGroup._spin = true;
  const pts = res.points, cols = res.colors || [];
  const pos = new Float32Array(pts.length * 3), col = new Float32Array(pts.length * 3);
  const radii = [];
  pts.forEach((p, i) => {
    pos[i*3] = p[0]; pos[i*3+1] = p[1]; pos[i*3+2] = p[2];
    const c = cols[i] || [.85, .85, .85];
    col[i*3] = c[0]; col[i*3+1] = c[1]; col[i*3+2] = c[2];
    if (i % 7 === 0) radii.push(Math.hypot(p[0], p[1], p[2]));
  });
  radii.sort((a, b) => a - b);
  const R = radii[Math.floor(radii.length * .97)] || 1;
  const k = 1.25 / R;
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  cloudObj = new THREE.Points(g, new THREE.PointsMaterial({size: .012 / k, vertexColors:true, sizeAttenuation:true}));
  modelGroup.add(cloudObj);
  modelGroup.scale.setScalar(k);
  scene.add(modelGroup);

  const sizeTxt = res.size ? ` · ${res.size.map(v => v.toFixed(1)).join(' × ')} ${res.units === 'mm' ? 'mm' : 'u'}` : '';
  $('vinfo').classList.add('show');
  $('vinfoTitle').textContent = res.session_name || 'Scan';
  $('vinfoSub').textContent = `${(res.point_count || pts.length).toLocaleString('pt-BR')} pontos · ${res.registered ?? '?'}/${res.frame_count ?? '?'} frames` +
                              (res.triangles ? ` · ${res.triangles.toLocaleString('pt-BR')} triângulos` : '') + sizeTxt +
                              (res.units === 'mm' ? ' · escala real' : ' · sem escala');
  ['vaPresent', 'vaNew'].forEach(id => $(id).classList.add('show'));
  $('vaStl').classList.toggle('show', !!res.has_mesh);
  $('vaMode').classList.toggle('show', !!res.has_mesh);
  reset3dCam();

  if (res.has_mesh && sid && THREE.PLYLoader) {
    new THREE.PLYLoader().load(API + '/scan/mesh/' + sid, geo => {
      if (!modelGroup || S.result !== res) { geo.dispose(); return }
      geo.computeVertexNormals();
      const hasCol = !!geo.getAttribute('color');
      meshObj = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
        color: hasCol ? 0xffffff : 0xefe6d6, vertexColors: hasCol, roughness: .55, metalness: .02, side: THREE.DoubleSide
      }));
      modelGroup.add(meshObj);
      setViewMode(S.viewMode);
    }, undefined, () => toast('Não foi possível carregar a malha', 'err'));
  } else {
    setViewMode('cloud');
  }
}

function setViewMode(m){
  S.viewMode = m;
  if (cloudObj) cloudObj.visible = (m === 'cloud' || !meshObj);
  if (meshObj) meshObj.visible = (m === 'mesh');
  $('vaMode').textContent = m === 'mesh' ? '◎ Nuvem' : '▲ Malha';
}
function toggleViewMode(){ setViewMode(S.viewMode === 'mesh' ? 'cloud' : 'mesh') }

function loadDemo(){
  const pts = [], cols = [];
  for (let t = 0; t < Math.PI; t += .05) {
    const r = 1.1 + Math.random() * .06, x = r * Math.cos(t), z = r * Math.sin(t) * .65;
    for (let y = -.28; y < .38; y += .04) {
      pts.push([x + (Math.random() - .5) * .04, y + (Math.random() - .5) * .02, z + (Math.random() - .5) * .04]);
      const c = .72 + Math.random() * .22; cols.push([c, c * .94, c * .87]);
    }
  }
  S.result = {points:pts, colors:cols, session_name:'Demonstração', point_count:pts.length, registered:'-', frame_count:'-', units:'-'};
  loadModel(S.result, null);
  $('vinfoSub').textContent = 'Demonstração — faça um scan para ver o seu modelo';
  $('vaStl').classList.remove('show');
}

// ════════════════════════════════════════
// SOCKET.IO
// ════════════════════════════════════════
let socket = null;
function initSocket(){
  try {
    socket = io(window.location.origin, {transports: ['websocket', 'polling']});
    socket.on('connect', () => { if (S.sessionId) socket.emit('join', {session_id: S.sessionId}) });
    socket.on('progress', d => { if (d.session_id === S.sessionId) setProgress(d.progress, d.msg) });
    socket.on('scan_done', d => { if (d.session_id === S.sessionId) onScanDone(d.session_id) });
    socket.on('scan_error', d => { if (d.session_id === S.sessionId) onScanError(d.error) });
  } catch(e) { console.log('Socket.IO indisponível — usando consulta periódica') }
}
function joinRoom(sid){ if (socket && socket.connected) socket.emit('join', {session_id: sid}) }

// ════════════════════════════════════════
// SOM & FEEDBACK
// ════════════════════════════════════════
let audioCtx = null;
function playClick(){
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.connect(g); g.connect(audioCtx.destination);
    o.frequency.value = 880;
    g.gain.setValueAtTime(.08, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(.001, audioCtx.currentTime + .05);
    o.start(); o.stop(audioCtx.currentTime + .05);
  } catch(e) {}
}
function flashFrame(){
  const el = $('camCounter');
  el.textContent = S.sent;
  el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 80);
}
function setQuality(q, label){
  $('qualFill').style.width = Math.max(0, Math.min(100, q)) + '%';
  $('qualVal').textContent = label ?? (q >= 70 ? 'Boa' : q >= 40 ? 'Média' : 'Baixa');
}
function updateCounter(){
  $('camFps').textContent = `${S.sent} frames` + (S.pending ? ` · enviando ${S.pending}` : '');
}

// ════════════════════════════════════════
// CÂMERA
// ════════════════════════════════════════
async function initCamera(){
  if (!navigator.mediaDevices?.getUserMedia) { toast('Câmera não suportada (use https ou localhost)', 'err'); return }
  try { S.stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}, width:{ideal:1920}, height:{ideal:1080}}, audio:false}) }
  catch(e1) {
    try { S.stream = await navigator.mediaDevices.getUserMedia({video:true, audio:false}) }
    catch(e2) { toast('Erro câmera: ' + e2.message, 'err'); return }
  }
  $('camVideo').srcObject = S.stream;
  $('btnCam').style.display = 'none';
  $('btnStopCam').style.display = '';
  $('btnCapture').disabled = false;
  toast('Câmera ativada!', 'ok');
}
function stopCamera(){
  if (S.capturing) stopCapture();
  S.stream?.getTracks().forEach(t => t.stop()); S.stream = null;
  $('camVideo').srcObject = null;
  $('btnStopCam').style.display = 'none';
  $('btnCam').style.display = '';
  $('btnCapture').disabled = true;
  toast('Câmera desligada', 'info');
}

// ════════════════════════════════════════
// CAPTURA (os frames vão sendo enviados enquanto captura)
// ════════════════════════════════════════
function markerMm(){
  if (!$('mkUse').checked) return 0;
  const v = parseFloat($('mkMm').value);
  return v > 0 ? v : 0;
}
function printMarker(){
  const mm = parseFloat($('mkMm').value) || 20;
  window.open(API + '/marker.png?mm=' + mm, '_blank');
  toast('Imprima em tamanho real (100%) e confira o quadrado com uma régua', 'info');
}

async function startCapture(){
  const def = 'Scan ' + new Date().toLocaleString('pt-BR', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
  const name = prompt('Nome do scan:', def);
  if (name === null) return;
  try {
    const d = await api('/session/new', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name: name || def, patient: S.patient, marker_mm: markerMm()})});
    S.sessionId = d.session_id;
    joinRoom(S.sessionId);
  } catch(e) { toast('Erro ao conectar no servidor: ' + e.message, 'err'); return }

  S.sent = 0; S.pending = 0; S.upQueue = Promise.resolve(); S.upErr = false; S.capturing = true;
  $('camRec').classList.add('on');
  $('btnCapture').style.display = 'none';
  $('btnStop').style.display = ''; $('btnStop').disabled = false;
  $('btnProcess').disabled = true;
  $('prog').classList.remove('show');
  setQuality(0, '—'); updateCounter();
  S.captureIv = setInterval(captureFrame, CAPTURE_MS);
  toast('Captura iniciada! Dê a volta devagar.', 'ok');
}

async function stopCapture(){
  clearInterval(S.captureIv); S.capturing = false;
  $('camRec').classList.remove('on');
  $('btnStop').style.display = 'none';
  $('btnCapture').style.display = ''; $('btnCapture').disabled = !S.stream;
  if (S.pending) toast(`Terminando de enviar ${S.pending} frame(s)...`, 'info');
  await S.upQueue;
  updateCounter();
  $('btnProcess').disabled = S.sent < MIN_FRAMES;
  toast(S.sent >= MIN_FRAMES ? `✅ ${S.sent} frames prontos — clique em Gerar Modelo 3D` : `Mínimo ${MIN_FRAMES} frames (você tem ${S.sent})`,
        S.sent >= MIN_FRAMES ? 'ok' : 'err');
}

function captureFrame(){
  const v = $('camVideo');
  if (!v.videoWidth || !S.capturing) return;
  if (S.sent + S.pending >= MAX_FRAMES) { stopCapture(); toast(`Limite de ${MAX_FRAMES} frames atingido`, 'info'); return }
  if (S.pending >= 6) { setQuality(30, 'Rede lenta'); return }   // não acumula na memória do celular
  const k = Math.min(1, 1600 / Math.max(v.videoWidth, v.videoHeight));
  const c = document.createElement('canvas');
  c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
  c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
  S.pending++; updateCounter();
  c.toBlob(blob => {
    if (!blob) { S.pending--; return }
    const sid = S.sessionId;
    S.upQueue = S.upQueue.then(() => uploadFrame(sid, blob));
  }, 'image/jpeg', .9);
}

async function uploadFrame(sid, blob){
  try {
    const fd = new FormData();
    fd.append('session_id', sid);
    fd.append('frame', blob, 'frame.jpg');
    const d = await api('/frame/upload', {method:'POST', body:fd});
    if (sid !== S.sessionId) return;
    if (d.accepted) { S.sent = d.total_frames; playClick(); flashFrame(); setQuality(d.quality) }
    else setQuality(d.quality || 0, d.reason);
  } catch(e) {
    if (!S.upErr) { S.upErr = true; toast('Erro no envio: ' + e.message, 'err') }
  } finally {
    S.pending = Math.max(0, S.pending - 1); updateCounter();
  }
}

// ════════════════════════════════════════
// PROCESSAMENTO
// ════════════════════════════════════════
let pollIv = null;
function setProgress(p, msg){
  const first = !$('prog').classList.contains('show');
  $('prog').classList.add('show');
  if (first) $('prog').scrollIntoView({block:'nearest', behavior:'smooth'});
  $('progFill').style.width = (p || 0) + '%';
  $('progMsg').textContent = msg || '';
}

async function processFrames(){
  if (!S.sessionId) { toast('Inicie uma captura primeiro', 'err'); return }
  $('btnProcess').disabled = true;
  await S.upQueue;
  setProgress(2, 'Enviando para processamento...');
  try {
    await api('/scan/process', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: S.sessionId, marker_mm: markerMm()})});
  } catch(e) { toast('Erro: ' + e.message, 'err'); $('btnProcess').disabled = false; return }
  joinRoom(S.sessionId);
  pollStatus(S.sessionId);
  refreshScans();
  toast('Processando... pode levar alguns minutos.', 'info');
}

function pollStatus(sid){
  clearInterval(pollIv);
  pollIv = setInterval(async () => {
    if (sid !== S.sessionId) { clearInterval(pollIv); return }
    try {
      const d = await api('/scan/status/' + sid);
      setProgress(d.progress, d.msg);
      if (d.status === 'done') onScanDone(sid);
      else if (d.status === 'error') onScanError(d.error);
    } catch(e) { /* servidor ocupado: tenta de novo */ }
  }, 2500);
}

function onScanDone(sid){
  clearInterval(pollIv);
  if (S._doneFor === sid) return;
  S._doneFor = sid;
  openScan(sid, true);
}
function onScanError(err){
  clearInterval(pollIv);
  setProgress(0, 'Erro: ' + (err || ''));
  toast('Erro: ' + (err || '').slice(0, 140), 'err');
  $('btnProcess').disabled = false;
  refreshScans();
}

async function openScan(sid, fresh){
  try {
    const d = await api('/scan/result/' + sid);
    S.sessionId = sid; S.result = d;
    loadModel(d, sid);
    if (fresh) {
      S.sent = 0; updateCounter(); $('btnProcess').disabled = true;
      toast(`✅ Modelo gerado: ${d.point_count.toLocaleString('pt-BR')} pontos` + (d.units === 'mm' ? ' (em mm)' : ' (sem escala)'), 'ok');
      if (d.units !== 'mm') setTimeout(() => toast('Sem escala: ' + (d.scale_msg || 'marcador não encontrado'), 'info'), 3400);
    }
    refreshScans();
    closeOverlays();
  } catch(e) { toast('Erro ao carregar resultado: ' + e.message, 'err') }
}

// ════════════════════════════════════════
// LISTA / HISTÓRICO
// ════════════════════════════════════════
async function refreshScans(){
  try { S.scans = await api('/scans/list') } catch(e) { return }
  renderScanList(); renderHistory();
}
const statusTxt = s => ({done:'', processing:'⚙️ processando', queued:'⏳ na fila', capturing:'📷 capturando', error:'⚠️ erro'}[s.status] ?? s.status);

function renderScanList(){
  const el = $('scanList');
  const list = S.scans.slice(0, 8);
  if (!list.length) { el.innerHTML = '<div style="font-size:.73rem;color:var(--muted);text-align:center;padding:14px 0">Nenhum scan ainda.</div>'; return }
  el.innerHTML = list.map(s => `
    <div class="scan-item ${s.id === S.sessionId ? 'active' : ''}" onclick="clickScan('${s.id}')">
      <div class="si-name">${esc(s.name)}</div>
      <div class="si-meta">${esc(s.patient || 'Sem paciente')} · ${fmtDate(s.created)}</div>
      ${s.status === 'done' ? `<div class="si-pts">${(s.points || 0).toLocaleString('pt-BR')} pts${s.units === 'mm' ? ' · mm' : ''}</div>`
        : s.status === 'error' ? `<div class="si-err">${esc(statusTxt(s))}</div>`
        : `<div class="si-meta">${esc(statusTxt(s))} ${s.status === 'processing' ? (s.progress || 0) + '%' : `· ${s.frames || 0} frames`}</div>`}
    </div>`).join('');
}

function clickScan(sid){
  const s = S.scans.find(x => x.id === sid);
  if (!s) return;
  if (s.status === 'done') return openScan(sid);
  S.sessionId = sid; joinRoom(sid);
  if (s.status === 'processing' || s.status === 'queued') { setProgress(s.progress, s.msg); pollStatus(sid) }
  else {
    // capturando ou erro: permite (re)processar os frames já enviados
    S.sent = s.frames || 0; updateCounter();
    $('btnProcess').disabled = S.sent < MIN_FRAMES;
    if (s.status === 'error') setProgress(0, 'Erro: ' + (s.error || ''));
    toast(`${s.frames || 0} frames neste scan — clique em Gerar Modelo 3D para ${s.status === 'error' ? 'tentar de novo' : 'processar'}`, 'info');
  }
  renderScanList();
}

function renderHistory(){
  const el = $('histList'); if (!el) return;
  const only = $('histOnlyPat')?.checked;
  const list = S.scans.filter(s => !only || (s.patient || '') === S.patient);
  if (!list.length) { el.innerHTML = '<div style="color:var(--muted);font-size:.8rem;text-align:center;padding:30px">Nenhum scan encontrado.</div>'; return }
  el.innerHTML = list.map(s => `
    <div class="hist-row">
      <div class="hr-main">
        <div class="hr-title">${esc(s.name)}</div>
        <div class="hr-sub">${esc(s.patient || 'Sem paciente')} · ${fmtDate(s.created)} · ${s.frames || 0} frames
          ${s.status === 'done' ? ` · ${(s.points || 0).toLocaleString('pt-BR')} pts · ${s.units === 'mm' ? 'em mm' : 'sem escala'}` : ' · ' + esc(statusTxt(s))}</div>
      </div>
      ${s.status === 'done' ? `<button onclick="openScan('${s.id}')">Abrir</button>` : `<button onclick="clickScan('${s.id}');closeOverlays()">Selecionar</button>`}
      ${s.has_mesh ? `<button onclick="downloadSTL('${s.id}')">STL</button>` : ''}
      <button class="del" onclick="deleteScan('${s.id}')">Excluir</button>
    </div>`).join('');
}

async function deleteScan(sid){
  const s = S.scans.find(x => x.id === sid);
  if (!confirm(`Excluir o scan "${s ? s.name : sid}"? Isso apaga as fotos e o modelo.`)) return;
  try {
    await api('/scan/' + sid, {method:'DELETE'});
    if (S.sessionId === sid) { S.sessionId = null; S.result = null; clearModel(); $('ph').style.display = ''; $('vinfo').classList.remove('show') }
    toast('Scan excluído', 'ok');
    refreshScans();
  } catch(e) { toast('Erro: ' + e.message, 'err') }
}

function resetScan(){
  clearInterval(pollIv);
  S.sessionId = null; S.sent = 0; S.pending = 0;
  updateCounter(); setQuality(0, '—');
  $('btnCapture').disabled = !S.stream;
  $('btnProcess').disabled = true;
  $('prog').classList.remove('show');
  renderScanList();
  toast('Pronto para novo scan', 'info');
}

function downloadSTL(sid){
  sid = sid || S.sessionId;
  const s = S.scans.find(x => x.id === sid);
  if (!sid || (s && !s.has_mesh) || (!s && !(S.result && S.result.has_mesh))) { toast('Este scan não tem malha STL', 'err'); return }
  window.location.href = API + '/scan/stl/' + sid;
}

// ════════════════════════════════════════
// PACIENTE & ODONTOGRAMA (salvo no servidor por paciente)
// ════════════════════════════════════════
const odoKey = () => encodeURIComponent(S.patient || '_sem_paciente');
let saveTm = null;

function setPatient(name){
  S.patient = (name || '').trim();
  lsSet('os_patient', S.patient);
  const shown = S.patient || 'Sem paciente';
  $('pName').textContent = shown; $('rpName').textContent = shown;
  $('pAv').textContent = S.patient ? S.patient.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase() : '?';
  loadOdontograma();
  renderHistory();
}
function editPatient(){
  const name = prompt('Nome do paciente:', S.patient);
  if (name === null) return;
  setPatient(name);
  toast('Paciente: ' + (S.patient || 'sem paciente'), 'ok');
}

async function loadOdontograma(){
  S.tooth = null;
  let d = {};
  try { d = await api('/odontograma/' + odoKey()) } catch(e) {}
  S.toothData = d.teeth || {};
  ODO.setState(S.toothData);
  ODO.selected = null; ODO.draw();
  $('tcNum').textContent = '—'; $('tcName').textContent = 'Clique num dente';
  $('toothNotes').value = '';
  document.querySelectorAll('.face').forEach(b => b.classList.remove('active', 'affected'));
}

function saveOdontograma(){
  $('saveInd').textContent = 'Salvando...';
  clearTimeout(saveTm);
  saveTm = setTimeout(async () => {
    try {
      await api('/odontograma/' + odoKey(), {method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({teeth: S.toothData, updated: Date.now()})});
      $('saveInd').textContent = '✓ Salvo';
    } catch(e) { $('saveInd').textContent = '⚠ Não salvou: ' + e.message }
  }, 600);
}

function toothRec(num){
  if (!S.toothData[num]) S.toothData[num] = {treat:null, faces:[], notes:''};
  return S.toothData[num];
}
function refreshTooth(num){
  ODO.setState(S.toothData); ODO.draw();
  if (num === S.tooth) selectTooth(num, true);
  saveOdontograma();
}

function selTreat(type, el){
  S.treat = type;
  document.querySelectorAll('.tc').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  if (S.tooth) { toothRec(S.tooth).treat = type; refreshTooth(S.tooth) }
  updatePresInfo();
  toast(TREAT_INFO[type].title + (S.tooth ? ' → dente ' + S.tooth : ''), 'info');
}

function clearTooth(){
  if (!S.tooth) { toast('Selecione um dente primeiro', 'err'); return }
  delete S.toothData[S.tooth];
  refreshTooth(S.tooth);
  toast('Dente ' + S.tooth + ' sem marcação', 'info');
}

function toggleFace(btn){
  if (!S.tooth) { toast('Selecione um dente primeiro', 'err'); return }
  const face = btn.dataset.face, data = toothRec(S.tooth);
  const i = data.faces.indexOf(face);
  if (i >= 0) data.faces.splice(i, 1); else data.faces.push(face);
  if (!data.treat && S.treat) data.treat = S.treat;
  refreshTooth(S.tooth);
}

function saveNotes(){
  if (!S.tooth) return;
  toothRec(S.tooth).notes = $('toothNotes').value;
  saveOdontograma();
}

function selectTooth(num, silent){
  S.tooth = num;
  const data = S.toothData[num] || {treat:null, faces:[], notes:''};
  $('tcNum').textContent = 'Dente ' + num;
  $('tcName').textContent = (TOOTH_NAMES[num] || 'Dente ' + num) + (data.treat ? ' · ' + TREAT_INFO[data.treat].title : '');
  $('tcStatus').textContent = data.treat ? '⚠️ Atenção' : 'OK';
  $('tcStatus').className = 'tc-status ' + (data.treat ? 'warn' : 'ok');
  document.querySelectorAll('.face').forEach(b => {
    b.classList.remove('active', 'affected');
    if (data.faces?.includes(b.dataset.face)) b.classList.add(data.treat ? 'affected' : 'active');
  });
  document.querySelectorAll('.tc').forEach(c => c.classList.toggle('active', !!data.treat && c.classList.contains(data.treat)));
  if (data.treat) { S.treat = data.treat; updatePresInfo() }
  if (document.activeElement !== $('toothNotes')) $('toothNotes').value = data.notes || '';
  if (silent) return;
  if (window.innerWidth <= 768) mobileNav('treat');
}

// ════════════════════════════════════════
// TABS & MOBILE NAV
// ════════════════════════════════════════
function closeOverlays(){
  $('odoOverlay').classList.remove('show');
  $('histOverlay').classList.remove('show');
  document.querySelectorAll('.tab').forEach((t, i) => t.classList.toggle('active', i === 0));
  S.tab = 'scan';
}
function showOdo(){
  $('odoOverlay').classList.add('show');
  setTimeout(() => { ODO.resize(); ODO.buildTeeth(); ODO.draw() }, 60);
}
function switchTab(tab, el){
  S.tab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  $('odoOverlay').classList.remove('show');
  $('histOverlay').classList.remove('show');
  if (tab === 'odonto') showOdo();
  if (tab === 'historico') { $('histOverlay').classList.add('show'); refreshScans() }
}
function mobileNav(panel){
  document.querySelectorAll('.mnav').forEach(b => b.classList.remove('active'));
  $('mnav-' + panel)?.classList.add('active');
  const right = $('panelRight');
  right.classList.remove('open');
  $('odoOverlay').classList.remove('show');
  $('histOverlay').classList.remove('show');
  if (panel === 'treat') right.classList.add('open');
  else if (panel === 'odo') showOdo();
  else if (panel === 'hist') { $('histOverlay').classList.add('show'); refreshScans() }
}

// ════════════════════════════════════════
// APRESENTAÇÃO
// ════════════════════════════════════════
let presR = null, presScene = null, presC = null, presObj = null, presInit = false;
let pDist = 3, pTh = .3, pPh = 1.1, pDrag = false, pp = {x:0, y:0}, pt0 = null;

function enterPres(){
  $('presMode').classList.add('show');
  $('presPatient').textContent = 'Paciente: ' + (S.patient || '—');
  updatePresInfo();
  initPresThree();
}
function exitPres(){ $('presMode').classList.remove('show') }
function updatePresInfo(){
  const t = TREAT_INFO[S.treat];
  if (!t) return;
  const el = $('presTitle');
  el.textContent = t.title + (S.tooth ? ' — dente ' + S.tooth : ''); el.style.color = t.color;
  $('presDesc').textContent = t.desc;
  $('presSteps').innerHTML = t.steps.map((s, i) => `
    <div class="pstep"><div class="pstep-num">${i + 1}</div><div class="pstep-text">${esc(s)}</div></div>`).join('');
}
function pCam(){ presC.position.set(pDist*Math.sin(pPh)*Math.sin(pTh), pDist*Math.cos(pPh), pDist*Math.sin(pPh)*Math.cos(pTh)); presC.lookAt(0, 0, 0) }

function initPresThree(){
  const canvas = $('presCanvas'), cont = canvas.parentElement;
  const W = cont.clientWidth || 600, H = cont.clientHeight || 400;
  if (!presInit) {
    presInit = true;
    presScene = new THREE.Scene();
    presC = new THREE.PerspectiveCamera(45, W/H, .01, 200);
    presR = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true});
    presR.setPixelRatio(Math.min(devicePixelRatio, 2));
    presScene.add(new THREE.HemisphereLight(0xffffff, 0x334455, .8));
    const d = new THREE.DirectionalLight(0xffffff, .6); d.position.set(2, 3, 2); presScene.add(d);
    canvas.addEventListener('mousedown', e => { pDrag = true; pp = {x:e.clientX, y:e.clientY} });
    window.addEventListener('mouseup', () => pDrag = false);
    window.addEventListener('mousemove', e => { if (!pDrag) return; pTh -= (e.clientX - pp.x) * .008; pPh = Math.max(.15, Math.min(Math.PI - .15, pPh + (e.clientY - pp.y) * .008)); pp = {x:e.clientX, y:e.clientY}; pCam() });
    canvas.addEventListener('wheel', e => { e.preventDefault(); pDist = Math.max(.6, Math.min(10, pDist + e.deltaY * .004)); pCam() }, {passive:false});
    canvas.addEventListener('touchstart', e => { pt0 = e.touches[0] }, {passive:true});
    canvas.addEventListener('touchmove', e => { if (!pt0) return; const t = e.touches[0]; pTh -= (t.clientX - pt0.clientX) * .012; pPh = Math.max(.15, Math.min(Math.PI - .15, pPh + (t.clientY - pt0.clientY) * .012)); pt0 = t; pCam() }, {passive:true});
    window.addEventListener('resize', () => { if (!presR) return; const W = cont.clientWidth, H = cont.clientHeight; if (!W || !H) return; presC.aspect = W/H; presC.updateProjectionMatrix(); presR.setSize(W, H) });
  }
  presC.aspect = W/H; presC.updateProjectionMatrix(); presR.setSize(W, H);
  pDist = 3; pTh = .3; pPh = 1.1; pCam();
  if (presObj) presScene.remove(presObj);
  presObj = modelGroup ? modelGroup.clone() : null;
  if (presObj) { presObj.rotation.set(0, 0, 0); presScene.add(presObj) }
  (function loop(){
    if (!$('presMode').classList.contains('show')) return;
    requestAnimationFrame(loop);
    if (presObj && !pDrag && !pt0) presObj.rotation.y += .005;
    presR.render(presScene, presC);
  })();
}

// ════════════════════════════════════════
// RELATÓRIO (abre para imprimir / salvar em PDF)
// ════════════════════════════════════════
function printReport(){
  ODO.resize(); ODO.buildTeeth(); ODO.draw();
  const odoImg = ODO.canvas.toDataURL('image/png');
  let modelImg = '';
  try { if (modelGroup) { rend3d.render(scene, cam3d); modelImg = rend3d.domElement.toDataURL('image/png') } } catch(e) {}
  const rows = Object.entries(S.toothData).filter(([, d]) => d.treat || (d.faces && d.faces.length) || d.notes)
    .sort((a, b) => a[0] - b[0])
    .map(([n, d]) => `<tr><td>${n}</td><td>${esc(TOOTH_NAMES[n] || '')}</td><td>${d.treat ? esc(TREAT_INFO[d.treat].title) : '-'}</td><td>${esc((d.faces || []).join(', ') || '-')}</td><td>${esc(d.notes || '')}</td></tr>`).join('');
  const r = S.result;
  const w = window.open('', '_blank');
  if (!w) { toast('Permita pop-ups para gerar o relatório', 'err'); return }
  w.document.write(`<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"><title>Relatório — ${esc(S.patient || 'Paciente')}</title>
  <style>body{font-family:Arial,sans-serif;margin:28px;color:#111}h1{font-size:20px;margin:0 0 4px}.sub{color:#555;font-size:12px;margin-bottom:18px}
  img{max-width:100%;border:1px solid #ddd;border-radius:6px;margin:6px 0 16px;background:#07090f}table{border-collapse:collapse;width:100%;font-size:12px}
  th,td{border:1px solid #ccc;padding:6px 8px;text-align:left}th{background:#f2f2f2}h2{font-size:15px;margin:18px 0 6px}</style></head><body>
  <h1>Relatório Odontológico</h1>
  <div class="sub">Paciente: <b>${esc(S.patient || '—')}</b> · ${new Date().toLocaleString('pt-BR')}</div>
  <h2>Odontograma</h2><img src="${odoImg}">
  <h2>Achados por dente</h2>
  ${rows ? `<table><tr><th>Dente</th><th>Nome</th><th>Tratamento</th><th>Faces</th><th>Anotações</th></tr>${rows}</table>` : '<p>Nenhuma marcação.</p>'}
  ${modelImg && r ? `<h2>Modelo 3D — ${esc(r.session_name || '')}</h2><img src="${modelImg}">
  <div class="sub">${(r.point_count || 0).toLocaleString('pt-BR')} pontos${r.size ? ' · ' + r.size.map(v => v.toFixed(1)).join(' × ') + (r.units === 'mm' ? ' mm' : '') : ''}</div>` : ''}
  <script>window.onload=()=>setTimeout(()=>window.print(),300)<\/script></body></html>`);
  w.document.close();
}

// ════════════════════════════════════════
// TOAST
// ════════════════════════════════════════
let toastTm;
function toast(msg, type = 'info'){
  const el = $('toast');
  el.className = 'toast ' + type; el.textContent = msg; el.classList.add('show');
  clearTimeout(toastTm); toastTm = setTimeout(() => el.classList.remove('show'), 3400);
}

// ════════════════════════════════════════
// INIT
// ════════════════════════════════════════
window.addEventListener('load', async () => {
  initThree();
  ODO.init('odoCanvas');
  ODO.onSelect = num => selectTooth(num);
  initSocket();
  $('mkMm').value = lsGet('os_marker_mm', '20');
  $('mkUse').checked = lsGet('os_marker_use', '1') === '1';
  $('mkMm').addEventListener('change', () => lsSet('os_marker_mm', $('mkMm').value));
  $('mkUse').addEventListener('change', () => lsSet('os_marker_use', $('mkUse').checked ? '1' : '0'));
  setPatient(lsGet('os_patient', ''));
  await refreshScans();
  const last = S.scans.find(s => s.status === 'done');
  if (last) await openScan(last.id); else loadDemo();
  const running = S.scans.find(s => s.status === 'processing' || s.status === 'queued');
  if (running) { S.sessionId = running.id; joinRoom(running.id); setProgress(running.progress, running.msg); pollStatus(running.id) }
});
