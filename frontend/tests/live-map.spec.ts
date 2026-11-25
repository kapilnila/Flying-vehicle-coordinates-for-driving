import {test, expect, type Page} from '@playwright/test';
import type {Track, TrackPoint} from '../src/PathPreview';

const timestamp = Date.UTC(2026, 10, 5, 12);
function point(index: number, reference: 'unknown' | 'msl' = 'msl'): TrackPoint {
  return {id: String(index), latitude: Number((12.97 + index / 10000).toFixed(4)), longitude: 77.59,
    captured_at_ms: timestamp + index * 1000, altitude_m: 950, altitude_reference: reference, speed_mps: 10};
}
function track(points: TrackPoint[]): Track {
  return {points, segments: points.slice(1).map((p, i) => ({
    from_id: points[i].id, to_id: p.id, connected: i !== 1,
  }))};
}
function metric(page: Page, label: string) {
  return page.getByText(label, {exact: true}).locator('..').locator('dd');
}

async function mockMaps(page: Page) {
  await page.addInitScript(() => {
    const fake = {imports: 0, elevationCalls: 0, failTerrain: false, failMaps: false, features: [] as any[],
      centers: [] as {lat: number; lng: number}[]};
    class FakeMap {
      data = {
        setStyle() {},
        addGeoJson(collection: {features: any[]}) {
          fake.features.push(...collection.features);
          return collection.features;
        },
        remove(feature: unknown) { fake.features = fake.features.filter(f => f !== feature); },
      };
      panTo(position: {lat: number; lng: number}) { fake.centers.push(position); }
    }
    class ElevationService {
      async getElevationForLocations() {
        fake.elevationCalls++;
        if (fake.failTerrain) throw Error('Quota exceeded');
        return {results: [{elevation: 900}]};
      }
    }
    Object.assign(window, {fakeMaps: fake, google: {maps: {
      importLibrary: async (library: string) => {
        fake.imports++;
        if (fake.failMaps) throw Error('Maps offline');
        return library === 'maps' ? {Map: FakeMap} : {ElevationService};
      },
      SymbolPath: {CIRCLE: 0},
      event: {addListenerOnce: (_: unknown, __: string, handler: () => void) => {
        const timer = setTimeout(handler, 0);
        return {remove: () => clearTimeout(timer)};
      }},
    }}});
  });
}

async function openLive(page: Page) {
  await page.goto('/');
  await page.getByRole('button', {name: /Paths/}).click();
  await page.getByRole('button', {name: 'Live map', exact: true}).click();
}

test.beforeEach(async ({page}) => {
  await page.route('**/nodes/**', route => route.abort());
  // Never let a test use a configured, billed browser key.
  await page.route('https://maps.googleapis.com/**', route => route.abort());
  await page.clock.install({time: timestamp + 10000});
});

test('map and terrain are opt-in; trail skips rejected segments and stops polling on close', async ({page}) => {
  await mockMaps(page);
  let requests = 0;
  let current = track([point(1), point(2), point(3)]);
  await page.route('**/trajectory?**', route => {
    requests++;
    expect(route.request().url()).toContain('window=latest');
    expect(route.request().url()).toContain('limit=100');
    return route.fulfill({json: current});
  });
  await page.goto('/');
  await page.getByRole('button', {name: /Paths/}).click();
  await expect(page.getByRole('button', {name: 'Path analysis', exact: true})).toHaveAttribute('aria-pressed', 'true');
  expect(requests).toBe(0);
  expect(await page.evaluate(() => (window as any).fakeMaps.imports)).toBe(0);
  await page.getByRole('button', {name: 'Live map', exact: true}).click();
  await expect(metric(page, 'Latest position')).toHaveText('12.9703, 77.59');
  await expect(page.getByLabel('Show terrain elevation')).not.toBeChecked();
  expect(await page.evaluate(() => (window as any).fakeMaps.elevationCalls)).toBe(0);
  await expect.poll(() => page.evaluate(() => (window as any).fakeMaps.features.length)).toBe(4);
  const lines = await page.evaluate(() => (window as any).fakeMaps.features.filter((f: any) => f.geometry.type === 'LineString'));
  expect(lines).toHaveLength(1);

  // Arrival of an older sample changes adjacency without changing the latest marker.
  current = track([point(0), point(1), point(2), point(3)]);
  await page.clock.runFor(5000);
  await expect.poll(() => requests).toBe(2);
  await expect(metric(page, 'Latest position')).toHaveText('12.9703, 77.59');
  current = track([point(2), point(3), point(4)]);
  await page.clock.runFor(5000);
  await expect(metric(page, 'Latest position')).toHaveText('12.9704, 77.59');
  await page.getByRole('button', {name: 'Path analysis', exact: true}).click();
  const closedAt = requests;
  await page.clock.runFor(15000);
  expect(requests).toBe(closedAt);
});

