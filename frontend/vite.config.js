import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时前端 5173，/api 代理到 Flask 5000；
// 打包后由 Flask 直接托管 dist，同源无跨域。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
  },
})
