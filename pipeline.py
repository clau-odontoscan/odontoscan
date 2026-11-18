"""
OdontoScan 3D — pipeline de reconstrução
COLMAP (SfM) -> filtragem -> escala por marcador ArUco -> malha Poisson (mesh_worker.py)
"""
import os, sys, json, shutil, subprocess, glob
import numpy as np
import cv2

# ── CONFIG ─────────────────────────────────────────────────────────────────────
MAX_THREADS   = int(os.environ.get('MAX_THREADS', min(4, os.cpu_count() or 2)))
MAX_FEATURES  = int(os.environ.get('MAX_FEATURES', 4096))
MATCH_OVERLAP = int(os.environ.get('MATCH_OVERLAP', 12))
MESH_TIMEOUT  = int(os.environ.get('MESH_TIMEOUT', 900))
ARUCO_DICT    = cv2.aruco.DICT_4X4_50
ARUCO_ID      = 0
HERE          = os.path.dirname(os.path.abspath(__file__))


def find_colmap():
    """Procura o executável do COLMAP: variável COLMAP_PATH, PATH, ou pasta bin ao lado / na Área de Trabalho."""
    env = os.environ.get('COLMAP_PATH')
    if env and os.path.exists(env):
        return env
    w = shutil.which('colmap') or shutil.which('colmap.exe') or shutil.which('COLMAP.bat')
    if w:
        return w
    candidates = [
        os.path.join(HERE, 'bin', 'colmap.exe'),
        os.path.join(HERE, 'colmap', 'COLMAP.bat'),
        os.path.join(HERE, 'colmap', 'bin', 'colmap.exe'),
        os.path.join(os.path.expanduser('~'), 'Desktop', 'bin', 'colmap.exe'),
        os.path.join(os.path.expanduser('~'), 'Desktop', 'colmap', 'COLMAP.bat'),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return env or 'colmap'


COLMAP = find_colmap()


def thread_env():
    """Limita threads de OpenMP/BLAS (causa dos timeouts no Railway) e roda Qt sem tela."""
    env = os.environ.copy()
    n = str(MAX_THREADS)
    for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
              'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        env[k] = n
    env['QT_QPA_PLATFORM'] = 'offscreen'
    return env


# ── DETECÇÃO DAS OPÇÕES DO COLMAP (nomes mudam entre versões) ─────────────────
_help_cache = {}

def _help(cmd):
    if cmd not in _help_cache:
        try:
            r = subprocess.run([COLMAP, cmd, '--help'], capture_output=True, text=True,
                               timeout=60, env=thread_env())
            _help_cache[cmd] = (r.stdout or '') + (r.stderr or '')
        except Exception:
            _help_cache[cmd] = ''
    return _help_cache[cmd]


def _opt(cmd, candidates, value):
    """Retorna ['--Opcao', valor] para a primeira opção que existe nesta versão do COLMAP."""
    h = _help(cmd)
    for c in candidates:
        if f'--{c} ' in h or f'--{c}\n' in h:
            return [f'--{c}', str(value)]
    return []


def colmap_info():
    try:
        r = subprocess.run([COLMAP, 'help'], capture_output=True, text=True, timeout=30, env=thread_env())
        first = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
        return {'path': COLMAP, 'ok': True, 'version': first[0] if first else '?'}
    except Exception as e:
        return {'path': COLMAP, 'ok': False, 'version': None, 'error': str(e)}


# ── LEITURA DO MODELO TXT ─────────────────────────────────────────────────────
def qvec2rotmat(q):
    w, x, y, z = q
    return np.array([
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*w*z,     2*x*z + 2*w*y],
        [2*x*y + 2*w*z,     1 - 2*x*x - 2*z*z, 2*y*z - 2*w*x],
        [2*x*z - 2*w*y,     2*y*z + 2*w*x,     1 - 2*x*x - 2*y*y]])


def read_cameras(path):
    cams = {}
    with open(path) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            p = line.split()
            cams[int(p[0])] = {'model': p[1], 'w': int(p[2]), 'h': int(p[3]),
                               'params': [float(v) for v in p[4:]]}
    return cams


def read_images(path):
    imgs = {}
    with open(path) as f:
        lines = [l for l in f if not l.startswith('#')]
    for i in range(0, len(lines), 2):
        p = lines[i].split()
        if len(p) < 10:
            continue
        q = [float(v) for v in p[1:5]]
        t = np.array([float(v) for v in p[5:8]])
        R = qvec2rotmat(q)
        imgs[p[9]] = {'R': R, 't': t, 'cam': int(p[8]), 'center': -R.T @ t}
    return imgs


