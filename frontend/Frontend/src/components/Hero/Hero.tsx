import React from 'react';
import './Hero.css';
import ParticleCanvas from '../ParticleCanvas/ParticleCanvas';
import OrchestrateIcon from '../Logo/AntigravityLogo';

const Hero: React.FC = () => {
  return (
    <section className="hero" id="home">
      <ParticleCanvas />
      <div className="hero__content">
        <div className="hero__badge">
          <span className="hero__badge-dot"></span>
          Phase 3 Complete \u2014 AI Intelligence Engine is live
        </div>

        <div className="hero__brand">
          <OrchestrateIcon size={40} />
          <span className="hero__brand-name">Orchestraty</span>
        </div>

        <h1 className="hero__headline">
          The AI Operating System<br />
          for your data
        </h1>

        <p className="hero__subtext">
          Ingest, structure, label, and export production-ready datasets.
          Powered by multi-LLM intelligence, CLIP vision, and automated workflows.
        </p>

        <div className="hero__actions">
          <a href="/app/login" className="hero__btn-primary">
            Get Started
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M3 8h10M8 3l5 5-5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </a>
          <a href="#features" className="hero__btn-secondary">
            See Features
          </a>
        </div>

        <div className="hero__stats">
          <div className="hero__stat">
            <span className="hero__stat-value">20+</span>
            <span className="hero__stat-label">API Endpoints</span>
          </div>
          <div className="hero__stat-divider" />
          <div className="hero__stat">
            <span className="hero__stat-value">5</span>
            <span className="hero__stat-label">LLM Providers</span>
          </div>
          <div className="hero__stat-divider" />
          <div className="hero__stat">
            <span className="hero__stat-value">70+</span>
            <span className="hero__stat-label">Source Files</span>
          </div>
          <div className="hero__stat-divider" />
          <div className="hero__stat">
            <span className="hero__stat-value">3</span>
            <span className="hero__stat-label">Phases Built</span>
          </div>
        </div>
      </div>
    </section>
  );
};

export default Hero;
