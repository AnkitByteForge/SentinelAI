/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx}',
    './components/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg:      '#070B0F',
        surface: '#0D1117',
        border:  '#1A2332',
        cyan:    '#00E5CC',
        amber:   '#F59E0B',
        emerald: '#10B981',
        rose:    '#F43F5E',
        muted:   '#4A5568',
        dim:     '#8892A4',
      },
      fontFamily: {
        mono:    ['var(--font-mono)', 'JetBrains Mono', 'monospace'],
        display: ['var(--font-display)', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4,0,0.6,1) infinite',
        'scan':       'scan 8s linear infinite',
        'glow':       'glow 2s ease-in-out infinite alternate',
        'fade-in':    'fadeIn 0.4s ease forwards',
        'slide-up':   'slideUp 0.4s ease forwards',
      },
      keyframes: {
        scan: {
          '0%':   { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100vh)' },
        },
        glow: {
          '0%':   { textShadow: '0 0 4px #00E5CC44' },
          '100%': { textShadow: '0 0 12px #00E5CC88, 0 0 24px #00E5CC44' },
        },
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%':   { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}