def read_points(path):
    pts, cols, errs, tracks = [], [], [], []
    with open(path) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            p = line.split()
            if len(p) < 8:
                continue
            pts.append([float(p[1]), float(p[2]), float(p[3])])
            cols.append([int(p[4]) / 255, int(p[5]) / 255, int(p[6]) / 255])
            errs.append(float(p[7]))
            tracks.append((len(p) - 8) // 2)
    return (np.array(pts, float).reshape(-1, 3), np.array(cols, float).reshape(-1, 3),
            np.array(errs, float), np.array(tracks, int))


def count_points(txt_dir):
    p = os.path.join(txt_dir, 'points3D.txt')
    if not os.path.exists(p):
        return 0
    with open(p) as f:
        return sum(1 for l in f if l.strip() and not l.startswith('#'))


# ── ESCALA PELO MARCADOR ARUCO ────────────────────────────────────────────────
def _K_dist(cam):
    m, p = cam['model'], cam['params']
    if m == 'SIMPLE_PINHOLE':
        f, cx, cy = p[:3]; K = [[f, 0, cx], [0, f, cy], [0, 0, 1]]; d = [0, 0, 0, 0]
    elif m == 'PINHOLE':
        fx, fy, cx, cy = p[:4]; K = [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]; d = [0, 0, 0, 0]
    elif m == 'SIMPLE_RADIAL':
        f, cx, cy, k = p[:4]; K = [[f, 0, cx], [0, f, cy], [0, 0, 1]]; d = [k, 0, 0, 0]
    elif m == 'RADIAL':
        f, cx, cy, k1, k2 = p[:5]; K = [[f, 0, cx], [0, f, cy], [0, 0, 1]]; d = [k1, k2, 0, 0]
    elif m in ('OPENCV', 'FULL_OPENCV'):
        fx, fy, cx, cy, k1, k2, p1, p2 = p[:8]; K = [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]; d = [k1, k2, p1, p2]
    else:
        return None, None
    return np.array(K, float), np.array(d, float)


def _triangulate(obs):
    """obs = lista de (R, t, xy_normalizado). DLT."""
    A = []
    for R, t, (u, v) in obs:
        P = np.hstack([R, t.reshape(3, 1)])
        A.append(u * P[2] - P[0])
        A.append(v * P[2] - P[1])
    _, _, Vt = np.linalg.svd(np.array(A))
    X = Vt[-1]
    return X[:3] / X[3]


def detect_scale(img_dir, cams, imgs, marker_mm):
    """Triangula os 4 cantos do marcador ArUco e devolve fator (mm por unidade do modelo)."""
    if not marker_mm or marker_mm <= 0:
        return None, 'sem tamanho de marcador'
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICT),
                                  cv2.aruco.DetectorParameters())
    corners_obs = [[] for _ in range(4)]
    views = 0
    for name, im in imgs.items():
        g = cv2.imread(os.path.join(img_dir, name), cv2.IMREAD_GRAYSCALE)
        if g is None:
            continue
        corners, ids, _ = det.detectMarkers(g)
        if ids is None:
            continue
        for c, i in zip(corners, ids.flatten()):
            if i != ARUCO_ID:
                continue
            K, d = _K_dist(cams[im['cam']])
            if K is None:
                continue
            und = cv2.undistortPoints(c.reshape(-1, 1, 2).astype(np.float64), K, d).reshape(-1, 2)
            for k in range(4):
                corners_obs[k].append((im['R'], im['t'], und[k]))
            views += 1
            break
    if views < 3:
        return None, f'marcador visto em {views} frame(s) (mínimo 3)'
    P = np.array([_triangulate(o) for o in corners_obs])
    sides = np.array([np.linalg.norm(P[i] - P[(i + 1) % 4]) for i in range(4)])
    diag = (np.linalg.norm(P[0] - P[2]) + np.linalg.norm(P[1] - P[3])) / 2
    if sides.mean() <= 0 or sides.std() / sides.mean() > 0.12 or abs(diag / sides.mean() - np.sqrt(2)) > 0.15:
        return None, 'marcador detectado mas geometria inconsistente'
    return float(marker_mm / np.median(sides)), f'marcador em {views} frames'


# ── FILTROS ───────────────────────────────────────────────────────────────────
def filter_points(pts, cols, errs, tracks):
    if len(pts) == 0:
        return pts, cols
    keep = tracks >= 2
    med = np.median(errs[keep]) if keep.any() else 1.0
    keep &= errs <= max(2.0, 3 * med)
    pts, cols = pts[keep], cols[keep]
    # outlier estatístico: distância média aos k vizinhos
    if len(pts) > 20:
        from scipy.spatial import cKDTree
        k = min(12, len(pts) - 1)
        d, _ = cKDTree(pts).query(pts, k=k + 1)
        md = d[:, 1:].mean(axis=1)
        m = md < md.mean() + 2.0 * md.std()
        pts, cols = pts[m], cols[m]
    return pts, cols


