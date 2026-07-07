import eventlet
eventlet.monkey_patch()
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import os, uuid, json, base64, subprocess, shutil, threading, time
import sqlite3
from contextlib import contextmanager
import numpy as np
import cv2
from PIL import Image
import io
import trimesh
from scipy.spatial import ConvexHull
from sklearn.neighbors import NearestNeighbors

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

# Timeout de segurança: se uma sessão ficar "processing" sem atualização
# por mais que isso (em segundos), é marcada como erro automaticamente.
PROCESSING_WATCHDOG_SECONDS = 20 * 60  # 20 minutos

# ── SESSÕES PERSISTENTES (SQLite) ───────────────────────────────────────────────
# Guardamos o estado das sessões em disco (no volume persistente do Railway)
# para sobreviver a reinícios do container. Isso evita que o frontend fique
# "preso" esperando um processo que já morreu.
SESSIONS_DB = os.path.join(DATA_DIR, 'sessions.db')


def init_sessions_db():
    conn = sqlite3.connect(SESSIONS_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            sid TEXT PRIMARY KEY,
            name TEXT,
            frame_count INTEGER DEFAULT 0,
            status TEXT,
            progress INTEGER DEFAULT 0,
            msg TEXT,
            created REAL,
            updated REAL,
            points INTEGER DEFAULT 0,
            error TEXT
        )
    ''')
    conn.commit()
    conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(SESSIONS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


init_sessions_db()

# Cache leve em memória: sid -> lista de paths de frames.
# Os arquivos de imagem já ficam salvos em disco, então isso é só um
# atalho para contar frames rapidamente sem consultar o disco toda vez.
# Se o container reiniciar no meio de uma captura, essa lista é reconstruída
# a partir da pasta de imagens (veja _rebuild_frame_cache).
sessions = {}


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
    name = data.get('name', f'Scan_{sid}')
    now  = time.time()

    with get_db() as db:
        db.execute(
            'INSERT INTO sessions (sid, name, frame_count, status, progress, msg, created, updated, points) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (sid, name, 0, 'capturing', 0, 'Aguardando frames...', now, now, 0)
        )

    sessions[sid] = []
    os.makedirs(os.path.join(DATA_DIR, sid, 'images'), exist_ok=True)
    return jsonify({'session_id': sid, 'status': 'ok'})

# ── UPLOAD DE FRAME ─────────────────────────────────────────────────────────────
@app.route('/api/frame/upload', methods=['POST'])
def upload_frame():
    data = request.json
    sid  = data.get('session_id')
    if not sid:
        return jsonify({'error': 'Sessão inválida'}), 400

    if sid not in sessions:
        # Container pode ter reiniciado; tenta recuperar do disco/DB
        with get_db() as db:
            row = db.execute('SELECT sid FROM sessions WHERE sid=?', (sid,)).fetchone()
        if not row:
            return jsonify({'error': 'Sessão inválida'}), 400
        _rebuild_frame_cache(sid)

    img_b64   = data['frame'].split(',')[1]
    img_bytes = base64.b64decode(img_b64)
    arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is None:
        return jsonify({'error': 'Frame inválido'}), 400

    idx      = len(sessions[sid])
    img_path = os.path.join(DATA_DIR, sid, 'images', f'{idx:04d}.jpg')
    cv2.imwrite(img_path, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    sessions[sid].append(img_path)

    # Keypoints para feedback
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    orb  = cv2.ORB_create(500)
    kp, _ = orb.detectAndCompute(gray, None)

    with get_db() as db:
        db.execute('UPDATE sessions SET frame_count=?, updated=? WHERE sid=?',
                   (idx + 1, time.time(), sid))

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
    if not sid:
        return jsonify({'error': 'Sessão inválida'}), 400

    if sid not in sessions:
        with get_db() as db:
            row = db.execute('SELECT sid FROM sessions WHERE sid=?', (sid,)).fetchone()
        if not row:
            return jsonify({'error': 'Sessão inválida'}), 400
        _rebuild_frame_cache(sid)

    frame_count = len(sessions[sid])
    if frame_count < 6:
        return jsonify({'error': f'Mínimo 6 frames. Você tem {frame_count}.'}), 400

    with get_db() as db:
        db.execute('UPDATE sessions SET status=?, updated=? WHERE sid=?',
                   ('processing', time.time(), sid))

    t = threading.Thread(target=_run_reconstruction, args=(sid,))
    t.daemon = True
    t.start()
    return jsonify({'status': 'processing'})

def _update(sid, progress, msg, status=None):
    """Atualiza progresso no banco (persistente) e via WebSocket."""
    with get_db() as db:
        if status:
            db.execute('UPDATE sessions SET progress=?, msg=?, status=?, updated=? WHERE sid=?',
                       (progress, msg, status, time.time(), sid))
        else:
            db.execute('UPDATE sessions SET progress=?, msg=?, updated=? WHERE sid=?',
                       (progress, msg, time.time(), sid))
    socketio.emit('progress', {'session_id': sid, 'progress': progress, 'msg': msg})

def _get_session_name(sid):
    with get_db() as db:
        row = db.execute('SELECT name FROM sessions WHERE sid=?', (sid,)).fetchone()
    return row['name'] if row else sid

def _run_reconstruction(sid):
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
        _update(sid, 80, 'Removendo outliers estatisticamente...')
        pts, cols = _remove_outliers(pts, cols)

        # ── PASSO 7: Densifica nuvem (método simples, sempre roda) ──
        _update(sid, 85, 'Densificando nuvem de pontos...')
        pts, cols = _densify(pts, cols)

        # ── PASSO 8: Normaliza ──
        center = pts.mean(axis=0)
        pts   -= center
        scale  = np.percentile(np.abs(pts), 95)
        if scale > 0:
            pts /= scale

        # ── PASSO 9: Gera malha 3D (Poisson via Open3D, com fallback) ──
        _update(sid, 92, 'Reconstruindo superfície com Poisson...')
        stl_path = None
        method_used = 'Convex Hull (fallback)'
        if HAS_OPEN3D:
            try:
                stl_path = _make_mesh_poisson(pts, cols, base)
                method_used = 'COLMAP SfM + Open3D Poisson'
            except Exception:
                stl_path = None
        if stl_path is None:
            try:
                stl_path = _make_mesh(pts, cols, base)
                method_used = 'COLMAP SfM + Convex Hull (fallback)'
            except Exception:
                stl_path = None

        # ── PASSO 10: Salva resultado ──
        _update(sid, 97, 'Salvando resultado...')
        frame_count = len(sessions.get(sid, []))
        result = {
            'session_name': _get_session_name(sid),
            'points':       pts.tolist(),
            'colors':       cols.tolist(),
            'point_count':  len(pts),
            'frame_count':  frame_count,
            'method':       method_used,
            'has_stl':      stl_path is not None
        }
        with open(os.path.join(base, 'result.json'), 'w') as f:
            json.dump(result, f)

        with get_db() as db:
            db.execute('UPDATE sessions SET points=? WHERE sid=?', (len(pts), sid))
        _update(sid, 100, f'Concluído! {len(pts):,} pontos 3D gerados.', status='done')
        socketio.emit('scan_done', {'session_id': sid, 'point_count': len(pts)})

    except Exception as e:
        with get_db() as db:
            db.execute('UPDATE sessions SET error=? WHERE sid=?', (str(e), sid))
        _update(sid, 0, 'Erro: ' + str(e)[:100], status='error')
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

def _remove_outliers(pts, cols):
    """
    Remove outliers da nuvem de pontos.
    Usa remoção estatística do Open3D quando disponível (muito mais precisa,
    pois olha a densidade local de vizinhos em vez de só a distância ao
    centro), com fallback pro método antigo por desvio-padrão.
    """
    if HAS_OPEN3D and len(pts) >= 20:
        try:
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(pts)
            pcd.colors = o3d.utility.Vector3dVector(np.clip(cols, 0, 1))
            pcd_clean, ind = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
            clean_pts = np.asarray(pcd_clean.points)
            clean_cols = np.asarray(pcd_clean.colors)
            if len(clean_pts) >= 6:
                return clean_pts, clean_cols
        except Exception:
            pass

    # Fallback: filtro simples por distância ao centro
    center = pts.mean(axis=0)
    dists  = np.linalg.norm(pts - center, axis=1)
    mask   = dists < (dists.mean() + 2.5 * dists.std())
    return pts[mask], cols[mask]


def _make_mesh_poisson(pts, cols, base_dir, depth=9):
    """
    Reconstrói a superfície 3D com Poisson Surface Reconstruction (Open3D).
    Diferente do Convex Hull, respeita reentrâncias e cavidades reais
    (espaço entre dentes, sulcos), gerando um modelo muito mais fiel.
    """
    if len(pts) < 20:
        return None

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    pcd.colors = o3d.utility.Vector3dVector(np.clip(cols, 0, 1))

    # Estima normais (obrigatório para o Poisson funcionar bem)
    pcd.estimate_normals(
        search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.15, max_nn=30)
    )
    pcd.orient_normals_consistent_tangent_plane(k=30)

    # Reconstrução Poisson
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=depth
    )

    # Remove os "balões" de baixa densidade que o Poisson costuma criar
    # nas bordas onde há poucos dados de suporte
    densities = np.asarray(densities)
    threshold = np.quantile(densities, 0.02)
    verts_to_remove = densities < threshold
    mesh.remove_vertices_by_mask(verts_to_remove)

    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_duplicated_vertices()
    mesh.remove_non_manifold_edges()
    mesh.compute_vertex_normals()

    if len(mesh.vertices) < 4:
        return None

    path = os.path.join(base_dir, 'model.stl')
    o3d.io.write_triangle_mesh(path, mesh)
    return path


def _make_mesh(pts, cols, base_dir):
    """Gera malha STL via Convex Hull + suavização (fallback do Poisson)"""
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
    with get_db() as db:
        row = db.execute('SELECT * FROM sessions WHERE sid=?', (sid,)).fetchone()

    if not row:
        return jsonify({'error': 'Não encontrado'}), 404

    # Watchdog: se está "processing" há muito tempo sem nenhuma atualização,
    # provavelmente o processo morreu (ex: restart do container) — marca erro
    # em vez de deixar o frontend preso pra sempre.
    if row['status'] == 'processing' and (time.time() - row['updated']) > PROCESSING_WATCHDOG_SECONDS:
        with get_db() as db:
            db.execute('UPDATE sessions SET status=?, error=?, updated=? WHERE sid=?',
                       ('error', 'Timeout: processamento não respondeu a tempo. Tente novamente.',
                        time.time(), sid))
        return jsonify({
            'status': 'error', 'progress': 0, 'msg': '',
            'points': 0, 'error': 'Timeout: processamento travou'
        })

    return jsonify({
        'status':   row['status'],
        'progress': row['progress'],
        'msg':      row['msg'],
        'points':   row['points'],
        'error':    row['error']
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
    with get_db() as db:
        rows = db.execute(
            "SELECT sid, name, frame_count, points, created FROM sessions "
            "WHERE status='done' ORDER BY created DESC"
        ).fetchall()

    result = [{
        'id':      row['sid'],
        'name':    row['name'],
        'frames':  row['frame_count'],
        'points':  row['points'],
        'created': row['created']
    } for row in rows]

    return jsonify(result)

# ── WEBSOCKET ──────────────────────────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    emit('connected', {'status': 'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"OdontoScan 3D — Porta {port} — Railway: {IS_RAILWAY}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
