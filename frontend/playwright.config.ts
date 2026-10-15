import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',use:{baseURL:process.env.BASE_URL||'http://127.0.0.1:5173',trace:'retain-on-failure',launchOptions:{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}},reporter:'list',timeout:30000});