# ── PIPELINE PRINCIPAL ────────────────────────────────────────────────────────
class PipelineError(Exception):
    pass


def _run(args, timeout, label):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=thread_env())
    except subprocess.TimeoutExpired:
        raise PipelineError(f'{label}: tempo esgotado ({timeout}s). Use menos frames.')
    except FileNotFoundError:
        raise PipelineError(f'COLMAP não encontrado em "{COLMAP}". Defina COLMAP_PATH.')
    if r.returncode != 0:
        tail = ((r.stderr or '') + (r.stdout or ''))[-500:]
        raise PipelineError(f'{label} falhou: {tail}')
    return r


def reconstruct(base, frame_count, marker_mm, update):
    """Executa tudo. update(progresso, mensagem). Retorna dict com o resultado."""
    img_dir    = os.path.join(base, 'images')
    db_path    = os.path.join(base, 'database.db')
    sparse_dir = os.path.join(base, 'sparse')
    txt_root   = os.path.join(base, 'sparse_txt')

    # limpa execução anterior (reprocessar)
    for p in (db_path, db_path + '-shm', db_path + '-wal'):
        if os.path.exists(p):
            os.remove(p)
    for d in (sparse_dir, txt_root, os.path.join(base, 'mesh')):
        shutil.rmtree(d, ignore_errors=True)
    for f in ('result.json', 'model.stl', 'mesh.ply'):
        p = os.path.join(base, f)
        if os.path.exists(p):
            os.remove(p)
    os.makedirs(sparse_dir, exist_ok=True)
    os.makedirs(txt_root, exist_ok=True)

    # 1. features
    update(8, 'Extraindo pontos de interesse...')
    fe = 'feature_extractor'
    _run([COLMAP, fe, '--database_path', db_path, '--image_path', img_dir,
          '--ImageReader.single_camera', '1', '--ImageReader.camera_model', 'SIMPLE_RADIAL']
         + _opt(fe, ['SiftExtraction.use_gpu', 'FeatureExtraction.use_gpu'], 0)
         + _opt(fe, ['SiftExtraction.num_threads', 'FeatureExtraction.num_threads'], MAX_THREADS)
         + _opt(fe, ['SiftExtraction.max_num_features'], MAX_FEATURES)
         + _opt(fe, ['SiftExtraction.max_image_size', 'FeatureExtraction.max_image_size'], 1600),
         600, 'Extração de features')

    # 2. matching sequencial (frames vêm em ordem; quadrático pega mais pares sem custo exaustivo)
    update(28, 'Calculando correspondências entre frames...')
    sm = 'sequential_matcher'
    _run([COLMAP, sm, '--database_path', db_path]
         + _opt(sm, ['SiftMatching.use_gpu', 'FeatureMatching.use_gpu'], 0)
         + _opt(sm, ['SiftMatching.num_threads', 'FeatureMatching.num_threads'], MAX_THREADS)
         + _opt(sm, ['SequentialMatching.overlap'], MATCH_OVERLAP)
         + _opt(sm, ['SequentialMatching.quadratic_overlap'], 1),
         900, 'Matching')

    # 3. mapper
    update(48, 'Reconstruindo geometria 3D...')
    mp = 'mapper'
    _run([COLMAP, mp, '--database_path', db_path, '--image_path', img_dir, '--output_path', sparse_dir]
         + _opt(mp, ['Mapper.num_threads'], MAX_THREADS),
         1500, 'Reconstrução (mapper)')

    # 4. escolhe o MAIOR modelo
    update(62, 'Exportando modelo...')
    best, best_n = None, 0
    for d in sorted(os.listdir(sparse_dir)):
        src = os.path.join(sparse_dir, d)
        if not os.path.isdir(src):
            continue
        out = os.path.join(txt_root, d)
        os.makedirs(out, exist_ok=True)
        _run([COLMAP, 'model_converter', '--input_path', src, '--output_path', out,
              '--output_type', 'TXT'], 180, 'Conversão do modelo')
        n = count_points(out)
        if n > best_n:
            best, best_n = out, n
    if not best:
        raise PipelineError('Nenhum modelo gerado. Use mais frames, gire mais devagar e melhore a iluminação.')

    cams = read_cameras(os.path.join(best, 'cameras.txt'))
    imgs = read_images(os.path.join(best, 'images.txt'))
    pts, cols, errs, tracks = read_points(os.path.join(best, 'points3D.txt'))

    # 5. filtra
    update(70, 'Filtrando ruído...')
    pts, cols = filter_points(pts, cols, errs, tracks)
    if len(pts) < 50:
        raise PipelineError(f'Poucos pontos confiáveis ({len(pts)}). Use mais frames com boa iluminação e textura.')

    # 6. escala
    update(76, 'Procurando marcador de escala...')
    scale, scale_msg = detect_scale(img_dir, cams, imgs, marker_mm)
    centers = np.array([im['center'] for im in imgs.values()])
    if scale:
        pts = pts * scale
        centers = centers * scale
    units = 'mm' if scale else 'arbitrária'

    # alinhamento: centraliza e põe o eixo de menor variação para cima (Y), do lado das câmeras
    pts, centers = align(pts, centers)

    # 7. malha (processo separado, threads limitadas)
    update(82, 'Gerando malha 3D (Poisson)...')
    mesh_info = run_mesh_worker(base, pts, cols, centers, bool(scale))

    # 8. resultado para o visualizador
    update(96, 'Salvando resultado...')
    view = pts
    vcols = cols
    if len(view) > 150000:
        idx = np.random.default_rng(0).choice(len(view), 150000, replace=False)
        view, vcols = view[idx], vcols[idx]
    ext = (pts.max(axis=0) - pts.min(axis=0)).tolist()
    result = {
        'points': np.round(view, 4).tolist(),
        'colors': np.round(vcols, 3).tolist(),
        'point_count': int(len(pts)),
        'frame_count': int(frame_count),
        'registered': int(len(imgs)),
        'units': units,
        'scale_msg': scale_msg,
        'size': mesh_info.get('size') or [round(v, 2) for v in ext],
        'method': 'COLMAP SfM + Poisson',
        'has_mesh': mesh_info.get('ok', False),
        'mesh_msg': mesh_info.get('msg', ''),
        'triangles': mesh_info.get('triangles', 0),
    }
    with open(os.path.join(base, 'result.json'), 'w') as f:
        json.dump(result, f)
    return result


