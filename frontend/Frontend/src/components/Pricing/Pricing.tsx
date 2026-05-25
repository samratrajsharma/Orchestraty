import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import './Pricing.css';

interface Plan {
  name: string;
  /** Monthly price in USD. null = not a numeric plan (Free / Custom). */
  monthlyPrice: number | null;
  /** Per-month price when billed annually (already discounted). */
  annualPrice: number | null;
  /** Shown instead of a number for non-numeric plans. */
  priceLabel?: string;
  description: string;
  cta: string;
  ctaLink: string;
  ctaStyle: 'primary' | 'outline';
  features: string[];
  popular: boolean;
}

const plans: Plan[] = [
  {
    name: 'Community',
    monthlyPrice: 0, annualPrice: 0, priceLabel: 'Free',
    description: 'For individual researchers and small experiments.',
    cta: 'Start Free', ctaLink: '/app/register', ctaStyle: 'outline',
    features: ['5 datasets', '3 LLM providers', 'Rule-based labeling', 'CSV/JSON export', 'Community support', 'Single user'],
    popular: false,
  },
  {
    name: 'Pro',
    monthlyPrice: 49, annualPrice: 39,
    description: 'For data teams building production ML pipelines.',
    cta: 'Start with Pro', ctaLink: '/app/register', ctaStyle: 'primary',
    features: ['Unlimited datasets', 'All LLM providers', 'AI labeling + propagation', 'Image pipeline (CLIP)', 'Workflow engine', 'Priority support', 'Up to 10 users'],
    popular: true,
  },
  {
    name: 'Enterprise',
    monthlyPrice: null, annualPrice: null, priceLabel: 'Custom',
    description: 'Maximum power for large organizations.',
    cta: 'Contact Sales', ctaLink: 'mailto:business@orchestraty.com', ctaStyle: 'outline',
    features: ['Everything in Pro', 'SSO & SAML', 'On-premise deployment', 'Custom model endpoints', 'Audit logs & compliance', 'SLA guarantee', 'Unlimited users', 'Dedicated support'],
    popular: false,
  },
];

const Pricing: React.FC = () => {
  const [annual, setAnnual] = useState(false);

  return (
    <section className="pricing section" id="pricing">
      <div className="container">
        <div className="pricing__header">
          <span className="pricing__eyebrow">Pricing</span>
          <h2 className="pricing__title">Simple, transparent pricing</h2>
          <p className="pricing__subtitle">Start free, scale as your data grows. All plans include the full Orchestraty platform.</p>
          <div className="pricing__toggle">
            <span className={!annual ? 'pricing__toggle-active' : ''}>Monthly</span>
            <button className={`pricing__toggle-btn ${annual ? 'pricing__toggle-btn--on' : ''}`} onClick={() => setAnnual(!annual)} aria-label="Toggle billing">
              <span className="pricing__toggle-thumb" />
            </button>
            <span className={annual ? 'pricing__toggle-active' : ''}>Annual <span className="pricing__save">Save 20%</span></span>
          </div>
        </div>
        <div className="pricing__grid">
          {plans.map((plan, i) => {
            // Resolve the price shown for the currently selected billing period.
            const numeric = plan.monthlyPrice !== null && plan.monthlyPrice > 0;
            const activePrice = annual ? plan.annualPrice : plan.monthlyPrice;
            const amount = plan.priceLabel && !numeric ? plan.priceLabel : `$${activePrice}`;
            const period = plan.priceLabel && !numeric ? '' : '/mo';
            const annualTotal = numeric && plan.annualPrice !== null ? plan.annualPrice * 12 : null;

            return (
              <div className={`pricing__card ${plan.popular ? 'pricing__card--popular' : ''}`} key={i}>
                {plan.popular && <div className="pricing__popular-badge">Most Popular</div>}
                <div className="pricing__card-header">
                  <h3 className="pricing__plan-name">{plan.name}</h3>
                  <div className="pricing__price">
                    <span className="pricing__amount">{amount}</span>
                    {period && <span className="pricing__period">{period}</span>}
                  </div>
                  <div className="pricing__billing-note">
                    {numeric && annual && annualTotal !== null
                      ? `Billed annually — $${annualTotal}/yr`
                      : numeric
                        ? 'Billed monthly'
                        : plan.name === 'Community' ? 'Free forever' : 'Tailored to your needs'}
                  </div>
                  <p className="pricing__plan-desc">{plan.description}</p>
                </div>
                {plan.ctaLink.startsWith('mailto:') ? (
                  <a href={plan.ctaLink} className={`pricing__cta pricing__cta--${plan.ctaStyle}`}>{plan.cta}</a>
                ) : (
                  <Link to={plan.ctaLink} className={`pricing__cta pricing__cta--${plan.ctaStyle}`}>{plan.cta}</Link>
                )}
                <ul className="pricing__features">
                  {plan.features.map((f, j) => (
                    <li key={j} className="pricing__feature">
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                        <path d="M3 8l3.5 3.5 6.5-7" stroke={plan.popular ? '#6366f1' : '#10b981'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
        <p className="pricing__enterprise">Questions? Contact us at <a href="mailto:business@orchestraty.com" className="pricing__enterprise-link">business@orchestraty.com</a></p>
      </div>
    </section>
  );
};

export default Pricing;
