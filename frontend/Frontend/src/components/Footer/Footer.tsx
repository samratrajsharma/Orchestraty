import React from 'react';
import './Footer.css';
import OrchestrateIcon from '../Logo/AntigravityLogo';

const footerLinks = [
  { heading: 'Platform', links: [
    {label: 'Data Pipelines', href: '/app/structuring'},
    {label: 'AI Labeling', href: '/app/ai-labeling'},
    {label: 'Image Intelligence', href: '/app/images'},
    {label: 'Workflows', href: '/app/workflows'},
    {label: 'LLM Config', href: '/app/llm'},
  ]},
  { heading: 'Use Cases', links: [
    {label: 'NLP Datasets', href: '#use-cases'},
    {label: 'Computer Vision', href: '#use-cases'},
    {label: 'Model Evaluation', href: '#use-cases'},
    {label: 'Automation', href: '#use-cases'},
  ]},
  { heading: 'Resources', links: [
    {label: 'Documentation', href: '#'},
    {label: 'API Reference', href: '#'},
    {label: 'GitHub', href: '#'},
    {label: 'Changelog', href: '#'},
  ]},
  { heading: 'Company', links: [
    {label: 'About', href: '#'},
    {label: 'Contact', href: 'mailto:business@orchestraty.com'},
    {label: 'Privacy Policy', href: '#'},
    {label: 'Terms', href: '#'},
  ]},
];

const Footer: React.FC = () => {
  return (
    <footer className="footer">
      <div className="container">
        <div className="footer__top">
          <div className="footer__brand">
            <a href="/" className="footer__logo">
              <OrchestrateIcon size={28} />
              <span className="footer__logo-name">Orchestraty</span>
            </a>
            <p className="footer__tagline">The AI Operating System for production-ready data.</p>
            <div className="footer__social">
              {['GH', 'X', 'in'].map((s, i) => (
                <a key={i} href="#" className="footer__social-link" aria-label={s}><span>{s}</span></a>
              ))}
            </div>
          </div>
          <div className="footer__links">
            {footerLinks.map((group) => (
              <div key={group.heading} className="footer__link-group">
                <h4 className="footer__link-heading">{group.heading}</h4>
                <ul className="footer__link-list">
                  {group.links.map((link) => (
                    <li key={link.label}><a href={link.href} className="footer__link">{link.label}</a></li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
        <div className="footer__bottom">
          <p className="footer__copy">\u00A9 {new Date().getFullYear()} Orchestraty. All rights reserved.</p>
          <div className="footer__bottom-links">
            <a href="#" className="footer__bottom-link">Privacy</a>
            <a href="#" className="footer__bottom-link">Terms</a>
          </div>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