def align(pts, centers):
    c = np.median(pts, axis=0)
    X = pts - c
    _, _, Vt = np.linalg.svd(X - X.mean(0), full_matrices=False)
    e1, e2, e3 = Vt[0], Vt[1], Vt[2]                 # maior, média, menor variação
    if np.mean((centers - c) @ e3) < 0:              # "cima" = lado onde estavam as câmeras
        e3 = -e3
    e2 = np.cross(e3, e1)                            # garante base destra
    R = np.stack([e1, e3, -e2])                      # X=e1, Y=e3 (cima), Z completa a base
    if np.linalg.det(R) < 0:
        R[2] = -R[2]
    P = X @ R.T
    P[:, 1] -= np.percentile(P[:, 1], 1)             # apoia o modelo em Y≈0
    P -= np.array([np.median(P[:, 0]), 0, np.median(P[:, 2])])
    P[:, 1] -= (P[:, 1].max() / 2)                   # centro vertical
    Cn = (centers - c) @ R.T
    shift = (X @ R.T) - P
    return P, Cn - shift[0]


def run_mesh_worker(base, pts, cols, centers, metric):
    npz = os.path.join(base, 'cloud.npz')
    np.savez(npz, pts=pts, cols=cols, centers=centers)
    out_json = os.path.join(base, 'mesh_info.json')
    if os.path.exists(out_json):
        os.remove(out_json)
    try:
        r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'mesh_worker.py'), npz, base,
                            '1' if metric else '0'],
                           capture_output=True, text=True, timeout=MESH_TIMEOUT, env=thread_env())
    except subprocess.TimeoutExpired:
        return {'ok': False, 'msg': f'malha: tempo esgotado ({MESH_TIMEOUT}s)'}
    if os.path.exists(out_json):
        with open(out_json) as f:
            return json.load(f)
    return {'ok': False, 'msg': 'malha falhou: ' + ((r.stderr or '') + (r.stdout or ''))[-300:]}


# ── MARCADOR PARA IMPRESSÃO ───────────────────────────────────────────────────
def marker_png(mm=20.0, dpi=300):
    from PIL import Image, ImageDraw
    px_per_mm = dpi / 25.4
    side = int(round(mm * px_per_mm))
    m = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), ARUCO_ID, side)
    quiet = int(round(8 * px_per_mm))
    W = side + 2 * quiet
    H = side + 2 * quiet + int(14 * px_per_mm)
    img = Image.new('L', (W, H), 255)
    img.paste(Image.fromarray(m), (quiet, quiet))
    d = ImageDraw.Draw(img)
    d.text((quiet, side + quiet + int(3 * px_per_mm)),
           f'OdontoScan - quadrado preto = {mm:g} mm. Imprimir em 100% (sem ajustar a pagina).', fill=0)
    import io
    buf = io.BytesIO()
    img.save(buf, 'PNG', dpi=(dpi, dpi))
    buf.seek(0)
    return buf
