"use client";

// ===== Brand logo: flat marks + 3D interactive mark (ported from logo3d.jsx) =====

import React from "react";

// Flat brand mark (rounded red tile + play triangle) — used in chrome.
export function MiniMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`minimark ${className}`} style={{ width: size, height: size, borderRadius: size * 0.28 }}>
      <span
        className="minimark-tri"
        style={{ borderWidth: `${size * 0.16}px 0 ${size * 0.16}px ${size * 0.26}px`, marginLeft: size * 0.04 }}
      />
    </span>
  );
}

export function Wordmark({
  size = 28,
  showText = true,
  className = "",
}: {
  size?: number;
  showText?: boolean;
  className?: string;
}) {
  return (
    <div className={`wordmark ${className}`}>
      <MiniMark size={size} />
      {showText && (
        <span className="wordmark-text" style={{ fontSize: size * 0.72 }}>
          Reelo
        </span>
      )}
    </div>
  );
}

// 3D interactive logo — drag to rotate, momentum, self-stopping loop (no idle CPU).
export function Logo3D({ size = 240 }: { size?: number }) {
  const stageRef = React.useRef<HTMLDivElement>(null);
  const objRef = React.useRef<HTMLDivElement>(null);
  const s = React.useRef<any>({
    rotX: -16,
    rotY: -26,
    vel: 0,
    dragging: false,
    lastX: 0,
    lastY: 0,
    running: false,
    raf: 0,
  });

  const depth = size * 0.24;
  const LAYERS = Math.min(100, Math.max(56, Math.round(depth / 0.55)));
  const step = depth / LAYERS;
  const tile = size * 0.82;

  React.useEffect(() => {
    const st = s.current;
    const REST_X = -16,
      REST_Y = -24;
    const apply = () => {
      if (objRef.current) {
        objRef.current.style.transform = `translateZ(-${depth / 2}px) rotateX(${st.rotX}deg) rotateY(${st.rotY}deg)`;
      }
    };
    const loop = () => {
      let dY = 0;
      if (!st.dragging) {
        st.rotY += st.vel;
        st.vel *= 0.92; // momentum friction
        st.rotX += (REST_X - st.rotX) * 0.06; // ease tilt back
        dY = ((REST_Y - st.rotY + 540) % 360) - 180; // shortest path to rest angle
        st.rotY += dY * 0.03; // gentle spring to a flattering 3/4 pose
      }
      apply();
      const moving =
        st.dragging || Math.abs(st.vel) > 0.03 || Math.abs(dY) > 0.3 || Math.abs(st.rotX - REST_X) > 0.15;
      if (moving) {
        st.raf = requestAnimationFrame(loop);
      } else {
        st.rotY = REST_Y;
        st.rotX = REST_X;
        apply();
        st.running = false;
      }
    };
    st.ensure = () => {
      if (!st.running) {
        st.running = true;
        st.raf = requestAnimationFrame(loop);
      }
    };
    // gentle one-time intro spin, then springs to rest
    apply(); // paint the initial pose immediately (avoids a flat frame before rAF)
    st.vel = 7;
    st.ensure();
    return () => {
      cancelAnimationFrame(st.raf);
      // Reset the loop guard so a remount (e.g. React StrictMode's double-invoke
      // in dev) can restart the animation — otherwise `running` stays true and
      // ensure() becomes a no-op, leaving the logo frozen.
      st.running = false;
    };
  }, [depth]);

  const onDown = (e: React.PointerEvent) => {
    const st = s.current;
    st.dragging = true;
    st.vel = 0;
    st.lastX = e.clientX;
    st.lastY = e.clientY;
    try {
      stageRef.current?.setPointerCapture(e.pointerId);
    } catch (_) {}
    st.ensure && st.ensure();
  };
  const onMove = (e: React.PointerEvent) => {
    const st = s.current;
    if (!st.dragging) return;
    const dx = e.clientX - st.lastX,
      dy = e.clientY - st.lastY;
    st.lastX = e.clientX;
    st.lastY = e.clientY;
    st.rotY += dx * 0.4;
    st.rotX = Math.max(-62, Math.min(62, st.rotX - dy * 0.3));
    st.vel = dx * 0.4;
    st.ensure && st.ensure();
  };
  const onUp = (e: React.PointerEvent) => {
    const st = s.current;
    st.dragging = false;
    try {
      stageRef.current?.releasePointerCapture(e.pointerId);
    } catch (_) {}
    st.ensure && st.ensure();
  };

  const layers = [];
  for (let i = 0; i < LAYERS; i++) {
    const t = i / (LAYERS - 1);
    const pct = 52 + t * 48; // back darker -> front brighter
    layers.push(
      <div
        key={i}
        className="l3d-layer"
        style={{
          width: tile,
          height: tile,
          marginLeft: -tile / 2,
          marginTop: -tile / 2,
          borderRadius: tile * 0.27,
          transform: `translateZ(${i * step}px)`,
          background: `color-mix(in oklab, var(--brand) ${pct}%, #1a0606)`,
        }}
      />,
    );
  }

  return (
    <div
      className="l3d-stage"
      ref={stageRef}
      style={{ width: size * 1.5, height: size * 1.5, perspective: size * 4, touchAction: "none" }}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      <div className="l3d-glow" style={{ width: size, height: size }} />
      <div className="l3d-obj" ref={objRef} style={{ width: tile, height: tile }}>
        {layers}
        {/* front face sheen + play triangle */}
        <div
          className="l3d-face"
          style={{
            width: tile,
            height: tile,
            marginLeft: -tile / 2,
            marginTop: -tile / 2,
            borderRadius: tile * 0.27,
            transform: `translateZ(${depth + 0.5}px)`,
          }}
        >
          <span
            className="l3d-tri"
            style={{
              borderWidth: `${tile * 0.17}px 0 ${tile * 0.17}px ${tile * 0.28}px`,
              marginLeft: tile * 0.05,
            }}
          />
        </div>
      </div>
    </div>
  );
}
