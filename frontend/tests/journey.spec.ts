import {test,expect} from '@playwright/test';
test('generate, decode, retrieve history, and retry without duplicates',async({page})=>{
 await page.goto('/');
 await page.screenshot({path:'test-results/dashboard.png',fullPage:true});
 const vehicle=`E2E-${Date.now()}`;
 await page.getByLabel('vehicle_id',{exact:true}).fill(vehicle);
 await page.getByLabel('altitude_reference',{exact:true}).selectOption('msl');
 await page.getByRole('button',{name:'Generate IDs →'}).click();
 await expect(page.getByRole('status')).toContainText('Committed');
 const first=await page.locator('.id-item').first().innerText();
 await page.getByRole('button',{name:'Generate IDs →'}).click();
 await expect(page.locator('.id-item').first()).toContainText(first.replace('↗','').trim());
 await page.locator('.id-item').first().click();
 await expect(page.locator('.details')).toContainText('timestamp');
 await page.getByRole('button',{name:/History/}).first().click();
 await page.getByRole('button',{name:'Search history'}).click();
 await expect(page.locator('tbody tr')).toHaveCount(1);
 await page.getByRole('button',{name:'Latest record'}).click();
 await expect(page.locator('pre')).toContainText(vehicle);
 await expect(page.locator('pre')).toContainText('"altitude_reference": "msl"');
 await page.reload();
 await page.getByRole('button',{name:/History/}).first().click();
 await page.getByLabel('History vehicle').fill(vehicle);
 await page.getByRole('button',{name:'Search history'}).click();
 await expect(page.locator('tbody tr')).toHaveCount(1);
});

test('mobile layout and disconnected health remain usable',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/nodes/**',route=>route.abort());
 await page.goto('/');
 await page.getByRole('button',{name:/Health/}).first().click();
 await expect(page.getByLabel('API token',{exact:true})).toBeVisible();
 await expect(page.getByText('Metrics are unavailable, not zero.').first()).toBeVisible();
 await expect(page.locator('main')).toBeVisible();
 await page.screenshot({path:'test-results/mobile-health.png',fullPage:true});
});

test('test lab launches a bounded run and exposes its report',async({page})=>{
 test.skip(!process.env.TEST_TOKEN,'requires compose.test.yaml and TEST_TOKEN');
 await page.goto('/');
 await page.getByRole('button',{name:/Test Lab/}).first().click();
 await page.getByLabel('Test access token').fill(process.env.TEST_TOKEN!);
 await page.getByLabel('duration',{exact:true}).fill('2');
 await page.getByLabel('total ids',{exact:true}).fill('100');
 await page.getByRole('button',{name:'Start test →'}).click();
 await expect(page.locator('.badge').filter({hasText:'completed'})).toBeVisible({timeout:20000});
 await expect(page.locator('pre')).toContainText('"duplicate_ids": 0');
});

test('trajectory screen connects valid events and reports an A star route',async({page,request})=>{
 const vehicle=`PATH-${Date.now()}`;
 for(const [index,point] of [[12.9716,77.5946],[12.9720,77.5950],[12.9724,77.5954]].entries()){
  const response=await request.post('/v1/ids',{data:{vehicle_id:vehicle,latitude:point[0],longitude:point[1],altitude_m:120+index,speed_mps:10,captured_at_ms:1000+index*10000},headers:{'Idempotency-Key':`${vehicle}-${index}`}});
  expect(response.ok()).toBeTruthy();
 }
 await page.goto('/');
 await page.getByRole('button',{name:/Paths/}).first().click();
 await page.getByLabel('Path vehicle').fill(vehicle);
 await page.getByLabel('max_speed_mps').fill('20');
 await page.getByRole('button',{name:'Build path →'}).click();
 await expect(page.getByText('A* ROUTE FOUND')).toBeVisible();
 await expect(page.getByText('Connected segments').locator('..').locator('strong')).toHaveText('2');
 await expect(page.getByRole('img',{name:'Relative trajectory plot'})).toBeVisible();
 await page.screenshot({path:'test-results/trajectory.png',fullPage:true});
});
