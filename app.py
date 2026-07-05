import eventlet
eventlet.monkey_patch()
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import os, uuid, json, base64, subprocess, shutil, threading, time
import numpy as np
import cv2
from PIL import Image
import io
import trimesh
from scipy.spatial import ConvexHull
from sklearn.neighbors import NearestNeighbors

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'odontoscan2024')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ── CONFIGURAÇÕES ──────────────────────────────────────────────────────────────
IS_RAILWAY = os.environ.get('RAILWAY_ENVIRONMENT') is not None
DATA_DIR   = '/data' if IS_RAILWAY else os.path.join(os.path.expanduser('~'), 'Desktop', 'odontoscan_work')
os.makedirs(DATA_DIR, exist_ok=True)

# COLMAP path
if IS_RAILWAY:
    COLMAP = 'colmap'  # Instalado via apt no Railway
else:
    COLMAP = r'C:\Users\Gabriela\Desktop\bin\colmap.exe'

sessions = {}

# ── FRONTEND ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_file(f'static/{filename}')

# ── PING ───────────────────────────────────────────────────────────────────────
@app.route('/api/ping')
def ping():
    return jsonify({'status': 'ok', 'railway': IS_RAILWAY, 'colmap': COLMAP})

# ── NOVA SESSÃO ────────────────────────────────────────────────────────────────
@app.route('/api/session/new', methods=['POST'])
def new_session():
    data = request.json or {}
    sid  = str(uuid.uuid4())[:8].upper()
    sessions[sid] = {
        'name':     data.get('name', f'Scan_{sid}'),
        'frames':   [],
        'status':   'capturing',
        'progress': 0,
        'msg':      'Aguardando frames...',
        'created':  time.time(),
        'points':   0
    }
    os.makedirs(os.path.join(DATA_DIR, sid, 'images'), exist_ok=True)
    return jsonify({'session_id': sid, 'status': 'ok'})

