/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 950: '#111c2d', 900: '#172437', 800: '#213149', 700: '#34445c' },
        teal: { 50: '#effcf9', 100: '#d7f7ef', 500: '#14a98b', 600: '#0d8e76', 700: '#0b725f' },
      },
      fontFamily: {
        sans: ['Inter', '"PingFang SC"', '"Microsoft YaHei"', '"Noto Sans CJK SC"', 'system-ui', 'sans-serif'],
        mono: ['"SFMono-Regular"', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
      boxShadow: {
        panel: '0 1px 2px rgba(16, 30, 54, .035)',
        float: '0 16px 48px rgba(15, 27, 45, .16)',
      },
    },
  },
  plugins: [],
};
