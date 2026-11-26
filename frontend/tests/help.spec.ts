import {test, expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const deployments = [
  {name: 'local public', url: process.env.HELP_PUBLIC_URL, publicApi: true, hosted: false},
  {name: 'hosted protected', url: process.env.HELP_PROTECTED_URL, publicApi: false, hosted: true},
];

for (const deployment of deployments) {
  test.describe(deployment.name, () => {
    test.skip(!deployment.url, 'requires temporary Help verification servers');

    test('walkthrough, copy controls, and mobile layout are informational', async ({page, context}) => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write']);
      await page.addInitScript(() => {
        (window as any).locationCalls = 0;
        navigator.geolocation.getCurrentPosition = () => {(window as any).locationCalls++;};
      });
      let posts = 0;
      page.on('request', request => {if (request.method() === 'POST') posts++;});
      await page.goto(deployment.url!);
      await page.getByRole('button', {name: /Help/}).click();
      await expect(page.getByRole('heading', {name: 'Get started with Spatial ID.'})).toBeVisible();
      await expect(page.getByRole('button', {name: /Test Lab/})).toHaveCount(deployment.hosted ? 0 : 1);
      await expect(page.getByText(deployment.publicApi ? 'Public vehicle access is enabled.' : 'Public access is disabled.', {exact: false})).toBeVisible();
      await page.getByRole('button', {name: 'Copy base URL'}).click();
      expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(deployment.url);
      for (const language of ['Python', 'JavaScript', 'cURL']) {
        await page.getByRole('button', {name: language, exact: true}).click();
        await page.getByRole('button', {name: 'Copy ' + language + ' example', exact: true}).click();
        const sample = await page.getByLabel(language + ' example', {exact: true}).innerText();
        expect((await page.evaluate(() => navigator.clipboard.readText())).replaceAll('\r\n', '\n')).toBe(sample);
        expect(sample).toContain(deployment.url!);
        expect(sample).toContain('Idempotency-Key');
        expect(sample.includes('API_TOKEN')).toBe(!deployment.publicApi);
        expect(sample).not.toContain('help-test-api-token');
      }
      expect(posts).toBe(0);
      expect(await page.evaluate(() => (window as any).locationCalls)).toBe(0);
      for (const [destination, heading] of [['Generate', 'Spatial event'], ['History', 'Vehicle records'], ['Paths', 'Trajectory rules']]) {
        await page.getByRole('button', {name: 'Open ' + destination, exact: true}).click();
        await expect(page.getByRole('heading', {name: heading, exact: true})).toBeVisible();
        await page.getByRole('button', {name: /Help/}).click();
      }
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({path: 'test-results/help-desktop-' + (deployment.hosted ? 'hosted' : 'local') + '.png'});
      await page.setViewportSize({width: 390, height: 844});
      await expect(page.getByRole('button', {name: 'Open Generate'})).toBeVisible();
      const helpButton = await page.getByRole('button', {name: /Help/}).boundingBox();
      expect(helpButton!.x + helpButton!.width).toBeLessThanOrEqual(390);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
      await page.screenshot({path: 'test-results/help-' + (deployment.hosted ? 'hosted' : 'local') + '.png', fullPage: true});
    });

    test('all displayed samples submit and retrieve the same point', async ({page}) => {
      test.setTimeout(90000);
      test.skip(!process.env.HELP_PYTHON || !process.env.HELP_BASH, 'requires Python and Bash executables');
      await page.goto(deployment.url!);
      await page.getByRole('button', {name: /Help/}).click();
      await expect(page.getByRole('button', {name: 'Copy Python example'})).toBeEnabled();
      const folder = mkdtempSync(join(tmpdir(), 'spatial-help-examples-'));
      const env = {...process.env, API_TOKEN: 'help-test-api-token'};
      try {
        for (const [language, extension, command] of [
          ['Python', '.py', process.env.HELP_PYTHON!],
          ['JavaScript', '.mjs', process.execPath],
          ['cURL', '.sh', process.env.HELP_BASH!],
        ]) {
          await page.getByRole('button', {name: language, exact: true}).click();
          const code = await page.getByLabel(language + ' example', {exact: true}).innerText();
          const filename = join(folder, 'sample' + extension);
          writeFileSync(filename, code, 'utf8');
          const script = language === 'cURL' ? filename.replaceAll('\\', '/') : filename;
          const output = execFileSync(command, [script], {env, encoding: 'utf8', timeout: 30000, stdio: ['ignore', 'pipe', 'pipe']});
          const result = JSON.parse(output.slice(output.indexOf('{')));
          const id = result.created.ids[0];
          expect(result.created.count).toBe(1);
          expect(result.record).toMatchObject({id, latitude: 12.9716, longitude: 77.5946, altitude_m: 120.4, speed_mps: 0});
          expect(result.record.vehicle_id).toMatch(/^DEMO-/);
          expect(result.latest.id).toBe(id);
          expect(result.history.items.map((item: {id: string}) => item.id)).toEqual([id]);
        }
      } finally {
        rmSync(folder, {recursive: true, force: true});
      }
    });
  });
}

test('unavailable configuration and clipboard failures have recovery guidance', async ({page}) => {
  test.skip(!process.env.HELP_PUBLIC_URL, 'requires temporary Help server');
  let unavailable = true;
  await page.route('**/v1/config', route => route.fulfill({
    status: unavailable ? 503 : 200, json: unavailable ? {} : {public_api: true},
  }));
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {value: {
      writeText: () => Promise.reject(new Error('Denied')),
    }});
  });
  await page.goto(process.env.HELP_PUBLIC_URL!);
  await page.getByRole('button', {name: /Help/}).click();
  await expect(page.getByText(/Access mode could not be checked/)).toBeVisible();
  unavailable = false;
  await page.getByRole('button', {name: 'Retry access check'}).click();
  await expect(page.getByText(/Public vehicle access is enabled/)).toBeVisible();
  await page.getByRole('button', {name: 'Copy base URL'}).click();
  await expect(page.getByRole('alert')).toContainText('copy it manually');
});
