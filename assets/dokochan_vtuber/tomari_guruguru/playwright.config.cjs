const path = require('node:path');
const { defineConfig, devices } = require('@playwright/test');

const fakeAudio = path.resolve(__dirname, 'verification/fake_voice.wav');

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 30 * 1000,
  reporter: 'line',
  workers: 1,
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:5179',
    ...devices['Desktop Chrome'],
    viewport: { width: 1280, height: 800 },
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    launchOptions: {
      args: [
        '--use-fake-ui-for-media-stream',
        '--use-fake-device-for-media-stream',
        `--use-file-for-fake-audio-capture=${fakeAudio}`,
      ],
    },
  },
});