# ── UPLOAD DE FRAME ─────────────────────────────────────────────────────────────
@app.route('/api/frame/upload', methods=['POST'])
def upload_frame():
    data = request.json
    sid  = data.get('session_id')
    if not sid or sid not in sessions:
        return jsonify({'error': 'Sessão inválida'}), 400

    img_b64   = data['frame'].split(',')[1]
    img_bytes = base64.b64decode(img_b64)
    arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is None:
        return jsonify({'error': 'Frame inválido'}), 400

    idx      = len(sessions[sid]['frames'])
    img_path = os.path.join(DATA_DIR, sid, 'images', f'{idx:04d}.jpg')
    cv2.imwrite(img_path, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    sessions[sid]['frames'].append(img_path)

    # Keypoints para feedback
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    orb  = cv2.ORB_create(500)
    kp, _ = orb.detectAndCompute(gray, None)

    # Notifica via WebSocket
    socketio.emit('frame_received', {
        'session_id': sid,
        'frame_index': idx,
        'total': idx + 1,
        'keypoints': len(kp)
    })

    return jsonify({'frame_index': idx, 'keypoints': len(kp), 'total_frames': idx + 1})

# ── PROCESSAR ──────────────────────────────────────────────────────────────────
@app.route('/api/scan/process', methods=['POST'])
def process_scan():
    data = request.json
    sid  = data.get('session_id')
    if not sid or sid not in sessions:
        return jsonify({'error': 'Sessão inválida'}), 400

    sess = sessions[sid]
    if len(sess['frames']) < 6:
        return jsonify({'error': f'Mínimo 6 frames. Você tem {len(sess["frames"])}.'}), 400

    sess['status'] = 'processing'
    t = threading.Thread(target=_run_reconstruction, args=(sid,))
    t.daemon = True
    t.start()
    return jsonify({'status': 'processing'})

def _update(sid, progress, msg):
    """Atualiza progresso via WebSocket e sessão"""
    sessions[sid]['progress'] = progress
    sessions[sid]['msg']      = msg
    socketio.emit('progress', {'session_id': sid, 'progress': progress, 'msg': msg})

def _run_reconstruction(sid):
    sess   = sessions[sid]
    base   = os.path.join(DATA_DIR, sid)
    img_dir   = os.path.join(base, 'images')
    db_path   = os.path.join(base, 'database.db')
    sparse_dir = os.path.join(base, 'sparse')
    os.makedirs(sparse_dir, exist_ok=True)

    try:
        # ── PASSO 1: Feature extraction ──
        _update(sid, 10, 'Extraindo pontos de interesse...')
        cmd = f'"{COLMAP}" feature_extractor --database_path "{db_path}" --image_path "{img_dir}" --ImageReader.single_camera 1 --FeatureExtraction.use_gpu 0'
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise Exception('Feature extraction falhou: ' + r.stderr[-400:])

        # ── PASSO 2: Matching ──
        _update(sid, 30, 'Calculando correspondências entre frames...')
        cmd = f'"{COLMAP}" exhaustive_matcher --database_path "{db_path}" --FeatureMatching.use_gpu 0'
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise Exception('Matching falhou: ' + r.stderr[-400:])

        # ── PASSO 3: Reconstrução sparse ──
        _update(sid, 50, 'Reconstruindo geometria 3D...')
        cmd = f'"{COLMAP}" mapper --database_path "{db_path}" --image_path "{img_dir}" --output_path "{sparse_dir}"'
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise Exception('Mapper falhou: ' + r.stderr[-400:])

        # Encontra modelo gerado
        model_dir = None
        for d in sorted(os.listdir(sparse_dir)):
            candidate = os.path.join(sparse_dir, d)
            if os.path.isdir(candidate):
                model_dir = candidate
                break
        if not model_dir:
            raise Exception('Nenhum modelo gerado. Tente com mais frames e melhor iluminação.')

        # ── PASSO 4: Exporta para TXT ──
        _update(sid, 65, 'Exportando modelo...')
        txt_dir = os.path.join(base, 'sparse_txt')
        os.makedirs(txt_dir, exist_ok=True)
        cmd = f'"{COLMAP}" model_converter --input_path "{model_dir}" --output_path "{txt_dir}" --output_type TXT'
        subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)

        # ── PASSO 5: Lê pontos 3D ──
        _update(sid, 75, 'Lendo pontos 3D...')
        pts3d_file = os.path.join(txt_dir, 'points3D.txt')
        points, colors = [], []

        if os.path.exists(pts3d_file):
            with open(pts3d_file) as f:
                for line in f:
                    if line.startswith('#') or not line.strip():
                        continue
                    parts = line.strip().split()
                    if len(parts) >= 7:
                        try:
                            x,y,z = float(parts[1]),float(parts[2]),float(parts[3])
                            r2,g,b = int(parts[4]),int(parts[5]),int(parts[6])
                            points.append([x,y,z])
                            colors.append([r2/255,g/255,b/255])
                        except: pass

        if len(points) < 6:
            raise Exception(f'Poucos pontos gerados ({len(points)}). Use mais frames com boa iluminação.')

        pts  = np.array(points)
        cols = np.array(colors)

        # ── PASSO 6: Remove outliers ──
        _update(sid, 80, 'Removendo outliers...')
        center = pts.mean(axis=0)
        dists  = np.linalg.norm(pts - center, axis=1)
        mask   = dists < (dists.mean() + 2.5 * dists.std())
        pts    = pts[mask]
        cols   = cols[mask]

        # ── PASSO 7: Densifica nuvem ──
        _update(sid, 85, 'Densificando nuvem de pontos...')
        pts, cols = _densify(pts, cols)

        # ── PASSO 8: Normaliza ──
        center = pts.mean(axis=0)
        pts   -= center
        scale  = np.percentile(np.abs(pts), 95)
        if scale > 0:
            pts /= scale

        # ── PASSO 9: Gera STL ──
        _update(sid, 92, 'Gerando malha 3D...')
        stl_path = None
        try:
            stl_path = _make_mesh(pts, cols, base)
        except: pass

        # ── PASSO 10: Salva resultado ──
        _update(sid, 97, 'Salvando resultado...')
        result = {
            'session_name': sess['name'],
            'points':       pts.tolist(),
            'colors':       cols.tolist(),
            'point_count':  len(pts),
            'frame_count':  len(sess['frames']),
            'method':       'COLMAP SfM + Dense',
            'has_stl':      stl_path is not None
        }
        with open(os.path.join(base, 'result.json'), 'w') as f:
            json.dump(result, f)

        sess['status'] = 'done'
        sess['points'] = len(pts)
        _update(sid, 100, f'Concluído! {len(pts):,} pontos 3D gerados.')
        socketio.emit('scan_done', {'session_id': sid, 'point_count': len(pts)})

    except Exception as e:
        sess['status'] = 'error'
        sess['error']  = str(e)
        _update(sid, 0, 'Erro: ' + str(e)[:100])
        socketio.emit('scan_error', {'session_id': sid, 'error': str(e)})

