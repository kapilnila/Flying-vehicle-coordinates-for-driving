import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{
  '/v1':'http://127.0.0.1:8080',
  '/test-api':'http://127.0.0.1:8090',
  '/nodes/0':{target:'http://127.0.0.1:8080',rewrite:p=>p.replace('/nodes/0','')},
  '/nodes/1':{target:'http://127.0.0.1:8081',rewrite:p=>p.replace('/nodes/1','')}
}}});
