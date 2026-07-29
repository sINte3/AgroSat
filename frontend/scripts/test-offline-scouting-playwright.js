async (page) => {
  const evidence = 'C:/AgroSat_backups/task209_global_program/evidence/phase_09_advanced_backlog/offline_mobile';
  const context = page.context();
  let conflictMode = false;
  let offlineWindow = false;
  const writes = [];
  const consoleErrors = [];
  const pageErrors = [];
  const unexpectedFailedRequests = [];
  await context.setOffline(false);

  const inspection = {
    id: 101,
    field: {
      id: 11,
      name: 'Офлайн-поле',
      enterprise_id: 5,
      enterprise_name: 'Тестовое предприятие',
    },
    created_by: { id: 7, display_name: 'Тестовый менеджер' },
    assigned_to: { id: 8, display_name: 'Тестовый агроном' },
    source: 'attention_queue',
    source_priority: 'high',
    source_attention_score: 78,
    source_observation_date: '2026-07-20',
    source_reason_codes: ['ndvi_drop'],
    title: 'Проверить северную часть поля',
    instructions: 'Проверить состояние растений и оросительной линии.',
    due_date: '2026-08-03',
    status: 'in_progress',
    is_overdue: false,
    version: 2,
    created_at: '2026-07-27T08:00:00+05:00',
    updated_at: '2026-07-27T09:00:00+05:00',
    started_at: '2026-07-27T09:00:00+05:00',
    completed_at: null,
    cancelled_at: null,
    completion_summary: null,
    cancellation_reason: null,
  };
  const list = {
    generated_at: '2026-07-28T10:00:00+05:00',
    summary: { total: 1, pending: 0, in_progress: 1, completed: 0, cancelled: 0, overdue: 0 },
    limit: 50,
    offset: 0,
    items: [inspection],
  };
  const closure = {
    inspection: {
      id: 101,
      field_id: 11,
      enterprise_id: 5,
      assigned_to_id: 8,
      status: 'in_progress',
      version: 2,
    },
    result: null,
    evidence: [],
    actions: [],
    evidence_limit: 100,
  };

  await page.unroute('**/api/**');
  page.on('console', (message) => {
    if (
      message.type() === 'error'
      && !message.text().includes('ERR_INTERNET_DISCONNECTED')
      && !(conflictMode && message.text().includes('409 (Conflict)'))
    ) consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  page.on('requestfailed', (request) => {
    if (!offlineWindow) {
      const failure = request.failure()?.errorText || 'failed';
      if (!failure.includes('ERR_INTERNET_DISCONNECTED')) {
        unexpectedFailedRequests.push({ url: request.url(), failure });
      }
    }
  });
  page.setDefaultTimeout(12000);
  await page.addInitScript(() => {
    localStorage.setItem('agrosat_token', 'offline-contract-fixture');
    localStorage.setItem('agrosat_user', JSON.stringify({
      id: 8,
      role: 'agronomist',
      enterprise_id: 5,
    }));
  });

  await page.route('**/api/**', async (route) => {
    if (offlineWindow) return route.abort('internetdisconnected');
    const request = route.request();
    const rawUrl = request.url();
    const apiPart = rawUrl.split('/api/')[1] || '';
    const path = `/api/${apiPart.split('?')[0]}`;
    const method = request.method();
    const json = (body, status = 200) => route.fulfill({
      status,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(body),
      headers: { 'Cache-Control': 'no-store' },
    });
    if (path === '/api/auth/me') {
      return json({
        id: 8,
        display_name: 'Тестовый агроном',
        full_name: 'Тестовый агроном',
        role: 'agronomist',
        enterprise_id: 5,
        is_active: true,
      });
    }
    if (path === '/api/enterprises/' || path === '/api/enterprises') {
      return json([{ id: 5, name: 'Тестовое предприятие' }]);
    }
    if (path === '/api/field-inspections' && method === 'GET') return json(list);
    if (path === '/api/field-inspections/101' && method === 'GET') return json(inspection);
    if (path === '/api/field-inspections/101/closure') return json(closure);
    if (path === '/api/field-inspections/101/timeline') {
      return json({ inspection_id: 101, limit: 100, offset: 0, items: [] });
    }
    if (path === '/api/field-inspections/101/result' && method === 'POST') {
      const record = {
        step: 'result',
        body: request.postDataJSON(),
        key: request.headers()['idempotency-key'],
      };
      writes.push(record);
      if (conflictMode) return json({ detail: 'Inspection version or state conflict' }, 409);
      return json({
        id: 201,
        inspection_id: 101,
        cause_code: record.body.cause_code,
        version: 1,
      });
    }
    if (path === '/api/field-inspections/101/evidence' && method === 'POST') {
      const record = {
        step: 'evidence',
        body: request.postDataJSON(),
        key: request.headers()['idempotency-key'],
      };
      writes.push(record);
      return json({ id: 301, inspection_id: 101, result_id: record.body.result_id });
    }
    if (path === '/api/field-inspections/101/actions' && method === 'POST') {
      const record = {
        step: 'action',
        body: request.postDataJSON(),
        key: request.headers()['idempotency-key'],
      };
      writes.push(record);
      return json({ id: 401, inspection_id: 101, result_id: record.body.result_id });
    }
    return json({ detail: `Unhandled fixture route ${method} ${path}` }, 404);
  });

  const readQueue = () => page.evaluate(async () => {
    const request = indexedDB.open('agrosat-offline-scouting', 1);
    const database = await new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const items = await new Promise((resolve, reject) => {
      const transaction = database.transaction('queue', 'readonly');
      const query = transaction.objectStore('queue').getAll();
      query.onsuccess = () => resolve(query.result);
      query.onerror = () => reject(query.error);
    });
    database.close();
    return items;
  });

  const fillDraft = async () => {
    const section = page.locator('section').filter({
      has: page.getByRole('heading', { name: 'Офлайн-черновик осмотра' }),
    });
    await section.getByLabel('Подтверждённая причина').selectOption('irrigation');
    await section.getByLabel('Пояснение причины').fill('Нарушена подача воды в северной части.');
    await section.getByLabel('Заметка о доказательствах').fill('Листья потеряли тургор; требуется полевая проверка линии.');
    await section.getByLabel('Геопозиция', { exact: true }).check();
    await section.getByLabel('Широта').last().fill('39.77');
    await section.getByLabel('Долгота').last().fill('64.42');
    await section.getByLabel('Создать действие на себя после результата').check();
    await section.getByLabel('Описание').fill('Проверить оросительную линию и восстановить подачу воды.');
    await section.getByLabel('Срок').fill('2026-08-03');
    await section.getByRole('button', { name: 'В очередь' }).click();
    await section.getByText('ожидают ручной синхронизации').waitFor();
    return section;
  };

  await page.goto('http://127.0.0.1:46221/favicon.svg', { waitUntil: 'load' });
  await page.evaluate(async () => {
    for (const registration of await navigator.serviceWorker.getRegistrations()) {
      await registration.unregister();
    }
    for (const name of await caches.keys()) {
      if (name.startsWith('agrosat-shell-')) await caches.delete(name);
    }
    await new Promise((resolve, reject) => {
      const request = indexedDB.deleteDatabase('agrosat-offline-scouting');
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('initial purge blocked'));
    });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('http://127.0.0.1:46221/inspections/101', { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: 'Офлайн-черновик осмотра' }).waitFor();
  const section = await fillDraft();

  const queued = await readQueue();
  const serializedQueue = JSON.stringify(queued);
  const queuedAudit = {
    count: queued.length,
    scope: queued[0]?.scope,
    status: queued[0]?.status,
    evidenceCount: queued[0]?.evidence?.length,
    hasTokenField: /access_?token|authorization|cookie|password/i.test(serializedQueue),
    hasBinary: await page.evaluate(async () => {
      const request = indexedDB.open('agrosat-offline-scouting', 1);
      const database = await new Promise((resolve) => {
        request.onsuccess = () => resolve(request.result);
      });
      const records = await new Promise((resolve) => {
        const query = database.transaction('queue', 'readonly').objectStore('queue').getAll();
        query.onsuccess = () => resolve(query.result);
      });
      database.close();
      const visit = (value) => {
        if (value instanceof Blob || value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return true;
        if (Array.isArray(value)) return value.some(visit);
        if (value && typeof value === 'object') return Object.values(value).some(visit);
        return false;
      };
      return records.some(visit);
    }),
  };

  offlineWindow = true;
  await context.setOffline(true);
  await page.getByRole('button', { name: 'Закрыть осмотр' }).click();
  await page.getByRole('button', { name: 'Обновить' }).click();
  await page.getByText('Показан последний сохранённый список.', { exact: false }).waitFor();
  await page.getByRole('button', { name: 'Подробнее' }).first().click();
  await page.getByText('Показана последняя сохранённая версия осмотра.').waitFor();
  await page.getByText('без сети', { exact: false }).first().waitFor();
  const offlineAudit = await page.evaluate(async () => ({
    controlled: Boolean(navigator.serviceWorker.controller),
    registrations: (await navigator.serviceWorker.getRegistrations()).length,
    online: navigator.onLine,
    cachedDetailVisible: document.body.innerText.includes('Показана последняя сохранённая версия осмотра.'),
    queuedVisible: document.body.innerText.includes('Изменения ещё не являются серверными данными.'),
    manualOnlyVisible: document.body.innerText.includes('Отправка не начнётся автоматически.'),
  }));

  await context.setOffline(false);
  offlineWindow = false;
  await page.waitForFunction(() => navigator.onLine === true);
  const activeSection = page.locator('section').filter({
    has: page.getByRole('heading', { name: 'Офлайн-черновик осмотра' }),
  });
  await activeSection.getByRole('button', { name: 'Синхронизировать' }).click();
  await activeSection.getByText('Все изменения подтверждены сервером.').waitFor();
  const afterSuccessQueue = await readQueue();
  const firstSyncWrites = writes.slice(0, 3);
  const syncAudit = {
    steps: firstSyncWrites.map((item) => item.step),
    versions: firstSyncWrites.map((item) => (
      item.body.expected_version ?? item.body.expected_inspection_version
    )),
    resultIds: firstSyncWrites.slice(1).map((item) => item.body.result_id),
    uniqueKeys: new Set(firstSyncWrites.map((item) => item.key)).size,
    queueAfterSuccess: afterSuccessQueue.length,
  };

  await fillDraft();
  conflictMode = true;
  const conflictSection = page.locator('section').filter({
    has: page.getByRole('heading', { name: 'Офлайн-черновик осмотра' }),
  });
  await conflictSection.getByRole('button', { name: 'Синхронизировать' }).click();
  await conflictSection.getByText('Конфликт: осмотр изменён на сервере.', { exact: false }).waitFor();
  const conflictQueue = await readQueue();
  const conflictAudit = {
    status: conflictQueue[0]?.status,
    retryable: conflictQueue[0]?.failure?.retryable,
    overwriteCopyVisible: await conflictSection.getByText('Серверные данные не перезаписаны.', { exact: false }).isVisible(),
  };

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('heading', { name: 'Офлайн-черновик осмотра' }).scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `${evidence}/offline-conflict-390x844.png`,
    fullPage: false,
  });
  const mobileAudit = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    conflictVisible: document.body.innerText.includes('Конфликт версии'),
    minActionHeight: Math.min(
      ...Array.from(
        document.querySelector('section[aria-labelledby="offline-draft-title"]')
          ?.querySelectorAll('button') || [],
      )
        .filter((button) => button.offsetParent !== null)
        .map((button) => Math.round(button.getBoundingClientRect().height)),
    ),
  }));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: 'Закрыть осмотр' }).click();
  await page.getByRole('button', { name: 'Выйти' }).click();
  await page.waitForFunction(async () => {
    if (!indexedDB.databases) return false;
    const databases = await indexedDB.databases();
    return !databases.some((item) => item.name === 'agrosat-offline-scouting');
  });
  const logoutAudit = await page.evaluate(async () => ({
    tokenPresent: localStorage.getItem('agrosat_token') !== null,
    userPresent: localStorage.getItem('agrosat_user') !== null,
    databasePresent: (await indexedDB.databases())
      .some((item) => item.name === 'agrosat-offline-scouting'),
  }));

  return {
    status: 'PASS',
    queuedAudit,
    offlineAudit,
    syncAudit,
    conflictAudit,
    mobileAudit,
    logoutAudit,
    writes: writes.map((item) => ({
      step: item.step,
      expectedVersion: item.body.expected_version ?? item.body.expected_inspection_version,
      resultId: item.body.result_id || null,
      hasIdempotencyKey: /^[A-Za-z0-9._:-]{8,64}$/.test(item.key || ''),
    })),
    consoleErrors,
    pageErrors,
    unexpectedFailedRequests,
  };
}
