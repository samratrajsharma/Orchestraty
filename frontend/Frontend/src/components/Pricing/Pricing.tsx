import React, { useState } from 'react';
import './Pricing.css';

const plans = [
  {
    name: 'Community', price: 'Free', period: 'forever',
    description: 'For individual researchers and small experiments.',
    cta: 'Start Free', ctaLink: '/app/register', ctaStyle: 'outline',
    features: ['5 datasets', '3 LLM providers', 'Rule-based labeling', 'CSV/JSON export', 'Community support', 'Single user'],
    popular: false,
  },
  {
    name: 'Pro', price: '$49', period: '/month',
    description: 'For data teams building production ML pipelines.',
    cta: 'Start with Pro', ctaLink: '/app', ctaStyle: 'primary',
    features: ['Unlimited datasets', 'All LLM providers', 'AI labeling + propagation', 'Image pipeline (CLIP)', 'Workflow engine', 'Priority support', 'Up to 10 users'],
    popular: true,
  },
  {
    name: 'Enterprise', price: 'Custom', period: '',
    description: 'Maximum power for large organizations.',
    cta: 'Contact Sales', ctaLink: '#', ctaStyle: 'outline',
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
          {plans.map((plan, i) => (
            <div className={`pricing__card ${plan.popular ? 'pricing__card--popular' : ''}`} key={i}>
              {plan.popular && <div className="pricing__popular-badge">Most Popular</div>}
              <div className="pricing__card-header">
                <h3 className="pricing__plan-name">{plan.name}</h3>
                <div className="pricing__price">
                  <span className="pricing__amount">{plan.price}</span>
                  <span className="pricing__period">{plan.period}</span>
                </div>
                <p className="pricing__plan-desc">{plan.description}</p>
              </div>
              <a href={plan.ctaLink} className={`pricing__cta pricing__cta--${plan.ctaStyle}`}>{plan.cta}</a>
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
          ))}
        </div>
        <p className="pricing__enterprise">Questions? Contact us at <a href="mailto:business@orchestraty.com" className="pricing__enterprise-link">business@orchestraty.com</a></p>
      </div>
    </section>
  );
};

export default Pricing;
