import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物直接落到仓库根目录的 dist/，方便一键挂到博客 / GitHub Pages。
// base 用相对路径，这样放在任意子目录下都能打开（含 file:// 双击预览）。
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5199,
    host: '127.0.0.1',
  },
})
