"""
OdontoScan 3D — servidor
Flask + Socket.IO (modo threading) · COLMAP · SQLite
"""
import os, re, json, time, uuid, shutil, sqlite3, threading

import numpy as np
import cv2
from flask import Flask, request, jsonify, send_file, render_template, abort
from flask_socketio import SocketIO, join_room

import pipeline

# Open3D é usado para reconstrução de superfície de alta qualidade
# (Poisson) e limpeza estatística de outliers. Import defensivo: se por
# algum motivo não estiver disponível no ambiente, o sistema cai
# automaticamente no método antigo (Convex Hull) sem quebrar.
try:
    import open3d as o3d
    HAS_OPEN3D = True
except Exception:
    HAS_OPEN3D = False

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or uuid.uuid4().hex
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024   # por requisição (1 frame)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# ── CONFIG ─────────────────────────────────────────────────────────────────────
IS_RAILWAY = os.environ.get('RAILWAY_ENVIRONMENT') is not None
DATA_DIR = os.environ.get('DATA_DIR') or ('/data' if IS_RAILWAY else
                                          os.path.join(os.path.expanduser('~'), 'Desktop', 'odontoscan_work'))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH    = os.path.join(DATA_DIR, 'odontoscan.db')
MAX_SIDE   = int(os.environ.get('MAX_IMAGE_SIDE', 1600))
MAX_FRAMES = int(os.environ.get('MAX_FRAMES', 180))
MIN_FRAMES = 8
SID_RE     = re.compile(r'^[A-F0-9]{10}$')

_db_lock    = threading.Lock()
_frame_lock = threading.Lock()
_job_sem    = threading.Semaphore(int(os.environ.get('MAX_JOBS', 1)))   # 1 reconstrução por vez


