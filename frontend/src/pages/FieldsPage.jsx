import { useState, useEffect, useCallback } from 'react';
import FieldMap from '../components/Map/FieldMap';
import FieldListPanel from '../components/Map/FieldListPanel';
import CreateFieldModal from '../components/Map/CreateFieldModal';
import { getCachedEnterprises, getFields, createField, clearGeoCache } from '../api/client';
import { useAuth } from '../context/AuthContext';

export default function FieldsPage({ onFieldClick, onNavigate, enterpriseId }) {
  const { user: currentUser } = useAuth();

  const [fields, setFields] = useState([]);
  const [enterprises, setEnterprises] = useState([]);
  const [selectedFieldId, setSelectedFieldId] = useState(null);
  const [highlightedFieldId, setHighlightedFieldId] = useState(null);

  // Draw / create field state
  const [isDrawingMode, setIsDrawingMode] = useState(false);
  const [drawnGeometry, setDrawnGeometry] = useState(null);
  const [createError, setCreateError] = useState(null);
  const [savingField, setSavingField] = useState(false);
  const [mapReloadKey, setMapReloadKey] = useState(0);

  const canCreateField =
    currentUser && ['admin', 'manager', 'agronomist'].includes(currentUser.role);

  useEffect(() => {
    getFields({ include_ndvi: true })
      .then(setFields)
      .catch((err) => console.error('Error loading fields:', err));

    getCachedEnterprises()
      .then(setEnterprises)
      .catch((err) => console.error('Error loading enterprises:', err));
  }, []);

  const handleFieldSelect = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
  }, []);

  const handleFieldHover = useCallback((fieldId) => {
    setHighlightedFieldId(fieldId);
  }, []);

  const handleOpenFullDetail = useCallback(
    (fieldId) => {
      if (onFieldClick) onFieldClick(fieldId);
    },
    [onFieldClick],
  );

  const handleMapFieldClick = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
  }, []);

  // ─── Drawing flow ──────────────────────────────────────────────────────────

  function startDrawing() {
    setSelectedFieldId(null);
    setCreateError(null);
    setDrawnGeometry(null);
    setIsDrawingMode(true);
  }

  function cancelDrawing() {
    setIsDrawingMode(false);
    setDrawnGeometry(null);
    setCreateError(null);
  }

  function handleDrawComplete(geometry) {
    if (!geometry || geometry.type !== 'Polygon') return;
    setDrawnGeometry(geometry);
    setIsDrawingMode(false);
    setSelectedFieldId(null);
  }

  // ─── Save flow ─────────────────────────────────────────────────────────────

  async function handleCreateField(payload) {
    setSavingField(true);
    setCreateError(null);
    try {
      const created = await createField(payload);
      clearGeoCache();

      const refreshedFields = await getFields({ include_ndvi: true });
      setFields(Array.isArray(refreshedFields) ? refreshedFields : []);

      setDrawnGeometry(null);
      setIsDrawingMode(false);
      setSelectedFieldId(created?.id ?? null);
      setMapReloadKey((prev) => prev + 1);
    } catch (err) {
      // Extract a safe Russian message; never render raw objects or HTML
      let msg = 'Не удалось сохранить поле. Проверьте данные и попробуйте снова.';
      const detail = err?.response?.data?.detail;
      if (typeof detail === 'string' && detail.length < 300) {
        msg = detail;
      } else if (typeof err?.message === 'string' && err.message.length < 200) {
        msg = err.message;
      }
      setCreateError(msg);
    } finally {
      setSavingField(false);
    }
  }

  return (
    <div className="relative w-full h-full">
      <FieldMap
        key={mapReloadKey}
        onFieldSelect={handleMapFieldClick}
        highlightedFieldId={highlightedFieldId}
        selectedFieldId={selectedFieldId}
        enterpriseId={enterpriseId}
        onDrawComplete={handleDrawComplete}
        isDrawingMode={isDrawingMode}
        setIsDrawingMode={setIsDrawingMode}
        canDraw={canCreateField}
      />

      {/* Draw controls overlay */}
      {canCreateField && (
        <div className="absolute top-3 left-3 z-10 flex flex-col gap-2">
          {!isDrawingMode ? (
            <button
              id="add-field-btn"
              onClick={startDrawing}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold text-white bg-green-600 hover:bg-green-700 shadow-lg transition-colors backdrop-blur-sm"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Добавить поле
            </button>
          ) : (
            <div className="flex flex-col gap-2">
              <div className="px-3 py-2 rounded-lg text-xs font-medium text-white bg-blue-600/90 shadow backdrop-blur-sm max-w-xs leading-snug">
                ✏️ Нарисуйте контур поля на карте и завершите двойным кликом.
              </div>
              <button
                id="cancel-draw-btn"
                onClick={cancelDrawing}
                className="px-3 py-2 rounded-lg text-sm font-medium text-white bg-slate-600/90 hover:bg-slate-700 shadow transition-colors backdrop-blur-sm"
              >
                Отменить рисование
              </button>
            </div>
          )}
        </div>
      )}

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

      {/* Create field modal — shown when drawing is complete */}
      {drawnGeometry && (
        <CreateFieldModal
          geometry={drawnGeometry}
          enterprises={enterprises}
          currentUser={currentUser}
          defaultEnterpriseId={enterpriseId}
          saving={savingField}
          error={createError}
          onCancel={cancelDrawing}
          onSubmit={handleCreateField}
        />
      )}
    </div>
  );
}