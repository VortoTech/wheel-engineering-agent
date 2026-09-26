import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { Box, Contrast, Crosshair, Layers2, Rotate3D, ScanLine } from 'lucide-react';

type Runtime = { camera: THREE.PerspectiveCamera; controls: OrbitControls; size: number };

export function Viewer({ url, building, displayOnly = false, axisMode = 'cad-z-up', label, initialNeutral = false, unitLabel = 'mm', inspectionLighting = false }: {
  url: string | null; building: boolean; displayOnly?: boolean;
  axisMode?: 'cad-z-up' | 'native-y-up'; label?: string; initialNeutral?: boolean; unitLabel?: string; inspectionLighting?: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  const runtime = useRef<Runtime | null>(null);
  const [wireframe, setWireframe] = useState(false);
  const [section, setSection] = useState(false);
  const [neutral, setNeutral] = useState(initialNeutral);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const options = useRef({ wireframe, section, neutral });
  options.current = { wireframe, section, neutral };

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
    renderer.toneMappingExposure = inspectionLighting ? 1 : 1.1;
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    // Studio reflections: without an environment map, metal renders flat grey (satin/gloss need
    // something to reflect). Inspection lighting keeps plain lights for reading surfaces.
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = inspectionLighting ? null : pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = environment;
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.09;
    controls.minDistance = 0.3;
    controls.maxDistance = 6000;
    scene.add(new THREE.HemisphereLight(0xc7defa, 0x404a5e, inspectionLighting ? .7 : .4));
    const key = new THREE.DirectionalLight(0xf5f6ff, inspectionLighting ? 3 : 1.6);
    key.position.set(inspectionLighting ? -4 : 2, 4, inspectionLighting ? 1 : 5); scene.add(key);
    const fill = new THREE.DirectionalLight(0xa6cfff, inspectionLighting ? .35 : .5);
    fill.position.set(-3, -1, 2); scene.add(fill);
    const back = new THREE.DirectionalLight(0xffffff, inspectionLighting ? .5 : .8);
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
        // CadQuery needs its Z-axis restored; external reconstruction GLBs keep native orientation.
        if (axisMode === 'cad-z-up') model.rotation.x += Math.PI / 2;
        model.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(model);
        const dimensions = bounds.getSize(new THREE.Vector3());
        const size = Math.max(dimensions.x, dimensions.y, dimensions.z);
        model.position.sub(bounds.getCenter(new THREE.Vector3()));
        model.traverse((child) => {
          if (child instanceof THREE.Mesh) {
            const original = Array.isArray(child.material) ? child.material : [child.material];
            const converted = original.map((source) => {
              const material = source instanceof THREE.MeshStandardMaterial ? source.clone()
                : new THREE.MeshStandardMaterial({ color: 0x9ca9bb });
              const dark = Math.max(material.color.r, material.color.g, material.color.b) < 0.3;
              if (dark) material.color.multiplyScalar(1.25);
              material.metalness = dark ? 0.8 : 0.85;
              material.roughness = dark ? 0.35 : 0.42;
              material.side = THREE.DoubleSide;
              material.userData.baseColor = material.color.clone();
              material.userData.baseMetalness = material.metalness;
              material.userData.baseRoughness = material.roughness;
              materials.push(material);
              return material;
            });
            child.material = Array.isArray(child.material) ? converted : converted[0];
            original.forEach((m) => m.dispose());
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
        if (material.userData.neutral !== options.current.neutral) {
          material.color.copy(options.current.neutral ? new THREE.Color(0xb7c1cf) : material.userData.baseColor);
          material.metalness = options.current.neutral ? .05 : material.userData.baseMetalness;
          material.roughness = options.current.neutral ? .8 : material.userData.baseRoughness;
          material.userData.neutral = options.current.neutral;
        }
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
      environment?.dispose(); pmrem.dispose();
      renderer.dispose(); renderer.domElement.remove(); runtime.current = null;
    };
  }, [url, axisMode, inspectionLighting]);

  const view = (front: boolean) => {
    const current = runtime.current;
    if (!current) return;
    const { camera, controls, size } = current;
    camera.position.set(front ? 0 : size * .95, front ? 0 : size * .5, size * (front ? 2.15 : 1.9));
    camera.up.set(0, 1, 0); controls.target.set(0, 0, 0); controls.update();
  };

  return <div className="viewer">
    <div className="viewport-label"><span className="live-dot"/> {label ?? (url ? displayOnly ? '外观预览 · 附件仅展示' : '轮毂实体 · 不含展示附件' : '建模空间')} {axisMode === 'cad-z-up' && <span>· {unitLabel}</span>}</div>
    <div className="canvas" ref={container} aria-label="轮毂三维模型，可拖动旋转、滚轮缩放"/>
    {!url && <div className="viewer-empty"><Box size={48} strokeWidth={1}/><h2>{axisMode === 'native-y-up' ? '从主参考图生成视觉网格' : '从第一版轮毂开始'}</h2><p>{axisMode === 'native-y-up' ? '该网格用于造型对照，不是参数化 CAD。' : '确认右侧参数，生成可编辑的三维实体。'}</p><span>{axisMode === 'native-y-up' ? 'Stable Fast 3D · GLB' : '周期轮辐 · 锻造单片模板'}</span></div>}
    {(loading || building) && <div className="viewer-progress" role="status"><span className="spinner"/>{building ? '正在构建并检查实体，上一版仍可查看' : '正在加载模型'}</div>}
    {error && <div className="viewer-error" role="alert">{error}</div>}
    <div className="viewport-tools">
      <button title="浅色轮廓检查" aria-label="浅色轮廓检查" aria-pressed={neutral} onClick={() => setNeutral(!neutral)}><Contrast size={19}/></button>
      <button onClick={() => view(false)} title="透视视图" aria-label="透视视图"><Rotate3D size={18}/></button>
      <button onClick={() => view(true)} title="正面视图" aria-label="正面视图"><Crosshair size={18}/></button>
      <span/>
      <button onClick={() => setWireframe(!wireframe)} className={wireframe ? 'active' : ''} aria-pressed={wireframe} title="线框" aria-label="切换线框"><Layers2 size={18}/></button>
      <button onClick={() => setSection(!section)} className={section ? 'active' : ''} aria-pressed={section} title="剖切" aria-label="切换剖切"><ScanLine size={18}/></button>
    </div>
    <div className="viewport-hint">拖动旋转 <i/> 滚轮缩放 <i/> 右键平移</div>
    <div className="axis-label"><b>{axisMode === 'native-y-up' ? 'Y' : 'Z'}</b><span>X</span><em>{axisMode === 'native-y-up' ? 'Z' : 'Y'}</em></div>
  </div>;
}
