import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
    server: {
        host: '0.0.0.0',    // ← This is the fix — expose to all interfaces
        port: 3001,
        proxy: {
            '/api': {
                target: 'http://api-gateway:8000',   // ← Docker internal name, not localhost
                changeOrigin: true,
            },
        },
    },
})