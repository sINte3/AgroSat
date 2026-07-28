async (page) => {
  const evidence = 'C:/AgroSat_backups/agent_browser/task209_phase08';
  let currentRole = 'manager';
  let inspectionLinked = false;
  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  const apiRequests = [];
  const writes = [];

  await page.unroute('**/api/**');
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  page.on('requestfailed', (request) => failedRequests.push({
    url: request.url(),
    failure: request.failure()?.errorText || 'failed',
  }));
  page.on('request', (request) => {
    if (!request.url().includes('/api/')) return;
    apiRequests.push({ method: request.method(), url: request.url() });
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      writes.push({ method: request.method(), url: request.url() });
    }
  });
  await page.addInitScript(() => {
    localStorage.setItem('agrosat_token', 'task209-fixture-token');
    localStorage.setItem(
      'agrosat_user',
      JSON.stringify({ id: 5, role: 'manager', enterprise_id: 7 }),
    );
  });

  const observed = ['2026-05-20', '2026-06-01', '2026-06-10', '2026-06-20'];
  const queryValue = (query, name) => {
    for (const part of query.split('&')) {
      const [key, value = ''] = part.split('=');
      if (decodeURIComponent(key) === name) return decodeURIComponent(value);
    }
    return null;
  };
  const indexRecord = (code, index) => ({
    id: 100 + index,
    field_id: 11,
    index_code: code,
    captured_date: observed[index],
    mean_value: 0.44 + index * 0.025,
    min_value: 0.2,
    max_value: 0.7,
    cloud_cover_pct: 5,
    valid_pixels_pct: 94,
  });
  const anomalyItem = () => ({
    id: 91,
    field: {
      id: 11,
      name: 'Контрактное поле',
      enterprise_id: 7,
      enterprise_name: 'Тестовое предприятие',
    },
    index_code: 'ndvi',
    current_observation_date: '2026-06-20',
    comparison_observation_date: '2026-06-10',
    area_ha: 2.46,
    score: 0.78,
    severity: 'high',
    persistence_count: 2,
    classification: 'persistent',
    confidence: 0.86,
    status: inspectionLinked ? 'inspection_created' : 'open',
    created_at: '2026-06-20T06:00:00Z',
    inspection: inspectionLinked
      ? { id: 301, status: 'pending', assigned_to_id: 5, due_date: null }
      : null,
  });

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const rawUrl = request.url();
    const apiPart = rawUrl.split('/api/')[1] || '';
    const [suffix, query = ''] = apiPart.split('?');
    const path = `/api/${suffix}`;
    const json = async (value, status = 200) => route.fulfill({
      status,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(value),
      headers: { 'Cache-Control': 'no-store' },
    });

    if (path === '/api/auth/me') {
      return json({
        id: 5,
        email: 'fixture@example.invalid',
        full_name: currentRole === 'viewer'
          ? 'Тестовый наблюдатель'
          : 'Тестовый менеджер',
        role: currentRole,
        enterprise_id: 7,
        is_active: true,
      });
    }
    if (path === '/api/enterprises/' || path === '/api/enterprises') return json([]);
    if (path === '/api/fields/11') {
      return json({
        id: 11,
        name: 'Контрактное поле',
        code: 'F-011',
        enterprise_id: 7,
        enterprise_name: 'Тестовое предприятие',
        area_ha: 118.4,
        current_crop: 'Хлопок',
        irrigation_type: 'drip',
        is_active: true,
      });
    }
    if (path === '/api/satellite-indices/coverage') {
      return json({
        filters: {},
        summary: {
          fields_total: 1,
          fields_with_any_data: 1,
          fields_without_data: 0,
          fields_with_all_requested_indices: 1,
          fields_with_partial_indices: 0,
          fields_stale: 0,
          index_summary: {},
          latest_captured_date: '2026-06-20',
          record_count_total: 20,
        },
        fields: [{
          field_id: 11,
          coverage_status: 'full',
          freshness_status: 'fresh',
          latest_captured_date: '2026-06-20',
          index_status: {},
        }],
      });
    }
    if (path === '/api/ndvi/11/history') {
      return json({
        records: observed.map((captured_date, index) => ({
          id: index + 1,
          captured_date,
          mean_ndvi: 0.48 + index * 0.02,
          min_ndvi: 0.2,
          max_ndvi: 0.74,
          cloud_cover_pct: 4,
          valid_pixels_pct: 96,
        })),
      });
    }
    if (path === '/api/satellite-indices/11/latest') {
      const code = queryValue(query, 'index_code') || 'savi';
      return json({ record: indexRecord(code, 3) });
    }
    if (path === '/api/satellite-indices/11/history') {
      const code = queryValue(query, 'index_code') || 'savi';
      return json({ records: observed.map((_, index) => indexRecord(code, index)) });
    }
    if (path === '/api/agronomic-interpretation/fields/11') {
      return json({
        generated_at: '2026-06-20T08:00:00Z',
        overall_confidence: 'medium',
        field: { id: 11, name: 'Контрактное поле', crop_name: 'Хлопок' },
        range: { date_from: '2026-01-01', date_to: '2026-06-20' },
        context: { missing_context: ['inspection_evidence'] },
        summary: {
          status: 'attention',
          confidence: 'medium',
          confidence_score: 72,
          indices_with_data: 5,
          indices_with_sufficient_history: 5,
          latest_observation_date: '2026-06-20',
          freshness_days: 0,
          primary_signals: ['ndvi:below_field_range'],
          recommended_next_checks: ['Проверить выбранную зону в поле'],
          disclaimer: 'Спутниковый сигнал требует полевой проверки.',
        },
        indices: [],
      });
    }
    if (path === '/api/pixel-anomalies/fields/11/summary') {
      return json({
        field: {
          id: 11,
          name: 'Контрактное поле',
          enterprise_id: 7,
          enterprise_name: 'Тестовое предприятие',
        },
        total: 1,
        open: inspectionLinked ? 0 : 1,
        inspection_created: inspectionLinked ? 1 : 0,
        persistent: 1,
        recovering: 0,
        latest_observation_date: '2026-06-20',
        latest_confidence: 0.86,
        insufficient_data_runs: 1,
      });
    }
    if (path === '/api/pixel-anomalies' && request.method() === 'GET') {
      const code = queryValue(query, 'index_code');
      if (code !== 'ndvi') {
        await page.waitForTimeout(120);
        return json({ total: 0, limit: 50, offset: 0, items: [] });
      }
      return json({ total: 1, limit: 50, offset: 0, items: [anomalyItem()] });
    }
    if (path === '/api/pixel-anomalies/91/geometry') {
      return json({
        type: 'Feature',
        id: 91,
        geometry: {
          type: 'MultiPolygon',
          coordinates: [[[
            [64.10, 39.70],
            [64.13, 39.70],
            [64.13, 39.73],
            [64.10, 39.73],
            [64.10, 39.70],
          ]]],
        },
        properties: {
          field_id: 11,
          index_code: 'ndvi',
          classification: 'persistent',
          status: inspectionLinked ? 'inspection_created' : 'open',
          area_ha: 2.46,
          score: 0.78,
          confidence: 0.86,
        },
      });
    }
    if (path === '/api/pixel-anomalies/91' && request.method() === 'GET') {
      return json({
        ...anomalyItem(),
        algorithm_version: 'paired_within_field_drop_v1',
        run_key: 'a'.repeat(64),
        threshold_hash: 'b'.repeat(64),
        thresholds: { within_field_delta: 0.12, comparison_drop: 0.10 },
        quality_summary: {
          current_valid_pixels_pct: 94.2,
          current_cloud_cover_pct: 4.8,
        },
        provenance: {
          contract: 'non_diagnostic',
          provider: 'deterministic_browser_fixture',
        },
        reason_codes: [],
      });
    }
    if (
      path === '/api/pixel-anomalies/91/inspection'
      && request.method() === 'POST'
    ) {
      inspectionLinked = true;
      return json({
        created: true,
        anomaly_id: 91,
        anomaly_status: 'inspection_created',
        inspection: {
          id: 301,
          status: 'pending',
          assigned_to_id: 5,
          due_date: null,
        },
      }, 201);
    }
    return json({});
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(
    'http://127.0.0.1:45227/fields/11/analytics',
    { waitUntil: 'networkidle' },
  );
  const heading = page.getByRole('heading', { name: /Пиксельные аномалии/ });
  await heading.waitFor({ state: 'visible' });
  await heading.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const managerMetricsBefore = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    canvases: document.querySelectorAll('.maplibregl-canvas').length,
    zoomControls: document.querySelectorAll('.maplibregl-ctrl-zoom-in').length,
    zoneButtons: Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent.includes('Зона #')).length,
    provenanceVisible: document.body.innerText.includes('deterministic_browser_fixture'),
    zoomTarget: (() => {
      const rect = document.querySelector('.maplibregl-ctrl-zoom-in')?.getBoundingClientRect();
      return rect ? { width: Math.round(rect.width), height: Math.round(rect.height) } : null;
    })(),
  }));

  const create = page.getByRole('button', { name: 'Создать осмотр' });
  await create.click();
  const dialog = page.getByRole('dialog', { name: 'Создать полевой осмотр' });
  await dialog.waitFor();
  const dialogSemantics = await dialog.evaluate((element) => ({
    modal: element.getAttribute('aria-modal'),
    labelledBy: element.getAttribute('aria-labelledby'),
    activeName: document.activeElement?.getAttribute('aria-label')
      || document.activeElement?.textContent?.trim(),
  }));
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'detached' });
  await page.waitForTimeout(50);
  const focusReturnedAfterEscape = await create.evaluate(
    (element) => document.activeElement === element,
  );
  await create.click();
  await page.getByRole('dialog', { name: 'Создать полевой осмотр' })
    .getByRole('button', { name: 'Создать осмотр' })
    .click();
  await page.getByText('Осмотр #301').waitFor();
  await page.screenshot({
    path: `${evidence}/pixel-anomaly-manager-1440x900.png`,
    fullPage: false,
  });

  await page.setViewportSize({ width: 1024, height: 768 });
  await heading.scrollIntoViewIfNeeded();
  await page.waitForTimeout(150);
  const tabletMetrics = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    canvases: document.querySelectorAll('.maplibregl-canvas').length,
    controls: document.querySelectorAll('.maplibregl-ctrl-zoom-in').length,
  }));
  await page.screenshot({
    path: `${evidence}/pixel-anomaly-manager-1024x768.png`,
    fullPage: false,
  });

  inspectionLinked = false;
  currentRole = 'viewer';
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: 'networkidle' });
  const viewerHeading = page.getByRole('heading', { name: /Пиксельные аномалии/ });
  await viewerHeading.waitFor({ state: 'visible' });
  await viewerHeading.scrollIntoViewIfNeeded();
  await page.waitForTimeout(250);
  const viewerMetrics = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    canvases: document.querySelectorAll('.maplibregl-canvas').length,
    controls: document.querySelectorAll('.maplibregl-ctrl-zoom-in').length,
    visibleCreateButtons: Array.from(document.querySelectorAll('button'))
      .filter((button) => (
        button.offsetParent !== null
        && button.textContent.trim() === 'Создать осмотр'
      )).length,
    readOnlyText: document.body.innerText.includes('Только чтение'),
    mapWidth: Math.round(
      document.querySelector('[aria-label^="Карта выбранной"]')
        ?.getBoundingClientRect().width || 0,
    ),
    zoomTarget: (() => {
      const rect = document.querySelector('.maplibregl-ctrl-zoom-in')?.getBoundingClientRect();
      return rect ? { width: Math.round(rect.width), height: Math.round(rect.height) } : null;
    })(),
  }));
  await page.screenshot({
    path: `${evidence}/pixel-anomaly-viewer-390x844.png`,
    fullPage: false,
  });

  const viewerWritesBefore = writes.length;
  await page.getByRole('button', { name: 'NDMI', exact: true }).last().click();
  await page.getByRole('button', { name: 'NDVI', exact: true }).last().click();
  await page.waitForTimeout(250);
  const afterIndexSwitch = await page.evaluate(() => ({
    title: document.querySelector('#pixel-anomaly-title')?.textContent || '',
    canvases: document.querySelectorAll('.maplibregl-canvas').length,
    controls: document.querySelectorAll('.maplibregl-ctrl-zoom-in').length,
  }));
  const viewerWritesAfter = writes.length;
  const expectedAbortedRequests = failedRequests.filter((item) => (
    item.url.includes('/api/pixel-anomalies')
    && item.failure === 'net::ERR_ABORTED'
  ));
  const unexpectedFailedRequests = failedRequests.filter(
    (item) => !expectedAbortedRequests.includes(item),
  );

  await page.evaluate(() => {
    window.history.pushState({}, '', '/dashboard');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await page.waitForTimeout(250);
  const afterRouteLeave = await page.evaluate(() => ({
    canvases: document.querySelectorAll('.maplibregl-canvas').length,
    controls: document.querySelectorAll('.maplibregl-ctrl-zoom-in').length,
  }));

  await page.evaluate(() => window.history.back());
  await page.waitForTimeout(250);
  const afterRouteReturn = await page.evaluate(() => ({
    canvases: document.querySelectorAll('.maplibregl-canvas').length,
    controls: document.querySelectorAll('.maplibregl-ctrl-zoom-in').length,
  }));

  return {
    status: 'PASS',
    managerMetricsBefore,
    tabletMetrics,
    viewerMetrics,
    dialogSemantics,
    focusReturnedAfterEscape,
    afterIndexSwitch,
    afterRouteLeave,
    afterRouteReturn,
    anomalyRequestCount: apiRequests
      .filter((item) => item.url.includes('/api/pixel-anomalies')).length,
    totalApiRequests: apiRequests.length,
    authorizedSyntheticWrites: writes.length,
    viewerWrites: viewerWritesAfter - viewerWritesBefore,
    consoleErrors,
    pageErrors,
    expectedAbortedRequests,
    unexpectedFailedRequests,
  };
}
