import { useRef, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import { useTexture } from '@react-three/drei'
import * as THREE from 'three'

interface Hologram3DProps {
  imageUrl?: string
  depthIntensity?: number
}

function Hologram3DWithTexture({
  imageUrl,
  depthIntensity,
  geometry,
  meshRef,
}: {
  imageUrl: string
  depthIntensity: number
  geometry: THREE.PlaneGeometry
  meshRef: React.RefObject<THREE.Mesh>
}) {
  const texture = useTexture(imageUrl)
  return (
    <group>
      <mesh ref={meshRef} geometry={geometry}>
        <meshPhongMaterial
          map={texture}
          color="#ffffff"
          emissive="#000000"
          emissiveIntensity={0}
          shininess={100}
          transparent
          opacity={0.9}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh geometry={geometry} position={[0, 0, -0.1]}>
        <meshBasicMaterial
          color="#00d4ff"
          transparent
          opacity={0.2}
          side={THREE.DoubleSide}
        />
      </mesh>
      <Points />
    </group>
  )
}

function Hologram3DDefault({
  geometry,
  meshRef,
}: {
  geometry: THREE.PlaneGeometry
  meshRef: React.RefObject<THREE.Mesh>
}) {
  return (
    <group>
      <mesh ref={meshRef} geometry={geometry}>
        <meshPhongMaterial
          color="#667eea"
          emissive="#764ba2"
          emissiveIntensity={0.3}
          shininess={100}
          transparent
          opacity={0.9}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh geometry={geometry} position={[0, 0, -0.1]}>
        <meshBasicMaterial
          color="#00d4ff"
          transparent
          opacity={0.2}
          side={THREE.DoubleSide}
        />
      </mesh>
      <Points />
    </group>
  )
}

export function Hologram3D({
  imageUrl,
  depthIntensity = 1.5,
}: Hologram3DProps) {
  const meshRef = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.rotation.y =
        Math.sin(state.clock.elapsedTime * 0.5) * 0.3
      meshRef.current.rotation.x =
        Math.cos(state.clock.elapsedTime * 0.3) * 0.1
      meshRef.current.position.y =
        Math.sin(state.clock.elapsedTime * 0.8) * 0.2
    }
  })

  const geometry = useMemo(() => {
    const geo = new THREE.PlaneGeometry(4, 4, 64, 64)
    const positions = geo.attributes.position.array as Float32Array

    for (let i = 0; i < positions.length; i += 3) {
      const x = positions[i]
      const y = positions[i + 1]
      const distance = Math.sqrt(x * x + y * y)
      const depth = Math.max(0, 1 - distance / 3) * depthIntensity
      positions[i + 2] = depth
    }

    geo.computeVertexNormals()
    return geo
  }, [depthIntensity])

  return imageUrl ? (
    <Hologram3DWithTexture
      imageUrl={imageUrl}
      depthIntensity={depthIntensity}
      geometry={geometry}
      meshRef={meshRef}
    />
  ) : (
    <Hologram3DDefault geometry={geometry} meshRef={meshRef} />
  )
}

function Points() {
  const pointsRef = useRef<THREE.Points>(null)

  const particles = useMemo(() => {
    const count = 1000
    const positions = new Float32Array(count * 3)

    for (let i = 0; i < count; i++) {
      positions[i * 3] = (Math.random() - 0.5) * 10
      positions[i * 3 + 1] = (Math.random() - 0.5) * 10
      positions[i * 3 + 2] = (Math.random() - 0.5) * 10
    }

    return positions
  }, [])

  useFrame((state) => {
    if (pointsRef.current) {
      pointsRef.current.rotation.y = state.clock.elapsedTime * 0.05
    }
  })

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={particles.length / 3}
          array={particles}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        size={0.05}
        color="#00d4ff"
        transparent
        opacity={0.6}
        sizeAttenuation
      />
    </points>
  )
}
