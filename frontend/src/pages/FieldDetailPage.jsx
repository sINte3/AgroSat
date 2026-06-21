import FieldDetail from '../components/Field/FieldDetail';

export default function FieldDetailPage({ fieldId, onBack }) {
  return (
    <div className="h-full flex flex-col">
      <FieldDetail fieldId={fieldId} onBack={onBack} />
    </div>
  );
}