test('terrain computes MSL height, throttles requests, and keeps sampled coordinates explicit', async ({page}) => {
  await mockMaps(page);
  let current = track([point(1)]);
  await page.route('**/trajectory?**', route => route.fulfill({json: current}));
  await openLive(page);
  await expect(page.getByLabel('Show terrain elevation')).toBeVisible();
  await page.clock.pauseAt(timestamp + 100000);
  await page.getByLabel('Show terrain elevation').check();
  await expect(metric(page, 'Terrain elevation above sea level')).toHaveText('900.0 m');
  await expect(metric(page, 'Estimated height above terrain')).toHaveText('≈ 50.0 m');
  expect(await page.evaluate(() => (window as any).fakeMaps.elevationCalls)).toBe(1);
  current = track([point(2)]);
  await page.clock.runFor(5000);
  await expect(metric(page, 'Latest position')).toHaveText('12.9702, 77.59');
  await expect(metric(page, 'Estimated height above terrain')).toHaveText('Unavailable');
  await expect(page.getByText(/Google Maps terrain sampled at/)).toContainText('12.9701, 77.59');
  await page.clock.runFor(24000);
  expect(await page.evaluate(() => (window as any).fakeMaps.elevationCalls)).toBe(1);
  await page.clock.runFor(2000);
  await expect(metric(page, 'Estimated height above terrain')).toHaveText('≈ 50.0 m');
  expect(await page.evaluate(() => (window as any).fakeMaps.elevationCalls)).toBe(2);
  await page.clock.runFor(30000);
  expect(await page.evaluate(() => (window as any).fakeMaps.elevationCalls)).toBe(2);
  await page.getByLabel('Show terrain elevation').uncheck();
  current = track([point(3)]);
  await page.clock.runFor(30000);
  expect(await page.evaluate(() => (window as any).fakeMaps.elevationCalls)).toBe(2);
});

test('unknown altitude and failed terrain keep the live position visible', async ({page}) => {
  await mockMaps(page);
  let current = track([point(1, 'unknown')]);
  await page.route('**/trajectory?**', route => route.fulfill({json: current}));
  await openLive(page);
  await page.getByLabel('Show terrain elevation').check();
  await expect(metric(page, 'Terrain elevation above sea level')).toHaveText('900.0 m');
  await expect(metric(page, 'Estimated height above terrain')).toHaveText('Unavailable');
  await expect(metric(page, 'Reported altitude')).toContainText('reference unknown');
  await page.evaluate(() => { (window as any).fakeMaps.failTerrain = true; });
  current = track([point(2)]);
  await page.clock.runFor(31000);
  await expect(metric(page, 'Terrain elevation above sea level')).toHaveText('Unavailable');
  await expect(metric(page, 'Estimated height above terrain')).toHaveText('Unavailable');
  await expect(metric(page, 'Latest position')).toHaveText('12.9702, 77.59');
  await page.evaluate(() => { (window as any).fakeMaps.failTerrain = false; });
  await page.clock.runFor(30000);
  await expect(metric(page, 'Estimated height above terrain')).toHaveText('≈ 50.0 m');
});

test('keyless or failed map uses coordinate plot; failed polling holds position and ages it', async ({page}) => {
  let fail = false;
  await page.route('**/trajectory?**', route => fail ? route.abort() : route.fulfill({json: track([point(1), point(2), point(3)])}));
  await openLive(page);
  await expect(page.getByRole('img', {name: 'Relative trajectory plot'})).toBeVisible();
  await expect(page.locator('.path-preview line')).toHaveCount(1);
  await expect(metric(page, 'Captured')).toContainText('seconds ago');
  fail = true;
  await page.clock.runFor(65000);
  await expect(page.getByRole('alert')).toContainText('Could not refresh');
  await expect(metric(page, 'Captured')).toContainText('minute ago');
  await expect(metric(page, 'Latest position')).toHaveText('12.9703, 77.59');
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({path: 'test-results/live-map-mobile.png', fullPage: true});
});

test('delayed responses from the previous vehicle are discarded', async ({page}) => {
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/vehicles/VEH-1024/trajectory?**', async route => {
    await gate;
    try { await route.fulfill({json: track([point(1)])}); } catch { /* request was aborted */ }
  });
  await page.route('**/vehicles/next/trajectory?**', route => route.fulfill({json: track([point(8)])}));
  await openLive(page);
  await page.getByLabel('Live vehicle').fill('next');
  await page.getByRole('button', {name: 'Follow vehicle'}).click();
  await expect(metric(page, 'Latest position')).toHaveText('12.9708, 77.59');
  release();
  await page.clock.runFor(100);
  await expect(metric(page, 'Latest position')).toHaveText('12.9708, 77.59');
});

test('Google authorization failure falls back after the map has loaded', async ({page}) => {
  await mockMaps(page);
  await page.route('**/trajectory?**', route => route.fulfill({json: track([point(1)])}));
  await openLive(page);
  await expect(page.getByLabel('Show terrain elevation')).toBeVisible();
  await page.evaluate(() => (window as any).gm_authFailure());
  await expect(page.getByRole('img', {name: 'Relative trajectory plot'})).toBeVisible();
  await expect(page.getByText(/Google Maps rejected/)).toBeVisible();
  await expect(metric(page, 'Latest position')).toHaveText('12.9701, 77.59');
});

test('empty vehicle begins tracking when a record arrives and reuses terrain at identical coordinates', async ({page}) => {
  await mockMaps(page);
  let current = track([]);
  await page.route('**/trajectory?**', route => route.fulfill({json: current}));
  await openLive(page);
  await expect(page.getByText('Waiting for the first vehicle record.')).toBeVisible();
  expect(await page.evaluate(() => (window as any).fakeMaps.imports)).toBe(0);
  current = track([point(1, 'unknown')]);
  await page.clock.runFor(5000);
  await page.getByLabel('Show terrain elevation').check();
  await expect(metric(page, 'Terrain elevation above sea level')).toHaveText('900.0 m');
  await expect(metric(page, 'Estimated height above terrain')).toHaveText('Unavailable');
  current = track([{...point(1), id: 'newer', captured_at_ms: timestamp + 9000, altitude_m: 960}]);
  await page.clock.runFor(5000);
  await expect(metric(page, 'Estimated height above terrain')).toHaveText('≈ 60.0 m');
  expect(await page.evaluate(() => (window as any).fakeMaps.elevationCalls)).toBe(1);
});

