"""
mesh_worker.py — roda em processo separado (threads limitadas pelo pai).
Uso: python mesh_worker.py cloud.npz pasta_saida metric(0/1)
Gera mesh.ply (com cor, para o visualizador) e model.stl (para o laboratório).
"""
import sys, os, json
import numpy as np


def main():
    npz, out_dir, metric = sys.argv[1], sys.argv[2], sys.argv[3] == '1'
    info_path = os.path.join(out_dir, 'mesh_info.json')

    def done(info):
        with open(info_path, 'w') as f:
            json.dump(info, f)

    try:
        import open3d as o3d
    except Exception as e:
        return done({'ok': False, 'msg': f'Open3D não instalado ({e}). Só a nuvem de pontos foi gerada.'})
    from scipy.spatial import cKDTree
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)

    data = np.load(npz)
    pts, cols, centers = data['pts'], data['cols'], data['centers']
    if len(pts) < 100:
        return done({'ok': False, 'msg': f'poucos pontos para malha ({len(pts)})'})

    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
    pcd.colors = o3d.utility.Vector3dVector(np.clip(cols, 0, 1))
    diag = float(np.linalg.norm(pts.max(0) - pts.min(0)))

    # remove a mesa/papel (plano dominante), se for claramente dominante
    plane_removed = False
    try:
        plane, inl = pcd.segment_plane(distance_threshold=diag * 0.01, ransac_n=3, num_iterations=800)
        n, d0 = np.asarray(plane[:3]), plane[3]
        if len(inl) > 0.12 * len(pts):
            h = pts @ n + d0
            if np.median(np.abs(h)) > 0 and np.mean(centers @ n + d0) < 0:   # lado de cima = lado das câmeras
                h = -h
            spread = np.std(h[inl])
            thr = max(3.0 * spread, diag * 0.002)
            keep = np.where(h > thr)[0]                                      # tira mesa e o que está abaixo dela
            if len(keep) < 300:
                return done({'ok': False, 'msg': 'quase só a mesa/fundo foi reconstruída — o modelo tem pouca textura: '
                                                 'melhore a luz, risque com lápis ou use spray, e chegue mais perto'})
            pcd = pcd.select_by_index(keep)
            plane_removed = True
    except Exception:
        pass

    if len(pcd.points) > 250000:
        pcd = pcd.voxel_down_sample(diag / 400)

    # normais orientadas para a câmera mais próxima (as câmeras giraram em volta do objeto)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=diag * 0.04, max_nn=30))
    P = np.asarray(pcd.points)
    N = np.asarray(pcd.normals)
    _, ci = cKDTree(centers).query(P)
    flip = np.einsum('ij,ij->i', N, centers[ci] - P) < 0
    N[flip] *= -1
    pcd.normals = o3d.utility.Vector3dVector(N)

    depth = 9 if len(P) > 30000 else 8
    mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=depth, scale=1.1)
    dens = np.asarray(dens)
    mesh.remove_vertices_by_mask(dens < np.quantile(dens, 0.10))

    # recorta na caixa dos pontos (Poisson "infla" além dos dados)
    bb = pcd.get_axis_aligned_bounding_box()
    bb = bb.scale(1.04, bb.get_center())
    mesh = mesh.crop(bb)

    # mantém só o maior pedaço
    if len(mesh.triangles):
        tri_clusters, cluster_n, _ = mesh.cluster_connected_triangles()
        tri_clusters = np.asarray(tri_clusters)
        cluster_n = np.asarray(cluster_n)
        mesh.remove_triangles_by_mask(tri_clusters != int(cluster_n.argmax()))
        mesh.remove_unreferenced_vertices()

    if len(mesh.triangles) < 50:
        return done({'ok': False, 'msg': 'malha resultou vazia — capture mais frames'})

    mesh = mesh.filter_smooth_taubin(number_of_iterations=8)
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_vertices()
    mesh.compute_vertex_normals()
    mesh.compute_triangle_normals()

    o3d.io.write_triangle_mesh(os.path.join(out_dir, 'mesh.ply'), mesh, write_ascii=False)
    stl = o3d.geometry.TriangleMesh(mesh.vertices, mesh.triangles)
    stl.compute_triangle_normals()
    o3d.io.write_triangle_mesh(os.path.join(out_dir, 'model.stl'), stl)

    # medidas do objeto (sem a mesa): comprimento x largura no plano, altura em Y
    V = np.asarray(mesh.vertices)
    xz = V[:, [0, 2]] - V[:, [0, 2]].mean(0)
    _, _, vt = np.linalg.svd(xz, full_matrices=False)
    a = xz @ vt.T
    size = [float(np.ptp(a[:, 0])), float(np.ptp(V[:, 1])), float(np.ptp(a[:, 1]))]

    done({'ok': True, 'size': [round(v, 2) for v in size], 'triangles': int(len(mesh.triangles)), 'vertices': int(len(mesh.vertices)),
          'watertight': bool(mesh.is_watertight()), 'plane_removed': plane_removed,
          'msg': ('malha em mm' if metric else 'malha sem escala (sem marcador)')})


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        with open(os.path.join(sys.argv[2], 'mesh_info.json'), 'w') as f:
            json.dump({'ok': False, 'msg': f'malha falhou: {e}'}, f)