# ── BANCO ──────────────────────────────────────────────────────────────────────
def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def db_init():
    with _db_lock, db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS scans(
            id TEXT PRIMARY KEY, name TEXT, patient TEXT, status TEXT, progress INTEGER,
            msg TEXT, error TEXT, created REAL, frames INTEGER DEFAULT 0, points INTEGER DEFAULT 0,
            marker_mm REAL, units TEXT, has_mesh INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS odontograma(
            patient TEXT PRIMARY KEY, data TEXT, updated REAL);
        ''')
        # watchdog: o que estava processando quando o servidor caiu volta como erro
        c.execute("UPDATE scans SET status='error', progress=0, "
                  "error='Interrompido (servidor reiniciou). Clique em Gerar Modelo 3D de novo.', "
                  "msg='Interrompido' WHERE status IN ('processing','queued')")


def scan_get(sid):
    with _db_lock, db() as c:
        r = c.execute('SELECT * FROM scans WHERE id=?', (sid,)).fetchone()
    return dict(r) if r else None


def scan_set(sid, **kw):
    if not kw:
        return
    cols = ', '.join(f'{k}=?' for k in kw)
    with _db_lock, db() as c:
        c.execute(f'UPDATE scans SET {cols} WHERE id=?', (*kw.values(), sid))


def valid_sid(sid):
    if not sid or not SID_RE.match(sid):
        abort(400, 'Sessão inválida')
    return sid


def sdir(sid):
    return os.path.join(DATA_DIR, 'scans', sid)


db_init()



def _rebuild_frame_cache(sid):
    """Reconstrói o cache de frames de uma sessão a partir do disco."""
    img_dir = os.path.join(DATA_DIR, sid, 'images')
    if not os.path.isdir(img_dir):
        sessions[sid] = []
        return
    files = sorted(f for f in os.listdir(img_dir) if f.lower().endswith('.jpg'))
    sessions[sid] = [os.path.join(img_dir, f) for f in files]



# ── FRONTEND ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/ping')
def ping():
    return jsonify({'status': 'ok', 'railway': IS_RAILWAY})


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'colmap': pipeline.colmap_info(), 'data_dir': DATA_DIR,
                    'threads': pipeline.MAX_THREADS})


@app.route('/api/marker.png')
def marker():
    try:
        mm = float(request.args.get('mm', 20))
    except ValueError:
        mm = 20.0
    mm = min(max(mm, 5), 100)
    return send_file(pipeline.marker_png(mm), mimetype='image/png',
                     download_name=f'marcador_odontoscan_{mm:g}mm.png')


# ── SESSÃO ─────────────────────────────────────────────────────────────────────
@app.route('/api/session/new', methods=['POST'])
def new_session():
    data = request.get_json(silent=True) or {}
    sid = uuid.uuid4().hex[:10].upper()
    try:
        marker_mm = float(data.get('marker_mm') or 0) or None
    except (TypeError, ValueError):
        marker_mm = None
    name = (str(data.get('name') or '').strip() or f'Scan {time.strftime("%d/%m %H:%M")}')[:80]
    patient = str(data.get('patient') or '').strip()[:80]
    os.makedirs(os.path.join(sdir(sid), 'images'), exist_ok=True)
    with _db_lock, db() as c:
        c.execute('INSERT INTO scans(id,name,patient,status,progress,msg,created,marker_mm) '
                  'VALUES(?,?,?,?,?,?,?,?)',
                  (sid, name, patient, 'capturing', 0, 'Aguardando frames...', time.time(), marker_mm))
    return jsonify({'session_id': sid, 'status': 'ok'})


# ── UPLOAD DE FRAME ────────────────────────────────────────────────────────────
def _decode_frame():
    f = request.files.get('frame')
    if f:
        buf = f.read()
    else:
        data = request.get_json(silent=True) or {}
        b64 = data.get('frame') or ''
        if ',' in b64:
            b64 = b64.split(',', 1)[1]
        import base64
        try:
            buf = base64.b64decode(b64)
        except Exception:
            return None
    if not buf:
        return None
    return cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)


@app.route('/api/frame/upload', methods=['POST'])
def upload_frame():
    sid = valid_sid(request.form.get('session_id') or (request.get_json(silent=True) or {}).get('session_id'))
    s = scan_get(sid)
    if not s:
        return jsonify({'error': 'Sessão não encontrada'}), 404
    if s['status'] in ('processing', 'queued'):
        return jsonify({'error': 'Scan já está sendo processado'}), 409

    img = _decode_frame()
    if img is None:
        return jsonify({'error': 'Frame inválido'}), 400

    h, w = img.shape[:2]
    if max(h, w) > MAX_SIDE:
        k = MAX_SIDE / max(h, w)
        img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(cv2.resize(gray, (640, int(640 * gray.shape[0] / gray.shape[1]))),
                                cv2.CV_64F).var())
    kp = cv2.ORB_create(800).detect(gray, None)
    nkp = len(kp)
    quality = int(min(100, nkp / 6) * 0.6 + min(100, sharp / 2) * 0.4)

    if nkp < 40:
        return jsonify({'accepted': False, 'reason': 'sem textura/escuro', 'keypoints': nkp,
                        'sharpness': round(sharp), 'quality': quality, 'total_frames': s['frames']})

    img_dir = os.path.join(sdir(sid), 'images')
    with _frame_lock:
        idx = len([n for n in os.listdir(img_dir) if n.endswith('.jpg')])
        if idx >= MAX_FRAMES:
            return jsonify({'accepted': False, 'reason': f'limite de {MAX_FRAMES} frames',
                            'total_frames': idx, 'quality': quality}), 200
        cv2.imwrite(os.path.join(img_dir, f'{idx:04d}.jpg'), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        scan_set(sid, frames=idx + 1)

    return jsonify({'accepted': True, 'frame_index': idx, 'total_frames': idx + 1,
                    'keypoints': nkp, 'sharpness': round(sharp), 'quality': quality})


# ── PROCESSAR ──────────────────────────────────────────────────────────────────
@app.route('/api/scan/process', methods=['POST'])
def process_scan():
    data = request.get_json(silent=True) or {}
    sid = valid_sid(data.get('session_id'))
    s = scan_get(sid)
    if not s:
        return jsonify({'error': 'Sessão não encontrada'}), 404
    if s['status'] in ('processing', 'queued'):
        return jsonify({'status': s['status']})
    if (s['frames'] or 0) < MIN_FRAMES:
        return jsonify({'error': f'Mínimo {MIN_FRAMES} frames. Você tem {s["frames"] or 0}.'}), 400
    if 'marker_mm' in data:
        try:
            scan_set(sid, marker_mm=float(data['marker_mm']) or None)
        except (TypeError, ValueError):
            pass
    scan_set(sid, status='queued', progress=2, msg='Na fila...', error=None)
    socketio.start_background_task(_job, sid)
    return jsonify({'status': 'queued'})


def _update(sid, progress, msg, status=None):
    kw = {'progress': int(progress), 'msg': msg}
    if status:
        kw['status'] = status
    scan_set(sid, **kw)
    socketio.emit('progress', {'session_id': sid, 'progress': int(progress), 'msg': msg}, to=sid)


def _job(sid):
    with _job_sem:
        s = scan_get(sid)
        _update(sid, 5, 'Iniciando COLMAP...', status='processing')
        try:
            res = pipeline.reconstruct(sdir(sid), s['frames'], s['marker_mm'],
                                       lambda p, m: _update(sid, p, m))
            msg = f'Concluído! {res["point_count"]:,} pontos'.replace(',', '.')
            msg += ' · malha OK' if res['has_mesh'] else f' · sem malha ({res["mesh_msg"]})'
            if res['units'] != 'mm':
                msg += ' · sem escala'
            scan_set(sid, status='done', progress=100, msg=msg, points=res['point_count'],
                     units=res['units'], has_mesh=int(res['has_mesh']))
            socketio.emit('progress', {'session_id': sid, 'progress': 100, 'msg': msg}, to=sid)
            socketio.emit('scan_done', {'session_id': sid, 'point_count': res['point_count']}, to=sid)
        except Exception as e:
            err = str(e)
            scan_set(sid, status='error', progress=0, error=err, msg='Erro')
            socketio.emit('scan_error', {'session_id': sid, 'error': err}, to=sid)


# ── CONSULTAS ──────────────────────────────────────────────────────────────────
@app.route('/api/scan/status/<sid>')
def scan_status(sid):
    s = scan_get(valid_sid(sid))
    if not s:
        return jsonify({'error': 'Não encontrado'}), 404
    return jsonify({k: s[k] for k in ('status', 'progress', 'msg', 'points', 'error', 'frames')})


@app.route('/api/scan/result/<sid>')
def scan_result(sid):
    path = os.path.join(sdir(valid_sid(sid)), 'result.json')
    if not os.path.exists(path):
        return jsonify({'error': 'Resultado não disponível'}), 404
    s = scan_get(sid) or {}
    with open(path) as f:
        res = json.load(f)
    res['session_name'] = s.get('name')
    res['patient'] = s.get('patient')
    return jsonify(res)


@app.route('/api/scan/stl/<sid>')
def download_stl(sid):
    path = os.path.join(sdir(valid_sid(sid)), 'model.stl')
    if not os.path.exists(path):
        return jsonify({'error': 'STL não disponível'}), 404
    s = scan_get(sid) or {}
    nome = re.sub(r'[^\w\-]+', '_', f"{s.get('patient') or ''}_{s.get('name') or sid}").strip('_')
    return send_file(path, as_attachment=True, download_name=f'{nome or sid}.stl')


@app.route('/api/scan/mesh/<sid>')
def download_mesh(sid):
    path = os.path.join(sdir(valid_sid(sid)), 'mesh.ply')
    if not os.path.exists(path):
        return jsonify({'error': 'Malha não disponível'}), 404
    return send_file(path, mimetype='application/octet-stream')


@app.route('/api/scans/list')
def list_scans():
    patient = request.args.get('patient')
    q, args = 'SELECT * FROM scans', ()
    if patient:
        q, args = q + ' WHERE patient=?', (patient,)
    with _db_lock, db() as c:
        rows = [dict(r) for r in c.execute(q + ' ORDER BY created DESC LIMIT 300', args)]
    return jsonify(rows)


@app.route('/api/scan/<sid>', methods=['DELETE'])
def delete_scan(sid):
    s = scan_get(valid_sid(sid))
    if not s:
        return jsonify({'error': 'Não encontrado'}), 404
    if s['status'] in ('processing', 'queued'):
        return jsonify({'error': 'Aguarde terminar o processamento'}), 409
    shutil.rmtree(sdir(sid), ignore_errors=True)
    with _db_lock, db() as c:
        c.execute('DELETE FROM scans WHERE id=?', (sid,))
    return jsonify({'ok': True})


# ── ODONTOGRAMA ────────────────────────────────────────────────────────────────
@app.route('/api/odontograma/<path:patient>', methods=['GET', 'PUT'])
def odontograma(patient):
    patient = patient.strip()[:80]
    if not patient:
        abort(400)
    if request.method == 'GET':
        with _db_lock, db() as c:
            r = c.execute('SELECT data FROM odontograma WHERE patient=?', (patient,)).fetchone()
        return jsonify(json.loads(r['data']) if r else {})
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    with _db_lock, db() as c:
        c.execute('INSERT INTO odontograma(patient,data,updated) VALUES(?,?,?) '
                  'ON CONFLICT(patient) DO UPDATE SET data=excluded.data, updated=excluded.updated',
                  (patient, json.dumps(data), time.time()))
    return jsonify({'ok': True})


# ── WEBSOCKET ──────────────────────────────────────────────────────────────────
@socketio.on('join')
def on_join(data):
    sid = (data or {}).get('session_id')
    if sid and SID_RE.match(sid):
        join_room(sid)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f'OdontoScan 3D — porta {port} — Railway: {IS_RAILWAY}')
    print(f'Dados: {DATA_DIR}')
    print(f'COLMAP: {pipeline.colmap_info()}')
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
