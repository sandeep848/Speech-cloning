import {defineConfig, loadEnv} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({mode}) => {
const apiUrl = loadEnv(mode, process.cwd(), 'VITE_').VITE_API_URL;
const apiOrigin = apiUrl ? new URL(apiUrl).origin : '';
if (apiUrl && !apiUrl.startsWith('https://') && !/^http:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(apiUrl)) {
  throw new Error('VITE_API_URL must use HTTPS except for local development');
}
return {
  plugins:[react(), {
    name:'production-csp', apply:'build',
    transformIndexHtml() {
      return [{tag:'meta', attrs:{'http-equiv':'Content-Security-Policy',content:
        `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ${apiOrigin}; media-src 'self' blob: ${apiOrigin}; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'`},injectTo:'head-prepend'}];
    },
  }],
  base:process.env.GITHUB_PAGES_BASE || '/',
  server:{proxy:{'/api':'http://127.0.0.1:8000'}},
};
});
