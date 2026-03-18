import { useRef, useMemo } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Line, Points, PointMaterial } from "@react-three/drei";
import * as THREE from "three";
import * as random from "maath/random/dist/maath-random.esm";

/* ================= CIRCUIT LINES ================= */
function CircuitLines() {
  const lineRef = useRef();

  const points = useMemo(() => {
    const pts = [];
    for (let i = 0; i < 60; i++) {
      const x = (Math.random() - 0.5) * 20;
      const y = (Math.random() - 0.5) * 20;
      const z = (Math.random() - 0.5) * 10;

      pts.push(new THREE.Vector3(x, y, z));
      pts.push(
        new THREE.Vector3(
          x + (Math.random() - 0.5) * 2,
          y + (Math.random() - 0.5) * 2,
          z,
        ),
      );
    }
    return pts;
  }, []);

  useFrame((state) => {
    if (lineRef.current) {
      lineRef.current.rotation.y = state.clock.getElapsedTime() * 0.05;
      lineRef.current.rotation.x = state.clock.getElapsedTime() * 0.02;
    }
  });

  return (
    <group ref={lineRef}>
      <Line
        points={points}
        color="#05DC7F" // ⚡ PURE NEON GREEN
        lineWidth={0.8} // thori thick for glow feel
        opacity={0.9}
        transparent
      />
      <PointMaterial
        transparent
        color="#39ff14"
        size={0.04}
        sizeAttenuation
        depthWrite={false}
      />
    </group>
  );
}

/* ================= PARTICLES ================= */
function CircuitParticles() {
  const ref = useRef();
  const sphere = useMemo(
    () => random.inSphere(new Float32Array(700), { radius: 12 }),
    [],
  );

  useFrame((_, delta) => {
    if (ref.current) {
      ref.current.rotation.y -= delta / 18;
      ref.current.rotation.x -= delta / 25;
    }
  });

  return (
    <group rotation={[0, 0, Math.PI / 6]}>
      <Points ref={ref} positions={sphere} stride={3} frustumCulled={false}>
        <PointMaterial
          transparent
          color="#22ff99" // ✨ Neon Particles
          size={0.035}
          sizeAttenuation
          depthWrite={false}
        />
      </Points>
    </group>
  );
}

/* ================= MAIN BACKGROUND ================= */
export default function CircuitBackground() {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 0,
        pointerEvents: "none",
        opacity: 0.45,
        background: "radial-gradient(circle at center, #022c22, #000000)",
      }}
    >
      <Canvas camera={{ position: [0, 0, 6] }}>
        <ambientLight intensity={0.6} />
        <CircuitLines />
        <CircuitParticles />
      </Canvas>
    </div>
  );
}
