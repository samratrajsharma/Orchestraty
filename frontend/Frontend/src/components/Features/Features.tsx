import React from 'react';
import './Features.css';

const features = [
  {
    icon: (<svg width="28" height="28" viewBox="0 0 28 28" fill="none"><rect width="28" height="28" rx="8" fill="#eef2ff"/><path d="M8 20V10l4 3 4-6 4 5v8H8z" stroke="#6366f1" strokeWidth="1.8" fill="none"/></svg>),
    tag: 'Data Intelligence', title: 'Auto Structuring & EDA',
    description: 'Schema detection, null handling, quality scoring, statistical profiling, vector embeddings, and KMeans/HDBSCAN clustering \u2014 all automated.',
    link: '/app/structuring',
  },
  {
    icon: (<svg width="28" height="28" viewBox="0 0 28 28" fill="none"><rect width="28" height="28" rx="8" fill="#f0fdf4"/><path d="M9 14l3 3 7-7" stroke="#10b981" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>),
    tag: 'AI Labeling', title: 'Multi-Source Labeling',
    description: 'Rule-based engines, LLM predictions, similarity propagation, synthetic data generation, and confidence-weighted aggregation.',
    link: '/app/ai-labeling',
  },
  {
    icon: (<svg width="28" height="28" viewBox="0 0 28 28" fill="none"><rect width="28" height="28" rx="8" fill="#fdf4ff"/><circle cx="14" cy="14" r="5" stroke="#a855f7" strokeWidth="1.8"/><path d="M14 9V7M14 21v-2M9 14H7M21 14h-2" stroke="#a855f7" strokeWidth="1.8" strokeLinecap="round"/></svg>),
    tag: 'Vision AI', title: 'Image Pipeline & CLIP',
    description: 'Upload, auto-extract metadata, generate CLIP embeddings, cluster visually similar images, and run text-to-image semantic search.',
    link: '/app/images',
  },
  {
    icon: (<svg width="28" height="28" viewBox="0 0 28 28" fill="none"><rect width="28" height="28" rx="8" fill="#fef3c7"/><path d="M14 8v5l3 3" stroke="#f59e0b" strokeWidth="2" strokeLinecap="round"/><circle cx="14" cy="14" r="6" stroke="#f59e0b" strokeWidth="1.8" fill="none"/></svg>),
    tag: 'Orchestration', title: 'Workflow Engine',
    description: 'Chain structuring, EDA, labeling, quality, and export into multi-step pipelines. Use templates or build custom workflows.',
    link: '/app/workflows',
  },
  {
    icon: (<svg width="28" height="28" viewBox="0 0 28 28" fill="none"><rect width="28" height="28" rx="8" fill="#f0f9ff"/><path d="M8 18l4-8 4 5 4-4" stroke="#06b6d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>),
    tag: 'Multi-LLM', title: 'Any Provider, One API',
    description: 'Connect OpenAI, Anthropic, Groq, Ollama, or any OpenAI-compatible endpoint. Switch models per task, test connections live.',
    link: '/app/llm',
  },
  {
    icon: (<svg width="28" height="28" viewBox="0 0 28 28" fill="none"><rect width="28" height="28" rx="8" fill="#fff1f2"/><rect x="7" y="10" width="14" height="10" rx="2" stroke="#ef4444" strokeWidth="1.8"/><path d="M10 10V8a4 4 0 018 0v2" stroke="#ef4444" strokeWidth="1.8" strokeLinecap="round"/></svg>),
    tag: 'Enterprise', title: 'RBAC & Audit Logs',
    description: 'Four-tier role system (admin, reviewer, annotator, viewer), full audit trail, JWT auth, and user management dashboard.',
    link: '/app/admin',
  },
];

const Features: React.FC = () => {
  return (
    <section className="features section" id="features">
      <div className="container">
        <div className="features__header">
          <span className="features__eyebrow">Platform</span>
          <h2 className="features__title">Everything you need for production-ready data</h2>
          <p className="features__subtitle">
            From raw CSV to labeled, quality-checked, exported datasets \u2014 Orchestraty handles the entire pipeline.
          </p>
        </div>
        <div className="features__grid">
          {features.map((f, i) => (
            <a className="features__card" key={i} href={f.link} style={{textDecoration:'none'}}>
              <div className="features__card-icon">{f.icon}</div>
              <span className="features__card-tag">{f.tag}</span>
              <h3 className="features__card-title">{f.title}</h3>
              <p className="features__card-desc">{f.description}</p>
              <span className="features__card-link">Explore \u2192</span>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
};

export default Features;
