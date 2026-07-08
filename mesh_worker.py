"""
Worker de reconstrução de malha 3D — roda como PROCESSO SEPARADO (não thread).

Por que isso existe: o servidor principal (app.py) usa eventlet, que torna
threads "cooperativas" — ou seja, mesmo em threads diferentes, o código
compartilha o mesmo processo do sistema operacional. Quando o Open3D roda
o Poisson Surface Reconstruction (cálculo pesado em C++), ele pode travar
esse processo inteiro, e como tudo é cooperativo, até o mecanismo de
timeout do lado do servidor pode ficar preso junto.

Rodando esse cálculo aqui, como um processo Python totalmente separado
(via subprocess), o sistema operacional pode garantir um timeout de
verdade: se demorar demais, o processo principal manda um sinal de
encerramento (SIGKILL) e este processo morre de fato — sem travar nada.

Uso: python3 mesh_worker.py <entrada.npz> <pasta_saida> <saida.npz>
  entrada.npz  — contém arrays 'pts' (Nx3) e 'cols' (Nx3)
  pasta_saida  — pasta onde o model.stl deve ser salvo, se gerado
  saida.npz    — onde salvar os resultados finais (pts, cols, method, has_stl)
"""
import sys
import os
import numpy as np

try:
    import open3d as o3d
    HAS_OPEN3D = True
except Exception:
    HAS_OPEN3D = False

import trimesh
from scipy.spatial import ConvexHull
from sklearn.neighbors import NearestNeighbors


def remove_outliers(pts, cols):
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
    center = pts.mean(axis=0)
    dists = np.linalg.norm(pts - center, axis=1)
    mask = dists < (dists.mean() + 2.5 * dists.std())
    return pts[mask], cols[mask]


def densify(pts, cols):
    if len(pts) < 4:
        return pts, cols
    try:
        k = min(5, len(pts) - 1)
        nbrs = NearestNeighbors(n_neighbors=k).fit(pts)
        _, idxs = nbrs.kneighbors(pts)
        new_pts, new_cols = [pts], [cols]
        for i in range(len(pts)):
            for j in idxs[i][1:3]:
                for t in [0.33, 0.67]:
                    p_ = pts[i] * (1 - t) + pts[j] * t
                    c_ = cols[i] * (1 - t) + cols[j] * t
                    new_pts.append(p_.reshape(1, 3))
                    new_cols.append(c_.reshape(1, 3))
        p = np.vstack(new_pts)
        c = np.vstack(new_cols)
        uniq = np.unique(p.round(4), axis=0)
        nbrs2 = NearestNeighbors(n_neighbors=1).fit(pts)
        _, idx2 = nbrs2.kneighbors(uniq)
        c2 = cols[idx2.flatten()]
        return uniq, c2
    except Exception:
        return pts, cols


def make_mesh_poisson(pts, cols, base_dir, depth=9):
    if len(pts) < 20:
        return None
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    pcd.colors = o3d.utility.Vector3dVector(np.clip(cols, 0, 1))
    pcd.estimate_normals(
        search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.15, max_nn=30)
    )
    pcd.orient_normals_consistent_tangent_plane(k=30)
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=depth
    )
    densities = np.asarray(densities)
    threshold = np.quantile(densities, 0.02)
    mesh.remove_vertices_by_mask(densities < threshold)
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


def make_mesh_convexhull(pts, cols, base_dir):
    if len(pts) < 4:
        return None
    hull = ConvexHull(pts)
    mesh = trimesh.Trimesh(vertices=pts[hull.vertices], faces=hull.simplices)
    trimesh.smoothing.filter_laplacian(mesh, iterations=5)
    path = os.path.join(base_dir, 'model.stl')
    mesh.export(path)
    return path


def main():
    input_npz, base_dir, output_npz = sys.argv[1], sys.argv[2], sys.argv[3]
    data = np.load(input_npz)
    pts, cols = data['pts'], data['cols']

    # Remove outliers
    pts, cols = remove_outliers(pts, cols)

    # Densifica só se a nuvem não for grande demais
    if len(pts) <= 4000:
        pts, cols = densify(pts, cols)

    # Normaliza
    center = pts.mean(axis=0)
    pts = pts - center
    scale = np.percentile(np.abs(pts), 95)
    if scale > 0:
        pts = pts / scale

    stl_path = None
    method_used = 'Convex Hull (fallback)'
    if HAS_OPEN3D:
        try:
            stl_path = make_mesh_poisson(pts, cols, base_dir)
            method_used = 'COLMAP SfM + Open3D Poisson'
        except Exception:
            stl_path = None
    if stl_path is None:
        try:
            stl_path = make_mesh_convexhull(pts, cols, base_dir)
            method_used = 'COLMAP SfM + Convex Hull (fallback)'
        except Exception:
            stl_path = None

    np.savez(output_npz, pts=pts, cols=cols,
             method_used=method_used, has_stl=stl_path is not None)


if __name__ == '__main__':
    main()