def _densify(pts, cols, factor=3):
    """Densifica a nuvem interpolando entre vizinhos próximos"""
    if len(pts) < 4:
        return pts, cols
    try:
        k   = min(5, len(pts)-1)
        nbrs = NearestNeighbors(n_neighbors=k).fit(pts)
        _, idxs = nbrs.kneighbors(pts)
        new_pts, new_cols = [pts], [cols]
        for i in range(len(pts)):
            for j in idxs[i][1:3]:
                for t in [0.33, 0.67]:
                    np_ = pts[i]*(1-t) + pts[j]*t
                    nc_ = cols[i]*(1-t) + cols[j]*t
                    new_pts.append(np_.reshape(1,3))
                    new_cols.append(nc_.reshape(1,3))
        p = np.vstack(new_pts)
        c = np.vstack(new_cols)
        # Remove duplicatas
        uniq = np.unique(p.round(4), axis=0)
        nbrs2 = NearestNeighbors(n_neighbors=1).fit(pts)
        _, idx2 = nbrs2.kneighbors(uniq)
        c2 = cols[idx2.flatten()]
        return uniq, c2
    except:
        return pts, cols

def _make_mesh(pts, cols, base_dir):
    """Gera malha STL via Convex Hull + suavização"""
    if len(pts) < 4:
        return None
    hull = ConvexHull(pts)
    mesh = trimesh.Trimesh(vertices=pts[hull.vertices], faces=hull.simplices)
    trimesh.smoothing.filter_laplacian(mesh, iterations=5)
    path = os.path.join(base_dir, 'model.stl')
    mesh.export(path)
    return path

# ── STATUS ─────────────────────────────────────────────────────────────────────
@app.route('/api/scan/status/<sid>')
def scan_status(sid):
    if sid not in sessions:
        return jsonify({'error': 'Não encontrado'}), 404
    s = sessions[sid]
    return jsonify({
        'status':   s.get('status'),
        'progress': s.get('progress', 0),
        'msg':      s.get('msg', ''),
        'points':   s.get('points', 0),
        'error':    s.get('error')
    })

# ── RESULTADO ──────────────────────────────────────────────────────────────────
@app.route('/api/scan/result/<sid>')
def scan_result(sid):
    path = os.path.join(DATA_DIR, sid, 'result.json')
    if not os.path.exists(path):
        return jsonify({'error': 'Resultado não disponível'}), 404
    with open(path) as f:
        return jsonify(json.load(f))

# ── DOWNLOAD STL ───────────────────────────────────────────────────────────────
@app.route('/api/scan/stl/<sid>')
def download_stl(sid):
    path = os.path.join(DATA_DIR, sid, 'model.stl')
    if not os.path.exists(path):
        return jsonify({'error': 'STL não disponível'}), 404
    return send_file(path, as_attachment=True, download_name=f'scan_{sid}.stl')

# ── LISTA ──────────────────────────────────────────────────────────────────────
@app.route('/api/scans/list')
def list_scans():
    result = []
    for sid, s in sessions.items():
        if s.get('status') == 'done':
            result.append({
                'id':      sid,
                'name':    s.get('name'),
                'frames':  len(s.get('frames', [])),
                'points':  s.get('points', 0),
                'created': s.get('created')
            })
    return jsonify(sorted(result, key=lambda x: x['created'], reverse=True))

# ── WEBSOCKET ──────────────────────────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    emit('connected', {'status': 'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"OdontoScan 3D — Porta {port} — Railway: {IS_RAILWAY}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
