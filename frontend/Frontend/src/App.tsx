import React from 'react';
import './App.css';
import { useAuth } from './context/AuthContext';
import Navbar from './components/Navbar/Navbar';
import Hero from './components/Hero/Hero';
import Features from './components/Features/Features';
import UseCases from './components/UseCases/UseCases';
import Pricing from './components/Pricing/Pricing';
import Footer from './components/Footer/Footer';
import UserHome from './components/UserHome/UserHome';

const App: React.FC = () => {
  const { user } = useAuth();

  return (
    <div className="app">
      <Navbar />
      {user ? (
        // Logged-in visitors get a personalized home with their usage snapshot.
        <main>
          <UserHome />
        </main>
      ) : (
        // Anonymous visitors get the marketing landing page.
        <main>
          <Hero />
          <Features />
          <UseCases />
          <Pricing />
        </main>
      )}
      <Footer />
    </div>
  );
};

export default App;
