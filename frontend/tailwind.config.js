/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        agro: {
          'dark': '#f8faf9',         // Main background — very light gray-green
          'panel': '#ffffff',         // Panel backgrounds — white
          'card': '#f1f5f3',          // Card/item backgrounds — light green-gray
          'border': '#e0e7e3',        // Borders — subtle green-gray
          'hover': '#e8eeea',         // Hover state
          'text': '#1a2e23',          // Primary text — dark green-black
          'muted': '#6b8578',         // Secondary/muted text — medium green-gray
          'accent': '#16a34a',        // Primary accent — green-600
          'accent-dim': '#bbf7d0',    // Dimmed accent — green-200
          // kept for backward compat
          bg: '#f8faf9',
          surface: '#ffffff',
          surface2: '#e0e7e3',
          accent2: '#22c55e',
          danger: '#ef4444',
          warning: '#f59e0b',
          info: '#3b82f6',
        },
        ndvi: {
          low: '#8B0000',
          mid: '#FFD700',
          high: '#228B22',
          max: '#006400',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
