import { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Layout/Sidebar';
import Header from './components/Layout/Header';
import DashboardPage from './pages/DashboardPage';
import FieldsPage from './pages/FieldsPage';
import FieldDetailPage from './pages/FieldDetailPage';
import AlertsPage from './pages/AlertsPage';
import EnterprisesPage from './pages/EnterprisesPage';
import EnterpriseDetailPage from './pages/EnterpriseDetailPage';
import { getCachedEnterprises } from './api/client';

export default function App() {
  const [view, setView] = useState('dashboard');
  const [selectedFieldId, setSelectedFieldId] = useState(null);
  const [selectedEnterpriseId, setSelectedEnterpriseId] = useState(null);
  const [enterprises, setEnterprises] = useState([]);

  useEffect(() => {
    getCachedEnterprises()
      .then(setEnterprises)
      .catch(err => console.error('Ошибка загрузки предприятий:', err));
  }, []);

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
        break;
      case 'fields':
        setView('fields');
        setSelectedFieldId(null);
        break;
      case 'field':
        setSelectedFieldId(id);
        setView('field-detail');
        break;
      case 'alerts':
        setView('alerts');
        setSelectedFieldId(null);
        break;
      case 'enterprises':
        setView('enterprises');
        setSelectedFieldId(null);
        setSelectedEnterpriseId(null);
        break;
      case 'enterprise':
        setSelectedEnterpriseId(id);
        setView('fields');
        break;
      case 'enterprise-detail':
        setSelectedEnterpriseId(id);
        setView('enterprise-detail');
        break;
      default:
        setView('dashboard');
    }
  }, []);

  const getHeaderInfo = () => {
    switch (view) {
      case 'dashboard':   return { title: 'Главная' };
      case 'fields':      return { title: 'Поля' };
      case 'field-detail': return { title: 'Поле', subtitle: selectedFieldId ? `#${selectedFieldId}` : null };
      case 'alerts':      return { title: 'Предупреждения' };
      case 'enterprise-detail': return { title: 'Предприятие', subtitle: selectedEnterpriseId ? `#${selectedEnterpriseId}` : null };
      default:            return { title: 'AgroSat' };
    }
  };

  const activeView = view === 'field-detail' ? 'fields'
    : view === 'enterprise-detail' ? 'enterprises'
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
