// 静态演示包构建配置（打包给外部验收人看的那份）
//
// 和 vite.config.js 的区别：
//   - base: './'                  所有资源用相对路径，方便单文件内联
//   - assetsInlineLimit 调大      图片等小资源直接内联
//   - cssCodeSplit: false         只产出一个 CSS 文件，便于内联进 HTML
//   - inlineDynamicImports        只产出一个 JS 文件
//
// 产物 dist-static/ 还会被 scripts/build_standalone.py 进一步压成单文件 HTML。
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    outDir: 'dist-static',
    emptyOutDir: true,
    assetsInlineLimit: 100000000,
    cssCodeSplit: false,
    chunkSizeWarningLimit: 4096,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'assets/app.js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
})
