import { Link } from 'react-router-dom';

export default function UnauthorizedPage() {
  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center px-4">
      <div className="text-center">
        <div className="text-6xl mb-4">🚫</div>
        <h1 className="text-xl font-semibold text-white mb-2">Доступ запрещён</h1>
        <p className="text-sm text-slate-400 mb-6">
          У вашей учётной записи нет прав для просмотра этой страницы.
        </p>
        <Link
          to="/"
          className="inline-block bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors"
        >
          На главную
        </Link>
      </div>
    </div>
  );
}
