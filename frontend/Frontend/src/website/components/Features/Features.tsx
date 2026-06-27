import React, { useState, useEffect, useCallback } from 'react';
import './Features.css';

interface Feat {
  accent: string;
  tag: string;
  title: string;
  description: string;
  link: string;
}

// ── Orchestraty (live) — six engines of the data OS ──
const aiDataFeatures: Feat[] = [
  {
    accent: '#1DB954',
    tag: 'Data Intelligence',
    title: 'Auto Structuring & EDA',
    description:
      'Automated quality scoring, statistical profiling, and EDA — plus embeddings and density clustering that surface structure you did not know was there.',
    link: '/#features',
  },
  {
    accent: '#1DB954',
    tag: 'AI Labeling',
    title: 'Multi-Source Labeling',
    description:
      'Rule-based engines, LLM predictions, similarity propagation, synthetic data generation, and confidence-weighted aggregation.',
    link: '/#features',
  },
  {
    accent: '#1ED760',
    tag: 'Vision AI',
    title: 'Image Pipeline & CLIP',
    description:
      'Upload, auto-extract metadata, generate CLIP embeddings, cluster visually similar images, and run text-to-image semantic search.',
    link: '/#features',
  },
  {
    accent: '#4ade80',
    tag: 'Orchestration',
    title: 'Workflow Engine',
    description:
      'Chain structuring, EDA, labeling, quality, and export into multi-step pipelines. Use templates or build custom workflows.',
    link: '/#features',
  },
  {
    accent: '#1ED760',
    tag: 'Multi-LLM',
    title: 'Any Provider, One API',
    description:
      'Connect OpenAI, Anthropic, Groq, Ollama, or any OpenAI-compatible endpoint. Switch models per task, test connections live.',
    link: '/#features',
  },
  {
    accent: '#1DB954',
    tag: 'Local-first',
    title: 'Your Data, Your Machine',
    description:
      'Everything runs on your computer. No cloud, no accounts, no telemetry. Your datasets and API keys never leave your machine.',
    link: '/#features',
  },
];

// ── Know Your Code (coming soon, but already built) — what it does today ──
const knowYourCodeFeatures: Feat[] = [
  {
    accent: '#1DB954',
    tag: 'Architecture',
    title: 'Codebase Mapping',
    description:
      'Point it at any repo and it maps the real structure — modules, files, symbols, and the dependency graph that connects them.',
    link: '/know-your-code#know-your-code',
  },
  {
    accent: '#1ED760',
    tag: 'Comprehension',
    title: 'Per-File Explanations',
    description:
      'Rich, sectioned explanations of what each file and module actually does — written for someone seeing the code for the first time.',
    link: '/know-your-code#know-your-code',
  },
  {
    accent: '#1DB954',
    tag: 'Ask',
    title: 'Ask Your Codebase',
    description:
      'Ask questions in plain English and get answers grounded in your code, powered by your own LLM. No more spelunking through unfamiliar files.',
    link: '/know-your-code#know-your-code',
  },
  {
    accent: '#1ED760',
    tag: 'Over time',
    title: 'Living Timeline',
    description:
      'Capture a folder over time. An append-only change log shows exactly what was added, removed, or refactored since you last looked.',
    link: '/know-your-code#know-your-code',
  },
  {
    accent: '#4ade80',
    tag: 'Trends',
    title: 'Trends & Health',
    description:
      'Track complexity, size, dead code, and coupling over weeks — see at a glance whether the codebase is getting healthier or messier.',
    link: '/know-your-code#know-your-code',
  },
  {
    accent: '#1ED760',
    tag: 'BYO-LLM · Local',
    title: 'AI Narration',
    description:
      'Turn raw diffs into a plain-English "what changed" story with OpenAI, Anthropic, Groq, or a local Ollama model — multi-folder, fully local.',
    link: '/know-your-code#know-your-code',
  },
];

type ModalKey = 'orchestraty' | 'kyc';

