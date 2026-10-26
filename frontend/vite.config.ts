import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true },
  // force-graph + d3 make one large chunk; that is fine for a local dashboard.
  build: { chunkSizeWarningLimit: 1500 },
});
