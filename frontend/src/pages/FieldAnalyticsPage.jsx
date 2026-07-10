import FieldAnalyticsWorkspace from '../components/Field/FieldAnalyticsWorkspace';

export default function FieldAnalyticsPage({ fieldId, onBack }) {
  return (
    <div className="h-full flex flex-col">
      <FieldAnalyticsWorkspace fieldId={fieldId} onBack={onBack} />
    </div>
  );
}
