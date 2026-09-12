import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { Box, Crosshair, Layers2, Rotate3D, ScanLine } from 'lucide-react';

type Runtime = { camera: THREE.PerspectiveCamera; controls: OrbitControls; size: number };

export function Viewer({ url, building }: { url: string | null; building: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const runtime = useRef<Runtime | null>(null);
  const [wireframe, setWireframe] = useState(false);
  const [section, setSection] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const options = useRef({ wireframe, section });
  options.current = { wireframe, section };

  useEffect(() => {
    if (!container.current) return;
    const host = container.current;
    let disposed = false;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setError('当前浏览器无法启动三维视图，仍可保存参数和导出模型。');
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.localClippingEnabled = true;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.6;
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.09;
    controls.minDistance = 0.3;
    controls.maxDistance = 6000;
    scene.add(new THREE.HemisphereLight(0xc7defa, 0x404a5e, 3));
    const key = new THREE.DirectionalLight(0xfff3dc, 4.2);
    key.position.set(2, 4, 5); scene.add(key);
    const fill = new THREE.DirectionalLight(0xa6cfff, 3);
    fill.position.set(-3, -1, 2); scene.add(fill);
    const back = new THREE.DirectionalLight(0xffffff, 3);
    back.position.set(1, 2, -4); scene.add(back);
    const clipping = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0);
    const materials: THREE.MeshStandardMaterial[] = [];
    let model: THREE.Group | null = null;
    let grid: THREE.GridHelper | null = null;
    const disposeModel = (object: THREE.Object3D) => object.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        child.geometry.dispose();
        (Array.isArray(child.material) ? child.material : [child.material]).forEach((m) => m.dispose());
      }
    });
    const fit = (size: number) => {
      camera.near = size / 1000; camera.far = size * 30;
      camera.position.set(size * 0.95, size * 0.5, size * 1.9);
      camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      controls.minDistance = size * 0.65; controls.maxDistance = size * 5;
      controls.update();
      runtime.current = { camera, controls, size };
    };
    fit(480);
    setError('');
    if (url) {
      setLoading(true);
      new GLTFLoader().load(url, (gltf) => {
        if (disposed) { disposeModel(gltf.scene); return; }
        model = gltf.scene;
        // CadQuery exports glTF in Y-up. Restore the CAD Z-axis for the front view.
        model.rotation.x += Math.PI / 2;
        model.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(model);
        const dimensions = bounds.getSize(new THREE.Vector3());
        const size = Math.max(dimensions.x, dimensions.y, dimensions.z);
        model.position.sub(bounds.getCenter(new THREE.Vector3()));
        model.traverse((child) => {
          if (child instanceof THREE.Mesh) {
            (Array.isArray(child.material) ? child.material : [child.material]).forEach((m) => m.dispose());
            const material = new THREE.MeshStandardMaterial({
              color: 0x9ca9bb, metalness: 0.55, roughness: 0.3, side: THREE.DoubleSide,
            });
            child.material = material;
            materials.push(material);
          }
        });
        scene.add(model);
        grid = new THREE.GridHelper(size * 2.3, 24, 0x3a4858, 0x25303d);
        grid.position.y = -dimensions.y / 2 - size * 0.025;
        scene.add(grid);
        fit(size); setLoading(false);
      }, undefined, () => {
        if (!disposed) { setError('模型预览加载失败。请重新载入，或下载 STEP 检查。'); setLoading(false); }
      });
    } else {
      setLoading(false);
    }
    const resize = () => {
      const width = host.clientWidth, height = host.clientHeight;
      renderer.setSize(width, height);
      camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize); observer.observe(host); resize();
    let frame = 0;
    const animate = () => {
      for (const material of materials) {
        material.wireframe = options.current.wireframe;
        material.clippingPlanes = options.current.section ? [clipping] : [];
      }
      controls.update(); renderer.render(scene, camera);
      frame = requestAnimationFrame(animate);
    };
    animate();
    return () => {
      disposed = true; cancelAnimationFrame(frame); observer.disconnect(); controls.dispose();
      if (model) disposeModel(model);
      if (grid) { grid.geometry.dispose(); (grid.material as THREE.Material).dispose(); }
      renderer.dispose(); renderer.domElement.remove(); runtime.current = null;
    };
  }, [url]);

  const view = (front: boolean) => {
    const current = runtime.current;
    if (!current) return;
    const { camera, controls, size } = current;
    camera.position.set(front ? 0 : size * .95, front ? 0 : size * .5, size * (front ? 2.15 : 1.9));
    camera.up.set(0, 1, 0); controls.target.set(0, 0, 0); controls.update();
  };

  return <div className="viewer">
    <div className="viewport-label"><span className="live-dot"/> {url ? '实体预览' : '建模空间'} <span>· mm</span></div>
    <div className="canvas" ref={container} aria-label="轮毂三维模型，可拖动旋转、滚轮缩放"/>
    {!url && <div className="viewer-empty"><Box size={48} strokeWidth={1}/><h2>从第一版轮毂开始</h2><p>确认右侧参数，生成可编辑的三维实体。</p><span>周期轮辐 · 概念模板 01</span></div>}
    {(loading || building) && <div className="viewer-progress" role="status"><span className="spinner"/>{building ? '正在构建并检查实体，上一版仍可查看' : '正在加载模型'}</div>}
    {error && <div className="viewer-error" role="alert">{error}</div>}
    <div className="viewport-tools">
      <button onClick={() => view(false)} title="透视视图" aria-label="透视视图"><Rotate3D size={18}/></button>
      <button onClick={() => view(true)} title="正面视图" aria-label="正面视图"><Crosshair size={18}/></button>
      <span/>
      <button onClick={() => setWireframe(!wireframe)} className={wireframe ? 'active' : ''} aria-pressed={wireframe} title="线框" aria-label="切换线框"><Layers2 size={18}/></button>
      <button onClick={() => setSection(!section)} className={section ? 'active' : ''} aria-pressed={section} title="剖切" aria-label="切换剖切"><ScanLine size={18}/></button>
    </div>
    <div className="viewport-hint">拖动旋转 <i/> 滚轮缩放 <i/> 右键平移</div>
    <div className="axis-label"><b>Z</b><span>X</span><em>Y</em></div>
  </div>;
}
