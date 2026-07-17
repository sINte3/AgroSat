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
import FieldAttentionPage from './pages/FieldAttentionPage';
import FieldInspectionsPage from './pages/FieldInspectionsPage';
import LoginPage from './pages/LoginPage';
import UnauthorizedPage from './pages/UnauthorizedPage';
import { getCachedEnterprises } from './api/client';

const PATH_VIEW_MAP = {
  '/dashboard': 'dashboard',
  '/fields': 'fields',
  '/alerts': 'alerts',
  '/attention': 'field-attention',
  '/inspections': 'field-inspections',
  '/enterprises': 'enterprises',
  '/reports': 'reports',
  '/login': 'login',
  '/unauthorized': 'unauthorized',
};

const parsePositiveId = (value) => {
  const stringValue = String(value);
  if (!/^\d+$/.test(stringValue)) return null;

  const id = Number(stringValue);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
};

const resolvePathname = (pathname) => {
  const inspectionMatch = pathname.match(/^\/inspections\/(\d+)$/);
  if (inspectionMatch) {
    const selectedInspectionId = parsePositiveId(inspectionMatch[1]);
    if (selectedInspectionId) return { view: 'field-inspection-detail', selectedInspectionId, selectedFieldId: null, selectedEnterpriseId: null };
  }
  const analyticsMatch = pathname.match(/^\/fields\/(\d+)\/analytics$/);
  if (analyticsMatch) {
    const selectedFieldId = parsePositiveId(analyticsMatch[1]);
    if (selectedFieldId) {
      return { view: 'field-analytics', selectedFieldId, selectedEnterpriseId: null };
    }
  }

  const fieldMatch = pathname.match(/^\/fields\/(\d+)$/);
  if (fieldMatch) {
    const selectedFieldId = parsePositiveId(fieldMatch[1]);
    if (selectedFieldId) {
      return { view: 'field-detail', selectedFieldId, selectedEnterpriseId: null };
    }
  }

  const enterpriseMatch = pathname.match(/^\/enterprises\/(\d+)$/);
  if (enterpriseMatch) {
    const selectedEnterpriseId = parsePositiveId(enterpriseMatch[1]);
    if (selectedEnterpriseId) {
      return { view: 'enterprise-detail', selectedFieldId: null, selectedEnterpriseId };
    }
  }

  return {
    view: PATH_VIEW_MAP[pathname] || 'dashboard',
    selectedFieldId: null,
    selectedEnterpriseId: null,
    selectedInspectionId: null,
  };
};

