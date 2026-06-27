import React from 'react';
import { Link, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { GITHUB_URL } from '../../site-links';
import './Docs.css';

// All docs markdown, imported raw at build time (single source of truth).
const RAW = import.meta.glob('../../docs-content/**/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const CONTENT: Record<string, string> = {};
for (const [k, v] of Object.entries(RAW)) {
  const slug = k.replace(/^.*docs-content\//, '').replace(/\.md$/, '');
  CONTENT[slug] = v as string;
}

interface NavItem { t: string; s: string; }
interface NavGroup { title: string; items: NavItem[]; }
const NAV: NavGroup[] = [
  { title: 'Overview', items: [{ t: 'Home', s: 'index' }] },
  { title: 'Getting started', items: [{ t: 'Installation', s: 'getting-started/installation' }, { t: 'Quickstart', s: 'getting-started/quickstart' }] },
  { title: 'Concepts', items: [{ t: 'Architecture', s: 'concepts/architecture' }, { t: 'Data model & versioning', s: 'concepts/data-model' }] },
  { title: 'Pipeline guides', items: [
    { t: 'Datasets (ingest)', s: 'guides/datasets' },
    { t: 'Structuring', s: 'guides/structuring' },
    { t: 'EDA', s: 'guides/eda' },
    { t: 'Rule-based labeling', s: 'guides/labeling' },
    { t: 'AI labeling', s: 'guides/ai-labeling' },
    { t: 'Image pipeline', s: 'guides/images' },
    { t: 'Review & export', s: 'guides/review-export' },
    { t: 'Workflows', s: 'guides/workflows' },
    { t: 'LLM configuration', s: 'guides/llm-config' },
  ] },
  { title: 'Reference', items: [{ t: 'Configuration', s: 'reference/configuration' }, { t: 'API', s: 'reference/api' }, { t: 'Commands', s: 'reference/commands' }] },
  { title: 'More', items: [{ t: 'Deployment', s: 'deployment' }, { t: 'FAQ', s: 'faq' }, { t: 'Roadmap', s: 'roadmap' }, { t: 'Contributing', s: 'contributing' }] },
];

function resolveSlug(cur: string, href: string): string {
  const clean = href.replace(/\.md$/, '').replace(/#.*$/, '');
  const parts = cur.includes('/') ? cur.slice(0, cur.lastIndexOf('/')).split('/') : [];
  for (const seg of clean.split('/')) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') parts.pop();
    else parts.push(seg);
  }
  return parts.join('/');
}

// MkDocs "!!! type \"Title\"" admonitions -> blockquotes (styled in CSS).
function preprocess(md: string): string {
  const lines = md.split('\n');
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const m = lines[i].match(/^!!!\s+(\w+)(?:\s+"([^"]*)")?\s*$/);
    if (m) {
      const title = m[2] || (m[1].charAt(0).toUpperCase() + m[1].slice(1));
      out.push('> **' + title + '**');
      out.push('>');
      i++;
      while (i < lines.length && (lines[i].startsWith('    ') || lines[i].trim() === '')) {
        if (lines[i].trim() === '') {
          if (i + 1 < lines.length && lines[i + 1].startsWith('    ')) { out.push('>'); i++; continue; }
          break;
        }
        out.push('> ' + lines[i].slice(4));
        i++;
      }
      continue;
    }
    out.push(lines[i]);
    i++;
  }
  return out.join('\n');
}

const Docs: React.FC = () => {
  const params = useParams();
  let slug = (params['*'] || 'index').replace(/\/$/, '');
  if (!CONTENT[slug]) slug = 'index';
  const md = preprocess(CONTENT[slug] || '# Not found\n\nThat page does not exist yet.');

  const components = {
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
      if (!href) return <span>{children}</span>;
      if (href.startsWith('http')) return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
      if (href.startsWith('#')) return <a href={href}>{children}</a>;
      if (href.endsWith('.md') || (!href.startsWith('/') && !href.includes('://'))) {
        return <Link to={'/docs/' + resolveSlug(slug, href)}>{children}</Link>;
      }
      return <a href={href}>{children}</a>;
    },
  };

  return (
    <div className="docs">
      <header className="docs__top">
        <Link to="/" className="docs__back">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="19" y1="12" x2="5" y2="12" /><polyline points="12 19 5 12 12 5" /></svg>
          <span>Back to site</span>
        </Link>
        <Link to="/docs" className="docs__home">Orchestraty Docs</Link>
        <a className="docs__gh" href={GITHUB_URL} target="_blank" rel="noopener noreferrer">GitHub ↗</a>
      </header>
      <div className="docs__body">
        <aside className="docs__sidebar">
          <nav>
            {NAV.map((g) => (
              <div key={g.title} className="docs__nav-group">
                <div className="docs__nav-title">{g.title}</div>
                {g.items.map((it) => (
                  <Link key={it.s} to={'/docs/' + it.s} className={'docs__nav-link' + (it.s === slug ? ' docs__nav-link--on' : '')}>{it.t}</Link>
                ))}
              </div>
            ))}
          </nav>
        </aside>
        <main className="docs__content">
          <article className="docs-md">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{md}</ReactMarkdown>
          </article>
        </main>
      </div>
    </div>
  );
};

export default Docs;
