import React from 'react';
import './UseCases.css';

const cases = [
  {
    number: '01', title: 'NLP Dataset Curation',
    description: 'Upload raw text data, auto-detect schemas, clean and normalize, generate embeddings, then use AI-powered labeling with multiple LLMs to produce gold-standard training datasets.',
    tags: ['Structuring', 'Embeddings', 'AI Labels', 'Export'],
    color: '#6366f1', bg: '#eef2ff', link: '/app/datasets',
  },
  {
    number: '02', title: 'Computer Vision Pipeline',
    description: 'Batch upload images, extract EXIF metadata, generate CLIP embeddings, auto-cluster by visual similarity, and use text-to-image search to find exactly what you need.',
    tags: ['CLIP', 'Clustering', 'Semantic Search', 'Gallery'],
    color: '#10b981', bg: '#f0fdf4', link: '/app/images',
  },
  {
    number: '03', title: 'Multi-Model Evaluation',
    description: 'Configure OpenAI, Anthropic, Groq, and local Ollama models side by side. Test connections, compare outputs, and switch providers per task without changing code.',
    tags: ['OpenAI', 'Anthropic', 'Ollama', 'Groq'],
    color: '#a855f7', bg: '#faf5ff', link: '/app/llm',
  },
  {
    number: '04', title: 'End-to-End Workflow Automation',
    description: 'Chain ingestion, structuring, EDA, labeling, quality review, and export into a single automated pipeline. Use built-in templates or create your own.',
    tags: ['Workflows', 'Templates', 'Automation', 'Pipeline'],
    color: '#06b6d4', bg: '#f0f9ff', link: '/app/workflows',
  },
];

const UseCases: React.FC = () => {
  return (
    <section className="usecases section" id="use-cases">
      <div className="container">
        <div className="usecases__header">
          <span className="usecases__eyebrow">Use Cases</span>
          <h2 className="usecases__title">Built for real-world data teams</h2>
          <p className="usecases__subtitle">
            Whether you are curating NLP datasets or building vision pipelines, Orchestraty scales with your workflow.
          </p>
        </div>
        <div className="usecases__list">
          {cases.map((c, i) => (
            <a className="usecases__card" key={i} href={c.link} style={{textDecoration:'none', color:'inherit'}}>
              <div className="usecases__card-left">
                <span className="usecases__number" style={{ color: c.color }}>{c.number}</span>
                <h3 className="usecases__card-title">{c.title}</h3>
                <p className="usecases__card-desc">{c.description}</p>
                <div className="usecases__tags">
                  {c.tags.map((tag) => (
                    <span key={tag} className="usecases__tag" style={{ background: c.bg, color: c.color }}>{tag}</span>
                  ))}
                </div>
              </div>
              <div className="usecases__card-visual" style={{ background: c.bg }}>
                <div className="usecases__visual-inner" style={{ borderColor: c.color + '33' }}>
                  <span className="usecases__visual-number" style={{ color: c.color }}>{c.number}</span>
                </div>
              </div>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
};

export default UseCases;