const MODALS: Record<ModalKey, { eyebrow: string; title: string; subtitle: string; features: Feat[] }> = {
  orchestraty: {
    eyebrow: 'AI Data Operating',
    title: 'Six pillars of the Orchestraty data OS',
    subtitle: 'Hover any tile to see what it does.',
    features: aiDataFeatures,
  },
  kyc: {
    eyebrow: 'Know Your Code',
    title: 'What Know Your Code already does',
    subtitle: 'Built and running today — shipping to everyone next. Hover any tile to see what it does.',
    features: knowYourCodeFeatures,
  },
};

const Features: React.FC = () => {
  const [activeModal, setActiveModal] = useState<ModalKey | null>(null);

  // Lock body scroll while a modal is open + close on Escape.
  useEffect(() => {
    if (!activeModal) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setActiveModal(null);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKey);
    };
  }, [activeModal]);

  const closeModal = useCallback(() => setActiveModal(null), []);
  const active = activeModal ? MODALS[activeModal] : null;

  return (
    <section className="features section" id="features">
      <div className="container">
        <div className="features__header">
          <span className="features__eyebrow">Platform</span>
          <h2 className="features__title">Your entire data pipeline, under one roof</h2>
          <p className="features__subtitle">
            Structuring, EDA, labeling, vision, workflows, and export — six engines in one app, all running on your machine.
          </p>
        </div>

        {/* ── Pillar buttons — both open a feature modal ── */}
        <div className="features__pills">
          <button
            type="button"
            className="features__pill features__pill--primary"
            onClick={() => setActiveModal('orchestraty')}
            aria-haspopup="dialog"
            aria-expanded={activeModal === 'orchestraty'}
          >
            <div className="features__pill-top">
              <span className="features__pill-index">01</span>
              <span className="features__pill-kicker">Platform · Live</span>
            </div>
            <div className="features__pill-body">
              <span className="features__pill-label">AI Data Operating</span>
              <span className="features__pill-sub">
                Six pillars that turn raw data into production-ready, labeled, quality-checked datasets.
              </span>
            </div>
            <div className="features__pill-foot">
              <span className="features__pill-count">6 modules inside</span>
              <span className="features__pill-arrow" aria-hidden="true">→</span>
            </div>
          </button>

          <button
            type="button"
            className="features__pill features__pill--soon"
            onClick={() => setActiveModal('kyc')}
            aria-haspopup="dialog"
            aria-expanded={activeModal === 'kyc'}
          >
            <div className="features__pill-top">
              <span className="features__pill-index">02</span>
            </div>
            <div className="features__pill-body">
              <span className="features__pill-label">Know Your Code</span>
              <span className="features__pill-sub">
                Codebase intelligence that maps your architecture, tracks every change over time, and answers questions about your code.
              </span>
            </div>
            <div className="features__pill-foot">
              <span className="features__pill-count">6 features inside</span>
              <span className="features__pill-arrow" aria-hidden="true">→</span>
            </div>
          </button>
        </div>
      </div>

      {/* ────────────────────────── Modal ────────────────────────── */}
      {active && (
        <div
          className="features-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="features-modal-title"
          onClick={closeModal}
        >
          <div className="features-modal__dialog" onClick={(e) => e.stopPropagation()}>
            <div className="features-modal__header">
              <div>
                <span className="features-modal__eyebrow">{active.eyebrow}</span>
                <h3 id="features-modal-title" className="features-modal__title">
                  {active.title}
                </h3>
                <p className="features-modal__subtitle">{active.subtitle}</p>
              </div>
              <button
                type="button"
                className="features-modal__close"
                onClick={closeModal}
                aria-label="Close"
              >
                <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                  <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </button>
            </div>

            <div className="features-modal__grid">
              {active.features.map((f, i) => (
                <a
                  key={i}
                  href={f.link}
                  className="feat-tile"
                  style={{ ['--tile-accent' as string]: f.accent }}
                >
                  <span className="feat-tile__tag">{f.tag}</span>
                  <h4 className="feat-tile__title">{f.title}</h4>
                  <div className="feat-tile__overlay">
                    <p className="feat-tile__desc">{f.description}</p>
                    <span className="feat-tile__cta">
                      Open <span aria-hidden="true">→</span>
                    </span>
                  </div>
                </a>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
};

export default Features;
