import FieldAnalyticsWorkspace from '../components/Field/FieldAnalyticsWorkspace';

export default function FieldAnalyticsPage({ fieldId, onBack }) {
  return (
    <main className="flex h-full min-w-0 flex-col overflow-hidden" aria-label="Аналитика поля">
      <FieldAnalyticsWorkspace fieldId={fieldId} onBack={onBack} />
    </main>
  );
}
