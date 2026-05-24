import React, { useEffect, useRef } from 'react';
import './ParticleCanvas.css';

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  baseVx: number;
  baseVy: number;
  size: number;
  baseSize: number;
  color: string;
  alpha: number;
  alphaDir: number;
}

const COLORS = [
  '#6366f1', '#8b5cf6', '#a855f7',
  '#06b6d4', '#10b981', '#f59e0b',
  '#4f46e5', '#0891b2',
];

const MOUSE_RADIUS = 180;
const CONNECT_DIST = 120;
const ATTRACT_STRENGTH = 0.035;
const DAMPING = 0.92;

const ParticleCanvas: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const mouseRef = useRef<{ x: number; y: number; active: boolean }>({ x: -9999, y: -9999, active: false });
  const animRef = useRef<number>(0);

  const createParticle = (width: number, height: number): Particle => {
    const vx = (Math.random() - 0.5) * 0.5;
    const vy = (Math.random() - 0.5) * 0.5;
    const size = Math.random() * 3 + 1.5;
    return {
      x: Math.random() * width,
      y: Math.random() * height,
      vx, vy,
      baseVx: vx,
      baseVy: vy,
      size,
      baseSize: size,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      alpha: Math.random() * 0.6 + 0.2,
      alphaDir: Math.random() > 0.5 ? 1 : -1,
    };
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    };
    resize();

    const count = Math.floor((canvas.width * canvas.height) / 7000);
    particlesRef.current = Array.from({ length: count }, () =>
      createParticle(canvas.width, canvas.height)
    );

    const onMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current.x = e.clientX - rect.left;
      mouseRef.current.y = e.clientY - rect.top;
      mouseRef.current.active = true;
    };
    const onMouseLeave = () => {
      mouseRef.current.active = false;
    };

    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseleave', onMouseLeave);

    const animate = () => {
      const W = canvas.width;
      const H = canvas.height;
      ctx.clearRect(0, 0, W, H);

      const particles = particlesRef.current;
      const mouse = mouseRef.current;

      // Update positions
      for (const p of particles) {
        if (mouse.active) {
          const dx = mouse.x - p.x;
          const dy = mouse.y - p.y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < MOUSE_RADIUS && dist > 0) {
            const force = (1 - dist / MOUSE_RADIUS) * ATTRACT_STRENGTH;
            p.vx += dx / dist * force * 8;
            p.vy += dy / dist * force * 8;
            // Grow particles near cursor
            p.size = p.baseSize + (1 - dist / MOUSE_RADIUS) * 3;
          } else {
            p.size += (p.baseSize - p.size) * 0.05;
          }
        } else {
          p.size += (p.baseSize - p.size) * 0.05;
          // Drift back toward base velocity
          p.vx += (p.baseVx - p.vx) * 0.01;
          p.vy += (p.baseVy - p.vy) * 0.01;
        }

        p.vx *= DAMPING;
        p.vy *= DAMPING;
        p.x += p.vx;
        p.y += p.vy;

        // Alpha pulse
        p.alpha += 0.003 * p.alphaDir;
        if (p.alpha <= 0.1 || p.alpha >= 0.8) p.alphaDir *= -1;

        // Wrap edges
        if (p.x < -10) p.x = W + 10;
        if (p.x > W + 10) p.x = -10;
        if (p.y < -10) p.y = H + 10;
        if (p.y > H + 10) p.y = -10;
      }

      // Draw connection lines between nearby particles (only near mouse for performance)
      if (mouse.active) {
        const nearMouse: Particle[] = [];
        for (const p of particles) {
          const dx = mouse.x - p.x;
          const dy = mouse.y - p.y;
          if (dx * dx + dy * dy < (MOUSE_RADIUS + 40) * (MOUSE_RADIUS + 40)) {
            nearMouse.push(p);
          }
        }

        for (let i = 0; i < nearMouse.length; i++) {
          for (let j = i + 1; j < nearMouse.length; j++) {
            const a = nearMouse[i];
            const b = nearMouse[j];
            const dx = a.x - b.x;
            const dy = a.y - b.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < CONNECT_DIST) {
              const opacity = (1 - dist / CONNECT_DIST) * 0.35;
              ctx.beginPath();
              ctx.moveTo(a.x, a.y);
              ctx.lineTo(b.x, b.y);
              ctx.strokeStyle = `rgba(129, 140, 248, ${opacity})`;
              ctx.lineWidth = 0.8;
              ctx.stroke();
            }
          }
        }

        // Draw lines from particles to mouse cursor
        for (const p of nearMouse) {
          const dx = mouse.x - p.x;
          const dy = mouse.y - p.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < MOUSE_RADIUS) {
            const opacity = (1 - dist / MOUSE_RADIUS) * 0.2;
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(mouse.x, mouse.y);
            ctx.strokeStyle = `rgba(139, 92, 246, ${opacity})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }

      // Draw particles
      for (const p of particles) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = p.alpha;
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      // Mouse glow
      if (mouse.active) {
        const gradient = ctx.createRadialGradient(mouse.x, mouse.y, 0, mouse.x, mouse.y, MOUSE_RADIUS * 0.6);
        gradient.addColorStop(0, 'rgba(99, 102, 241, 0.06)');
        gradient.addColorStop(1, 'rgba(99, 102, 241, 0)');
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(mouse.x, mouse.y, MOUSE_RADIUS * 0.6, 0, Math.PI * 2);
        ctx.fill();
      }

      animRef.current = requestAnimationFrame(animate);
    };

    animate();

    const handleResize = () => {
      resize();
      const newCount = Math.floor((canvas.width * canvas.height) / 7000);
      particlesRef.current = Array.from({ length: newCount }, () =>
        createParticle(canvas.width, canvas.height)
      );
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      canvas.removeEventListener('mousemove', onMouseMove);
      canvas.removeEventListener('mouseleave', onMouseLeave);
      cancelAnimationFrame(animRef.current);
    };
  }, []);

  return <canvas ref={canvasRef} className="particle-canvas" />;
};

export default ParticleCanvas;
