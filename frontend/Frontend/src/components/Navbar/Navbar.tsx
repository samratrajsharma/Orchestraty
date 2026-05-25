import React, { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import './Navbar.css';
import OrchestrateIcon from '../Logo/AntigravityLogo';
import { useAuth } from '../../context/AuthContext';

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

/** Render a dropdown/nav target as a router Link for internal routes, or a
 *  plain anchor for hash links and external URLs. */
function NavTarget({ to, className, children, onClick }: {
  to: string; className?: string; children: React.ReactNode; onClick?: () => void;
}) {
  const isInternal = to.startsWith('/');
  if (isInternal) {
    return <Link to={to} className={className} onClick={onClick}>{children}</Link>;
  }
  return <a href={to} className={className} onClick={onClick}>{children}</a>;
}

const Navbar: React.FC = () => {
  const [activeDropdown, setActiveDropdown] = useState<string | null>(null);
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user } = useAuth();
  // Hover-intent: small delay before closing so the cursor can travel into
  // the dropdown without it vanishing.
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const h = () => setScrolled(window.scrollY > 10);
    window.addEventListener('scroll', h);
    return () => window.removeEventListener('scroll', h);
  }, []);

  const openDropdown = (label: string) => {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
    setActiveDropdown(label);
  };
  const scheduleClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => setActiveDropdown(null), 140);
  };

  return (
    <nav className={`navbar ${scrolled ? 'navbar--scrolled' : ''}`}>
      <div className="navbar__inner">
        <Link to="/" className="navbar__logo">
          <OrchestrateIcon size={28} />
          <span className="navbar__logo-text">Orchestraty</span>
        </Link>

        <ul className="navbar__links">
          {navItems.map((item) => (
            <li key={item.label} className="navbar__item"
              onMouseEnter={() => item.dropdown && openDropdown(item.label)}
              onMouseLeave={() => item.dropdown && scheduleClose()}>
              {item.dropdown ? (
                <button type="button" className="navbar__link navbar__link--button"
                  onClick={() => setActiveDropdown(activeDropdown === item.label ? null : item.label)}>
                  {item.label}
                  <svg className={`navbar__chevron ${activeDropdown === item.label ? 'navbar__chevron--open' : ''}`} width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
              ) : (
                <a href={item.href || '#'} className="navbar__link">{item.label}</a>
              )}
              {item.dropdown && activeDropdown === item.label && (
                <div className="navbar__dropdown">
                  {item.dropdown.map((d) => (
                    <NavTarget key={d.label} to={d.href} className="navbar__dropdown-item"
                      onClick={() => setActiveDropdown(null)}>
                      <span className="navbar__dropdown-label">{d.label}</span>
                      {d.description && <span className="navbar__dropdown-desc">{d.description}</span>}
                    </NavTarget>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>

        <div className="navbar__cta">
          {user ? (
            <Link to="/app" className="navbar__download-btn">
              Open Dashboard
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2.5 7h9M7 2.5L11.5 7 7 11.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </Link>
          ) : (
            <>
              <Link to="/app/login" className="navbar__cta-link">Sign in</Link>
              <Link to="/app/register" className="navbar__download-btn">
                Get Started
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M2.5 7h9M7 2.5L11.5 7 7 11.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </Link>
            </>
          )}
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
              {item.href ? (
                <a href={item.href} className="navbar__mobile-link" onClick={() => setMobileOpen(false)}>{item.label}</a>
              ) : (
                <span className="navbar__mobile-link">{item.label}</span>
              )}
              {item.dropdown?.map((d) => (
                <NavTarget key={d.label} to={d.href} className="navbar__mobile-sublink"
                  onClick={() => setMobileOpen(false)}>{d.label}</NavTarget>
              ))}
            </div>
          ))}
          {user ? (
            <Link to="/app" className="navbar__download-btn navbar__mobile-download" onClick={() => setMobileOpen(false)}>Open Dashboard</Link>
          ) : (
            <>
              <Link to="/app/login" className="navbar__mobile-link" onClick={() => setMobileOpen(false)}>Sign in</Link>
              <Link to="/app/register" className="navbar__download-btn navbar__mobile-download" onClick={() => setMobileOpen(false)}>Get Started</Link>
            </>
          )}
        </div>
      )}
    </nav>
  );
};

export default Navbar;
