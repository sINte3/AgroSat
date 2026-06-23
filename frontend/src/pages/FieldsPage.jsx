import { useState, useEffect, useCallback } from 'react';
import FieldMap from '../components/Map/FieldMap';
import FieldListPanel from '../components/Map/FieldListPanel';
import { getCachedEnterprises, getFields } from '../api/client';

export default function FieldsPage({ onFieldClick, onNavigate, enterpriseId }) {
  const [fields, setFields] = useState([]);
  const [enterprises, setEnterprises] = useState([]);
  const [selectedFieldId, setSelectedFieldId] = useState(null);
  const [highlightedFieldId, setHighlightedFieldId] = useState(null);

  useEffect(() => {
    getFields({ include_ndvi: true })
      .then(setFields)
      .catch(err => console.error('Error loading fields:', err));

    getCachedEnterprises()
      .then(setEnterprises)
      .catch(err => console.error('Error loading enterprises:', err));
  }, []);

  const handleFieldSelect = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
  }, []);

  const handleFieldHover = useCallback((fieldId) => {
    setHighlightedFieldId(fieldId);
  }, []);

  const handleOpenFullDetail = useCallback((fieldId) => {
    if (onFieldClick) onFieldClick(fieldId);
  }, [onFieldClick]);

  const handleMapFieldClick = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
  }, []);

  return (
    <div className="relative w-full h-full">
      <FieldMap
        onFieldSelect={handleMapFieldClick}
        highlightedFieldId={highlightedFieldId}
        selectedFieldId={selectedFieldId}
        enterpriseId={enterpriseId}
      />
      <FieldListPanel
        fields={fields}
        enterprises={enterprises}
        selectedFieldId={selectedFieldId}
        highlightedFieldId={highlightedFieldId}
        onFieldSelect={handleFieldSelect}
        onFieldHover={handleFieldHover}
        onOpenFullDetail={handleOpenFullDetail}
        onNavigate={onNavigate}
        enterpriseId={enterpriseId}
      />
    </div>
  );
}