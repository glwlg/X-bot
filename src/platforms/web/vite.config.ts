import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), tailwindcss()],

  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },

  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },

  publicDir: 'public',

  build: {
    // 生产构建输出到 FastAPI 静态目录
    outDir: '../../api/static/dist',
    emptyOutDir: true,
  },

  // 生产环境使用相对路径
  base: '/',
})
