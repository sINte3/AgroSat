import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [tokenInput, setTokenInput] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!tokenInput.trim()) return;
    setBusy(true);
    setError('');

    const ok = await login(tokenInput.trim());
    setBusy(false);

    if (ok) {
      navigate('/', { replace: true });
    } else {
      setError('Токен недействителен или истёк');
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-slate-900 border border-slate-800 rounded-xl p-8 shadow-2xl"
      >
        <div className="text-center mb-6">
          <div className="w-12 h-12 rounded-full bg-emerald-500 flex items-center justify-center mx-auto mb-4">
            <span className="text-white font-bold text-lg">A</span>
          </div>
          <h1 className="text-xl font-semibold text-white tracking-tight">AgroSat</h1>
          <p className="text-sm text-slate-400 mt-1">Bukhara Agrocluster</p>
        </div>

        <label className="block text-xs text-slate-400 font-mono mb-1.5">
          Токен доступа
        </label>
        <input
          type="password"
          value={tokenInput}
          onChange={(e) => setTokenInput(e.target.value)}
          placeholder="Paste your JWT token"
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500 font-mono"
          autoFocus
        />

        {error && (
          <p className="text-red-400 text-xs mt-2">{error}</p>
        )}

        <button
          type="submit"
          disabled={busy || !tokenInput.trim()}
          className="w-full mt-4 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:text-slate-500 text-white text-sm font-medium rounded-lg py-2.5 transition-colors"
        >
          {busy ? 'Проверка...' : 'Войти'}
        </button>
      </form>
    </div>
  );
}
