import React, { useState, useEffect } from 'react';
import apiClient from '../../api/client';

const WEATHER_CODES = {
  0: { icon: '☀️', label: 'Ясно' },
  1: { icon: '🌤️', label: 'Преимущественно ясно' },
  2: { icon: '⛅', label: 'Переменная облачность' },
  3: { icon: '☁️', label: 'Пасмурно' },
  45: { icon: '🌫️', label: 'Туман' },
  48: { icon: '🌫️', label: 'Изморозь' },
  51: { icon: '🌦️', label: 'Морось лёгкая' },
  53: { icon: '🌦️', label: 'Морось умеренная' },
  55: { icon: '🌧️', label: 'Морось сильная' },
  61: { icon: '🌧️', label: 'Дождь лёгкий' },
  63: { icon: '🌧️', label: 'Дождь умеренный' },
  65: { icon: '🌧️', label: 'Дождь сильный' },
  71: { icon: '🌨️', label: 'Снег лёгкий' },
  73: { icon: '🌨️', label: 'Снег умеренный' },
  75: { icon: '❄️', label: 'Снег сильный' },
  80: { icon: '🌦️', label: 'Ливень кратковременный' },
  81: { icon: '🌧️', label: 'Ливень умеренный' },
  82: { icon: '⛈️', label: 'Ливень сильный' },
  95: { icon: '⛈️', label: 'Гроза' },
  96: { icon: '⛈️', label: 'Гроза с градом' },
  99: { icon: '⛈️', label: 'Гроза с сильным градом' },
};

function getWeather(code) {
  return WEATHER_CODES[code] ?? { icon: '🌡️', label: 'Неизвестно' };
}

// Направление ветра в текст
function windDir(deg) {
  const dirs = ['С', 'СВ', 'В', 'ЮВ', 'Ю', 'ЮЗ', 'З', 'СЗ'];
  return dirs[Math.round(deg / 45) % 8];
}

// Форматировать дату прогноза
function fmtDay(dateStr, idx) {
  if (idx === 0) return 'Сегодня';
  if (idx === 1) return 'Завтра';
  const d = new Date(dateStr);
  return d.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' });
}

function LoadingSkeleton() {
  return (
    <div style={{ padding: '16px 0' }}>
      {[...Array(3)].map((_, i) => (
        <div key={i} style={{
          height: 48, marginBottom: 8, borderRadius: 8,
          background: '#e0e7e3'
        }} />
      ))}
    </div>
  );
}

