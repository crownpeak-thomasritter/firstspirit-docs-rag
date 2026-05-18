/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Crownpeak / FirstSpirit Docs palette (light theme).
        bg: '#ffffff',
        surface1: '#f7f7f8',
        surface2: '#e4e7ef',
        ink: '#1a1616',
        'ink-55': '#1a16168c',
        'ink-40': '#1a161666',
        line: '#e4e7ef',
        'line-2': '#1a16161a',
        // Rezolve violet — the brand accent.
        accent: '#bd2eff',
        'accent-dark': '#a314e8',
        'accent-bg': '#bd2eff14',
        'accent-bg-2': '#bd2eff29',
        // User bubble uses the violet so chat reads as branded.
        'user-bubble': '#bd2eff',
        'assistant-bubble': '#f7f7f8',
        success: '#2f8a52',
        danger: '#dc2626',
        warning: '#c2410c',
        // Legacy aliases kept so the existing `text-text-secondary` etc. classes
        // still resolve while components are migrated to var()-driven utilities.
        'text-primary': '#1a1616',
        'text-secondary': '#1a16168c',
        'text-tertiary': '#1a161666',
      },
      fontFamily: {
        sans: ['Geist', 'Inter', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'sans-serif'],
        mono: ['"Geist Mono"', '"JetBrains Mono"', '"Fira Code"', 'monospace'],
        serif: ['"Instrument Serif"', 'Georgia', 'serif'],
      },
      borderRadius: {
        sm: '6px',
        DEFAULT: '10px',
        md: '10px',
        lg: '14px',
        xl: '20px',
        '2xl': '28px',
      },
    },
  },
  plugins: [],
}
