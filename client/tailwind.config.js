/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Odyssey Preservation Software palette (logo usage guide)
        ink: '#E8F1F3',
        canvas: '#032F3A',
        panel: '#054657',
        line: '#177F90',
        cyan: '#08A2A7',
        lime: '#A6CE3A',
        chartreuse: '#CCDB2D',
        forest: '#039569',
        teal: '#054657',
        'teal-mid': '#177F90',
        amber: '#E2C46A',
        rose: '#E57373',
        violet: '#5BA3AE',
      },
      fontFamily: {
        sans: ['Montserrat', 'Avenir Next', 'Segoe UI', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['Montserrat', 'Avenir Next', 'Segoe UI', 'sans-serif'],
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(8,162,167,.18), 0 18px 60px rgba(0,0,0,.28)',
      },
    },
  },
  plugins: [],
}