export default function WeatherWidget({ fieldId }) {
  const [weather, setWeather] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!fieldId) return;
    setLoading(true);
    setError(null);
    apiClient.get(`/api/weather/field/${fieldId}`)
      .then(res => setWeather(res.data))
      .catch(err => {
        const status = err.response?.status;
        if (status === 404 || status === 422) {
          setError('Координаты поля не заданы — погода недоступна');
        } else {
          console.error('Ошибка загрузки погоды:', err);
          setError('Не удалось загрузить данные погоды');
        }
      })
      .finally(() => setLoading(false));
  }, [fieldId]);

  if (loading) return <LoadingSkeleton />;

  if (error) return (
    <div style={{ textAlign: 'center', padding: '24px 16px', color: '#ef4444', fontSize: 14 }}>
      {error}
    </div>
  );

  if (!weather) return null;

  const { current, forecast } = weather;
  const currentWeather = getWeather(current.weather_code);

  return (
    <div style={{ color: '#1a2e23' }}>
      {/* Текущая погода */}
      <div style={{
        background: '#f1f5f3',
        border: '1px solid #e0e7e3',
        borderRadius: 12,
        padding: '16px',
        marginBottom: 12
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 42, lineHeight: 1, marginBottom: 4 }}>
              {currentWeather.icon}
            </div>
            <div style={{ fontSize: 13, color: '#6b8578' }}>{currentWeather.label}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 44, fontWeight: 700, color: '#16a34a', lineHeight: 1 }}>
              {Math.round(current.temperature)}°
            </div>
            <div style={{ fontSize: 12, color: '#6b8578' }}>
              Ощущается как {Math.round(current.feels_like)}°
            </div>
          </div>
        </div>

        {/* Детали текущей погоды */}
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
          gap: 8, marginTop: 12, paddingTop: 12,
          borderTop: '1px solid #e0e7e3'
        }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 18 }}>💧</div>
            <div style={{ fontSize: 13, color: '#1a2e23', fontWeight: 600 }}>{current.humidity}%</div>
            <div style={{ fontSize: 11, color: '#6b8578' }}>Влажность</div>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 18 }}>💨</div>
            <div style={{ fontSize: 13, color: '#1a2e23', fontWeight: 600 }}>
              {Math.round(current.wind_speed)} км/ч
            </div>
            <div style={{ fontSize: 11, color: '#6b8578' }}>{windDir(current.wind_direction)}</div>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 18 }}>🌧️</div>
            <div style={{ fontSize: 13, color: '#1a2e23', fontWeight: 600 }}>
              {current.precipitation?.toFixed(1) ?? 0} мм
            </div>
            <div style={{ fontSize: 11, color: '#6b8578' }}>Осадки</div>
          </div>
        </div>
      </div>

      {/* Прогноз на 5 дней */}
      <div>
        <div style={{ fontSize: 12, color: '#6b8578', marginBottom: 8, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Прогноз на 5 дней
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {(forecast || []).slice(0, 5).map((day, idx) => {
            const w = getWeather(day.weather_code);
            const hasPrecip = day.precipitation > 0.5;
            return (
              <div key={day.date} style={{
                display: 'flex', alignItems: 'center',
                justifyContent: 'space-between',
                padding: '8px 10px',
                borderRadius: 8,
                background: idx === 0 ? '#f1f5f3' : 'transparent',
                border: `1px solid ${idx === 0 ? '#e0e7e3' : 'transparent'}`,
              }}>
                <div style={{ width: 80, fontSize: 13, color: idx === 0 ? '#1a2e23' : '#6b8578' }}>
                  {fmtDay(day.date, idx)}
                </div>
                <div style={{ fontSize: 20, width: 32, textAlign: 'center' }}>{w.icon}</div>
                {hasPrecip ? (
                  <div style={{ fontSize: 12, color: '#3b82f6', width: 50, textAlign: 'center' }}>
                    💧 {day.precipitation.toFixed(1)}мм
                  </div>
                ) : (
                  <div style={{ width: 50 }} />
                )}
                <div style={{ textAlign: 'right' }}>
                  <span style={{ fontSize: 14, fontWeight: 600, color: '#1a2e23' }}>
                    {Math.round(day.temp_max)}°
                  </span>
                  <span style={{ fontSize: 13, color: '#6b8578', marginLeft: 6 }}>
                    {Math.round(day.temp_min)}°
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Агрономическая подсказка */}
      {weather.forecast && (() => {
        const next3 = weather.forecast.slice(0, 3);
        const totalRain = next3.reduce((s, d) => s + (d.precipitation || 0), 0);
        const maxTemp = Math.max(...next3.map(d => d.temp_max));
        if (totalRain > 15) return (
          <div style={{
            marginTop: 12, padding: '10px 12px',
            background: '#eff6ff', border: '1px solid #93c5fd',
            borderRadius: 8, fontSize: 12, color: '#1e40af'
          }}>
            🌧️ Ожидаются обильные осадки ({totalRain.toFixed(0)} мм за 3 дня). Возможен риск переувлажнения и болезней.
          </div>
        );
        if (maxTemp > 40) return (
          <div style={{
            marginTop: 12, padding: '10px 12px',
            background: '#fef2f2', border: '1px solid #fecaca',
            borderRadius: 8, fontSize: 12, color: '#b91c1c'
          }}>
            🌡️ Экстремальная жара (до {maxTemp}°C). Рекомендуется увеличить норму полива.
          </div>
        );
        return null;
      })()}

      <div style={{ marginTop: 10, fontSize: 11, color: '#6b8578', textAlign: 'right' }}>
        Данные: Open-Meteo • {new Date().toLocaleDateString('ru-RU')}
      </div>
    </div>
  );
}
