import React from 'react';
import './App.css';
import Navbar from './components/Navbar/Navbar';
import Hero from './components/Hero/Hero';
import Features from './components/Features/Features';
import UseCases from './components/UseCases/UseCases';
import Pricing from './components/Pricing/Pricing';
import Footer from './components/Footer/Footer';

const App: React.FC = () => {
  return (
    <div className="app">
      <Navbar />
      <main>
        <Hero />
        <Features />
        <UseCases />
        <Pricing />
      </main>
      <Footer />
    </div>
  );
};

export default App;
