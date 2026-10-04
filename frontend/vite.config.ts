import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true },
  // cytoscape alone is ~500 kB; one chunk is fine for a local dashboard.
  build: { chunkSizeWarningLimit: 1500 },
});
