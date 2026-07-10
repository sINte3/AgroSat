import { useState, useEffect, useCallback } from 'react';
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import PrivateRoute from './components/Routing/PrivateRoute';
import Sidebar from './components/Layout/Sidebar';
import Header from './components/Layout/Header';
import DashboardPage from './pages/DashboardPage';
import FieldsPage from './pages/FieldsPage';
import FieldDetailPage from './pages/FieldDetailPage';
import FieldAnalyticsPage from './pages/FieldAnalyticsPage';
import AlertsPage from './pages/AlertsPage';
import EnterprisesPage from './pages/EnterprisesPage';
import EnterpriseDetailPage from './pages/EnterpriseDetailPage';
import ReportsPage from './pages/ReportsPage';
import LoginPage from './pages/LoginPage';
import UnauthorizedPage from './pages/UnauthorizedPage';
import { getCachedEnterprises } from './api/client';

const PATH_VIEW_MAP = {
  '/dashboard': 'dashboard',
  '/fields': 'fields',
  '/alerts': 'alerts',
  '/enterprises': 'enterprises',
  '/reports': 'reports',
};

function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const pathView = PATH_VIEW_MAP[location.pathname] || 'dashboard';
  const [view, setView] = useState(pathView);
  const [selectedFieldId, setSelectedFieldId] = useState(null);
  const [selectedEnterpriseId, setSelectedEnterpriseId] = useState(null);
  const [enterprises, setEnterprises] = useState([]);

  useEffect(() => {
    getCachedEnterprises()
      .then(setEnterprises)
      .catch(() => console.error('Ошибка загрузки предприятий'));
  }, []);

  // Sync view from URL changes (back/forward, manual URL entry, refresh)
  // Only override local state when the URL is a known top-level path — ignore
  // detail/internal views so back-button from field-detail goes to /fields.
  useEffect(() => {
    if (PATH_VIEW_MAP[location.pathname]) {
      setView(pathView);
      setSelectedFieldId(null);
      setSelectedEnterpriseId(null);
    }
  }, [location.pathname, pathView]);

  const handleFieldClick = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
    setView('field-detail');
  }, []);

  const handleFieldHighlight = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
  }, []);

  const handleNavigate = useCallback((target, id) => {
    switch (target) {
      case 'dashboard':
        setView('dashboard');
        setSelectedFieldId(null);
        setSelectedEnterpriseId(null);
        navigate('/dashboard', { replace: true });
        break;
      case 'fields':
        setView('fields');
        setSelectedFieldId(null);
        navigate('/fields', { replace: true });
        break;
      case 'field':
        setSelectedFieldId(id);
        setView('field-detail');
        break;
      case 'field-analytics':
        setSelectedFieldId(id);
        setView('field-analytics');
        break;
      case 'alerts':
        setView('alerts');
        setSelectedFieldId(null);
        navigate('/alerts', { replace: true });
        break;
      case 'enterprises':
        setView('enterprises');
        setSelectedFieldId(null);
        setSelectedEnterpriseId(null);
        navigate('/enterprises', { replace: true });
        break;
      case 'enterprise':
        setSelectedEnterpriseId(id);
        setView('fields');
        break;
      case 'enterprise-detail':
        setSelectedEnterpriseId(id);
        setView('enterprise-detail');
        break;
      case 'reports':
        setView('reports');
        setSelectedFieldId(null);
        setSelectedEnterpriseId(null);
        navigate('/reports', { replace: true });
        break;
      default:
        setView('dashboard');
    }
  }, [navigate]);

  const getHeaderInfo = () => {
    switch (view) {
      case 'dashboard':   return { title: 'Главная' };
      case 'fields':      return { title: 'Поля' };
      case 'field-detail': return { title: 'Поле', subtitle: selectedFieldId ? `#${selectedFieldId}` : null };
      case 'field-analytics': return { title: 'Аналитика поля', subtitle: selectedFieldId ? `#${selectedFieldId}` : null };
      case 'alerts':      return { title: 'Предупреждения' };
      case 'reports':     return { title: 'Отчёты' };
      case 'enterprise-detail': return { title: 'Предприятие', subtitle: selectedEnterpriseId ? `#${selectedEnterpriseId}` : null };
      default:            return { title: 'AgroSat' };
    }
  };

  const activeView = view === 'field-detail' || view === 'field-analytics' ? 'fields'
    : view === 'enterprise-detail' ? 'enterprises'
    : view === 'reports' ? 'reports'
    : view;

  const renderContent = () => {
    switch (view) {
      case 'fields':
        return <FieldsPage onFieldClick={handleFieldClick} onNavigate={handleNavigate} enterpriseId={selectedEnterpriseId} />;
      case 'field-detail':
        return (
          <FieldDetailPage
            fieldId={selectedFieldId}
            onBack={() => { setSelectedFieldId(null); setView('fields'); }}
          />
        );
      case 'field-analytics':
        return (
          <FieldAnalyticsPage
            fieldId={selectedFieldId}
            onBack={() => { setSelectedFieldId(null); setView('fields'); }}
          />
        );
      case 'alerts':
        return <AlertsPage onFieldClick={handleFieldClick} onFieldHighlight={handleFieldHighlight} />;
      case 'enterprises':
        return <EnterprisesPage onNavigate={handleNavigate} />;
      case 'enterprise-detail':
        return (
          <EnterpriseDetailPage
            enterpriseId={selectedEnterpriseId}
            onBack={() => { setSelectedEnterpriseId(null); setView('enterprises'); }}
          />
        );
      case 'reports':
        return <ReportsPage />;
      case 'dashboard':
      default:
        return (
          <DashboardPage
            onNavigate={handleNavigate}
            onFieldClick={handleFieldClick}
            onFieldHighlight={handleFieldHighlight}
          />
        );
    }
  };

  return (
    <div className="flex h-screen bg-agro-dark overflow-hidden">
      <Sidebar
        activeView={activeView}
        onNavigate={handleNavigate}
        enterprises={enterprises}
      />

      <main className="flex-1 overflow-hidden relative">
        <Header
          {...getHeaderInfo()}
          currentView={view}
        />
        {renderContent()}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/unauthorized" element={<UnauthorizedPage />} />

          <Route
            element={
              <PrivateRoute
                allowedRoles={['admin', 'manager', 'agronomist', 'viewer']}
              />
            }
          >
            <Route path="*" element={<AppLayout />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
