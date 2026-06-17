import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import ExternalSignals from './pages/ExternalSignals';
import Strategies from './pages/Strategies';
import RiskManagement from './pages/RiskManagement';
import Backtest from './pages/Backtest';
import Settings from './pages/Settings';
import TradingJournal from './pages/TradingJournal';
import Insights from './pages/Insights';

function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '') || undefined}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="options-sheet" element={<ExternalSignals />} />
          <Route path="strategies" element={<Strategies />} />
          <Route path="risk" element={<RiskManagement />} />
          <Route path="backtest" element={<Backtest />} />
          <Route path="insights" element={<Insights />} />
          <Route path="journal" element={<TradingJournal />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
