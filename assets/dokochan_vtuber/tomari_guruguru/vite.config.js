import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';
import { cpSync } from 'fs';

const DEFAULT_ASSET_BASE = 'generated_v6_gpt_hairclip/slices_gpt_hairclip_candidate_01_png';

function copyDefaultAssets() {
  return {
    name: 'copy-default-character-assets',
    closeBundle() {
      cpSync(
        resolve(import.meta.dirname, DEFAULT_ASSET_BASE),
        resolve(import.meta.dirname, 'dist', DEFAULT_ASSET_BASE),
        { recursive: true }
      );
    },
  };
}

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/dokochan-guruguru/' : '/',
  plugins: [react(), command === 'build' ? copyDefaultAssets() : null],
  server: {
    host: '127.0.0.1',
    open: '/talk.html',
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        guruguru: resolve(import.meta.dirname, 'guruguru.html'),
        talk: resolve(import.meta.dirname, 'talk.html'),
      },
    },
  },
}));
