import React, { useState, useEffect } from 'react';
import './Navbar.css';
import OrchestrateIcon from '../Logo/AntigravityLogo';

interface DropdownItem { label: string; description?: string; href: string; }
interface NavItem { label: string; href?: string; dropdown?: DropdownItem[]; }

const navItems: NavItem[] = [
  {
    label: 'Platform',
    dropdown: [
      { label: 'Data Pipelines', description: 'Ingest, clean, structure and profile datasets', href: '/app/structuring' },
      { label: 'AI Labeling', description: 'LLM-powered predictions, propagation & aggregation', href: '/app/ai-labeling' },
      { label: 'Image Intelligence', description: 'CLIP embeddings, clustering & semantic search', href: '/app/images' },
      { label: 'Workflow Engine', description: 'Multi-step orchestration pipelines', href: '/app/workflows' },
    ],
  },
  {
    label: 'Use Cases',
    href: '#use-cases',
  },
  { label: 'Pricing', href: '#pricing' },
  {
    label: 'Resources',
    dropdown: [
      { label: 'Documentation', description: 'API reference & integration guides', href: '#' },
      { label: 'GitHub', description: 'Open source repository', href: '#' },
    ],
  },
];

const Navbar: React.FC = () => {
  const [activeDropdown, setActiveDropdown] = useState<string | null>(null);
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const h = () => setScrolled(window.scrollY > 10);
    window.addEventListener('scroll', h);
    return () => window.removeEventListener('scroll', h);
  }, []);

  return (
    <nav className={`navbar ${scrolled ? 'navbar--scrolled' : ''}`}>
      <div className="navbar__inner">
        <a href="/" className="navbar__logo">
          <OrchestrateIcon size={28} />
          <span className="navbar__logo-text">Orchestraty</span>
        </a>

        <ul className="navbar__links">
          {navItems.map((item) => (
            <li key={item.label} className="navbar__item"
              onMouseEnter={() => item.dropdown && setActiveDropdown(item.label)}
              onMouseLeave={() => setActiveDropdown(null)}>
              <a href={item.href || '#'} className="navbar__link">
                {item.label}
                {item.dropdown && (
                  <svg className={`navbar__chevron ${activeDropdown === item.label ? 'navbar__chevron--open' : ''}`} width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                )}
              </a>
              {item.dropdown && activeDropdown === item.label && (
                <div className="navbar__dropdown">
                  {item.dropdown.map((d) => (
                    <a key={d.label} href={d.href} className="navbar__dropdown-item">
                      <span className="navbar__dropdown-label">{d.label}</span>
                      {d.description && <span className="navbar__dropdown-desc">{d.description}</span>}
                    </a>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>

        <div className="navbar__cta">
          <a href="/app/login" className="navbar__cta-link">Sign in</a>
          <a href="/app/login" className="navbar__download-btn">
            Get Started
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M2.5 7h9M7 2.5L11.5 7 7 11.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </a>
        </div>

        <button className="navbar__hamburger" onClick={() => setMobileOpen(!mobileOpen)} aria-label="Menu">
          <span className="navbar__hamburger-line"></span>
          <span className="navbar__hamburger-line"></span>
          <span className="navbar__hamburger-line"></span>
        </button>
      </div>

      {mobileOpen && (
        <div className="navbar__mobile">
          {navItems.map((item) => (
            <div key={item.label}>
              <a href={item.href || '#'} className="navbar__mobile-link" onClick={() => setMobileOpen(false)}>{item.label}</a>
              {item.dropdown?.map((d) => (
                <a key={d.label} href={d.href} className="navbar__mobile-sublink" onClick={() => setMobileOpen(false)}>{d.label}</a>
              ))}
            </div>
          ))}
          <a href="/app/login" className="navbar__download-btn navbar__mobile-download" onClick={() => setMobileOpen(false)}>Get Started</a>
        </div>
      )}
    </nav>
  );
};

export default Navbar;