function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const initialRoute = resolvePathname(location.pathname);
  const [view, setView] = useState(initialRoute.view);
  const [selectedFieldId, setSelectedFieldId] = useState(initialRoute.selectedFieldId);
  const [selectedEnterpriseId, setSelectedEnterpriseId] = useState(initialRoute.selectedEnterpriseId);
  const [selectedInspectionId, setSelectedInspectionId] = useState(initialRoute.selectedInspectionId);
  const [enterprises, setEnterprises] = useState([]);
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);

  useEffect(() => {
    getCachedEnterprises()
      .then(setEnterprises)
      .catch(() => setEnterprises([]));
  }, []);

  // Sync view and detail selection from URL changes (back/forward, manual entry, refresh).
  useEffect(() => {
    const route = resolvePathname(location.pathname);
    setView(route.view);
    setSelectedFieldId(route.selectedFieldId);
    setSelectedEnterpriseId(route.selectedEnterpriseId);
    setSelectedInspectionId(route.selectedInspectionId);
  }, [location.pathname]);

  const handleFieldClick = useCallback((fieldId) => {
    const validFieldId = parsePositiveId(fieldId);
    if (!validFieldId) return;

    setSelectedFieldId(validFieldId);
    setSelectedEnterpriseId(null);
    setView('field-detail');
    navigate(`/fields/${validFieldId}`);
  }, [navigate]);

  const handleFieldHighlight = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
  }, []);

  const handleNavigate = useCallback((target, id) => {
    setMobileNavigationOpen(false);
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
      case 'field-detail': {
        const validFieldId = parsePositiveId(id);
        if (!validFieldId) break;

        setSelectedFieldId(validFieldId);
        setSelectedEnterpriseId(null);
        setView('field-detail');
        navigate(`/fields/${validFieldId}`);
        break;
      }
      case 'field-analytics': {
        const validFieldId = parsePositiveId(id);
        if (!validFieldId) break;

        setSelectedFieldId(validFieldId);
        setSelectedEnterpriseId(null);
        setView('field-analytics');
        navigate(`/fields/${validFieldId}/analytics`);
        break;
      }
      case 'alerts':
        setView('alerts');
        setSelectedFieldId(null);
        navigate('/alerts', { replace: true });
        break;
      case 'field-attention':
        setView('field-attention');
        setSelectedFieldId(null);
        setSelectedEnterpriseId(null);
        navigate('/attention');
        break;
      case 'field-inspections':
        setView('field-inspections'); setSelectedInspectionId(null); navigate('/inspections'); break;
      case 'field-inspection-detail': {
        const validInspectionId = parsePositiveId(id); if (!validInspectionId) break;
        setView('field-inspection-detail'); setSelectedInspectionId(validInspectionId); navigate(`/inspections/${validInspectionId}`); break;
      }
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
      case 'enterprise-detail': {
        const validEnterpriseId = parsePositiveId(id);
        if (!validEnterpriseId) break;

        setSelectedFieldId(null);
        setSelectedEnterpriseId(validEnterpriseId);
        setView('enterprise-detail');
        navigate(`/enterprises/${validEnterpriseId}`);
        break;
      }
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
      case 'dashboard':   return { title: 'Сегодня', subtitle: 'Оперативная сводка по доступным данным' };
      case 'fields':      return { title: 'Поля' };
      case 'field-detail': return { title: 'Поле', subtitle: selectedFieldId ? `#${selectedFieldId}` : null };
      case 'field-analytics': return { title: 'Аналитика поля', subtitle: selectedFieldId ? `#${selectedFieldId}` : null };
      case 'alerts':      return { title: 'Предупреждения' };
      case 'field-attention': return { title: 'Требуют внимания' };
      case 'field-inspections': return { title: 'Осмотры полей' };
      case 'field-inspection-detail': return { title: 'Осмотры полей', subtitle: selectedInspectionId ? `Осмотр #${selectedInspectionId}` : null };
      case 'reports':     return { title: 'Отчёты' };
      case 'enterprise-detail': return { title: 'Предприятие', subtitle: selectedEnterpriseId ? `#${selectedEnterpriseId}` : null };
      default:            return { title: 'AgroSat' };
    }
  };

  const activeView = view === 'field-detail' || view === 'field-analytics' ? 'fields'
    : view === 'field-inspection-detail' ? 'field-inspections'
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
            onBack={() => { setSelectedFieldId(null); setSelectedEnterpriseId(null); setView('fields'); navigate('/fields'); }}
          />
        );
      case 'field-analytics':
        return (
          <FieldAnalyticsPage
            fieldId={selectedFieldId}
            onBack={() => { setSelectedFieldId(null); setSelectedEnterpriseId(null); setView('fields'); navigate('/fields'); }}
          />
        );
      case 'alerts':
        return <AlertsPage onFieldClick={handleFieldClick} onFieldHighlight={handleFieldHighlight} />;
      case 'field-attention':
        return <FieldAttentionPage onNavigate={handleNavigate} enterprises={enterprises} />;
      case 'field-inspections':
      case 'field-inspection-detail':
        return <FieldInspectionsPage onNavigate={handleNavigate} enterprises={enterprises} selectedInspectionId={selectedInspectionId} />;
      case 'enterprises':
        return <EnterprisesPage onNavigate={handleNavigate} />;
      case 'enterprise-detail':
        return (
          <EnterpriseDetailPage
            enterpriseId={selectedEnterpriseId}
            onBack={() => { setSelectedFieldId(null); setSelectedEnterpriseId(null); setView('enterprises'); navigate('/enterprises'); }}
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
    <div className="flex h-screen overflow-hidden bg-slate-50">
      <Sidebar
        activeView={activeView}
        onNavigate={handleNavigate}
        enterprises={enterprises}
        mobileOpen={mobileNavigationOpen}
        onMobileClose={() => setMobileNavigationOpen(false)}
      />

      <main className="flex-1 overflow-hidden relative">
        <Header
          {...getHeaderInfo()}
          currentView={view}
          mobileNavigationOpen={mobileNavigationOpen}
          onMobileNavigationToggle={() => setMobileNavigationOpen(open => !open)}
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
