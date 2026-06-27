import React from 'react';
import { Link } from 'react-router-dom';
import './UseCases.css';

// ─────────────────────────────────────────────────────────────────
// Use cases organized under the product pillars so this
// section mirrors the pill buttons in the Features section.
// AI Data Operating cases link to the matching app routes; the
// coming-soon pillars stay non-interactive.
// ─────────────────────────────────────────────────────────────────
type Pillar = {
  name: string;
  tagline: string;
  accent: string;
  comingSoon?: boolean;
  cases: { title: string; description: string; link?: string }[];
};

const pillars: Pillar[] = [
  {
    name: 'AI Data Operating',
    tagline: 'Production-ready datasets, end to end.',
    accent: '#1DB954',
    cases: [
      {
        title: 'Training dataset curation',
        description: 'Go from raw files to a labeled, training-ready dataset — solo.',
        link: '/#features',
      },
      {
        title: 'ML data quality automation',
        description: 'Catch quality issues and drift before they poison training.',
        link: '/#features',
      },
      {
        title: 'Vision pipelines',
        description: 'CLIP embeddings, clustering, semantic search.',
        link: '/#features',
      },
    ],
  },
  {
    name: 'Know Your Code',
    tagline: 'Codebase intelligence and review.',
    accent: '#1ED760',
    comingSoon: true,
    cases: [
      { title: 'Engineer onboarding', description: 'Map the architecture in hours, not weeks.' },
      { title: 'Architecture review', description: 'Automated dependency and pattern analysis.' },
      { title: 'Refactor planning', description: 'See every call site before you touch a symbol.' },
    ],
  },
];

const UseCases: React.FC = () => {
  return (
    <section className="usecases section" id="use-cases">
      <div className="container">
        <div className="usecases__header">
          <span className="usecases__eyebrow">Use Cases</span>
          <h2 className="usecases__title">Where each pillar earns its keep</h2>
          <p className="usecases__subtitle">
            Two pillars, real problems they solve. Click any AI Data Operating case to jump into the app.
          </p>
        </div>

        <div className="usecases__pillars">
          {pillars.map((p) => (
            <article
              key={p.name}
              className={`uc-pillar${p.comingSoon ? ' uc-pillar--soon' : ''}`}
              style={{ ['--uc-accent' as string]: p.accent }}
            >
              <header className="uc-pillar__head">
                <div className="uc-pillar__title-row">
                  <h3 className="uc-pillar__title">{p.name}</h3>
                </div>
                <p className="uc-pillar__tagline">{p.tagline}</p>
              </header>

              <ul className="uc-pillar__cases">
                {p.cases.map((c) => {
                  const Content = (
                    <>
                      <span className="uc-case__bullet" aria-hidden="true" />
                      <div className="uc-case__body">
                        <h4 className="uc-case__title">{c.title}</h4>
                        <p className="uc-case__desc">{c.description}</p>
                      </div>
                      {c.link && (
                        <span className="uc-case__arrow" aria-hidden="true">→</span>
                      )}
                    </>
                  );

                  // Linkable case (real product route) renders as a Link;
                  // coming-soon cases render as a plain list item.
                  return c.link ? (
                    <li key={c.title}>
                      <Link to={c.link} className="uc-case uc-case--link">
                        {Content}
                      </Link>
                    </li>
                  ) : (
                    <li key={c.title} className="uc-case">{Content}</li>
                  );
                })}
              </ul>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
};

export default UseCases;
