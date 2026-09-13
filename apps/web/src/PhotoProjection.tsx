import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { PhotoPose } from './types';

/** A real, depth-tested CAD mesh projection; never deforms the reference photo. */
export function PhotoProjection({ url, pose, width, height }: {url:string; pose:PhotoPose; width:number; height:number}) {
  const host = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState('加载实体投影…');
  useEffect(() => {
    if (!host.current) return;
    const container = host.current;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({alpha:true,antialias:true,preserveDrawingBuffer:true}); }
    catch { setStatus('实体投影无法启动，可切换正面轮廓查看。'); return; }
    let disposed = false;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
    renderer.setSize(width,height);
    renderer.domElement.style.width = '100%'; renderer.domElement.style.height = '100%';
    container.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const d = pose.distance_radii;
    const camera = d >= 1e5
      ? new THREE.OrthographicCamera(-pose.cx/pose.scale_px,(width-pose.cx)/pose.scale_px,
          pose.cy/pose.scale_px,-(height-pose.cy)/pose.scale_px,.01,20)
      : new THREE.PerspectiveCamera(2*Math.atan(height/(2*pose.scale_px*d))*180/Math.PI,
          width/height,Math.max(.01,d-4),d+4);
    camera.position.set(0,0,d>=1e5?10:d); camera.lookAt(0,0,0);
    if (camera instanceof THREE.PerspectiveCamera) {
      camera.projectionMatrix.elements[8] = 1-2*pose.cx/width;
      camera.projectionMatrix.elements[9] = 2*pose.cy/height-1;
      camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    }
    const rotated = new THREE.Group(); const r = pose.rotation;
    rotated.matrix.set(r[0][0],r[0][1],r[0][2],0,r[1][0],r[1][1],r[1][2],0,
      r[2][0],r[2][1],r[2][2],0,0,0,0,1);
    rotated.matrixAutoUpdate = false;
    const normalized = new THREE.Group(); normalized.scale.setScalar(1/pose.radius_mm);
    normalized.position.z = -pose.reference_z_mm/pose.radius_mm;
    rotated.add(normalized); scene.add(rotated);
    const dispose = (object:THREE.Object3D) => object.traverse(child => {
      if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) {
        child.geometry.dispose();
        (Array.isArray(child.material) ? child.material : [child.material]).forEach(m=>m.dispose());
      }
    });
    let model:THREE.Group|null = null;
    setStatus('加载实体投影…');
    new GLTFLoader().load(url,gltf => {
      if (disposed) { dispose(gltf.scene); return; }
      model = gltf.scene;
      // Restore CAD Z-up exactly as the main viewer; coordinates remain in mm.
      model.rotation.x += Math.PI/2;
      model.traverse(child => {
        if (child instanceof THREE.Mesh) {
          (Array.isArray(child.material) ? child.material : [child.material]).forEach(m=>m.dispose());
          child.material = new THREE.MeshBasicMaterial({color:0xe8aa50,side:THREE.DoubleSide});
        }
      });
      normalized.add(model); renderer.render(scene,camera); setStatus('');
    },undefined,()=> { if (!disposed) setStatus('实体投影加载失败，可切换正面轮廓查看。'); });
    return () => { disposed=true; if(model)dispose(model); renderer.dispose(); renderer.domElement.remove(); };
  },[url,pose,width,height]);
  return <div className="photo-projection" style={{width:'100%',height:'100%',pointerEvents:'none'}}>
    <div ref={host} style={{width:'100%',height:'100%'}}/>
    {status && <span role="status">{status}</span>}
  </div>;
}
