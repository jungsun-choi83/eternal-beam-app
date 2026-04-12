import numpy as np
import open3d as o3d
from PIL import Image


def depth_to_point_cloud(depth: np.ndarray, rgb: Image.Image) -> o3d.geometry.PointCloud:
    rgb_np = np.array(rgb).astype(np.float32) / 255.0
    H, W = depth.shape

    fx = fy = max(H, W) * 1.2
    cx = W / 2.0
    cy = H / 2.0

    u, v = np.meshgrid(np.arange(W), np.arange(H))
    z = depth
    z_flat = z.flatten()
    u_flat = u.flatten()
    v_flat = v.flatten()

    mask = z_flat > 0
    z_flat = z_flat[mask]
    u_flat = u_flat[mask]
    v_flat = v_flat[mask]

    x = (u_flat - cx) * z_flat / fx
    y = (v_flat - cy) * z_flat / fy
    points = np.stack([x, -y, z_flat], axis=1)

    colors = rgb_np.reshape(-1, 3)[mask]

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    pcd.colors = o3d.utility.Vector3dVector(colors)
    return pcd


def point_cloud_to_mesh(pcd: o3d.geometry.PointCloud) -> o3d.geometry.TriangleMesh:
    pcd = pcd.voxel_down_sample(voxel_size=0.01)
    mesh, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=8)
    mesh = mesh.filter_smooth_simple(number_of_iterations=3)
    mesh.compute_vertex_normals()
    return mesh


def render_for_hologram(mesh: o3d.geometry.TriangleMesh, out_path: str):
    width, height = 1080, 1080
    renderer = o3d.visualization.rendering.OffscreenRenderer(width, height)

    mat = o3d.visualization.rendering.MaterialRecord()
    mat.shader = "defaultLit"

    scene = renderer.scene
    scene.set_background([0, 0, 0, 0])

    scene.add_geometry("mesh", mesh, mat)

    bbox = mesh.get_axis_aligned_bounding_box()
    center = bbox.get_center()
    cam_distance = 2.5
    cam_pos = center + np.array([cam_distance, cam_distance, cam_distance])
    up = [0, 1, 0]

    scene.camera.look_at(center, cam_pos, up)
    scene.camera.set_projection(60.0, width / height, 0.1, 100.0)

    img = renderer.render_to_image()
    o3d.io.write_image(out_path, img)