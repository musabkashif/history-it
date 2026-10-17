/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#eaf0f7',
        canvas: '#08111f',
        panel: '#0e1b2d',
        line: '#20334c',
        cyan: '#63e6e2',
        amber: '#f4c66d',
        rose: '#fb7185',
        violet: '#a78bfa',
      },
      boxShadow: { glow: '0 0 0 1px rgba(99,230,226,.12), 0 18px 60px rgba(0,0,0,.22)' },
    },
  },
  plugins: [],
}
