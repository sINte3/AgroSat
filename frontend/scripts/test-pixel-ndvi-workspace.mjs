import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chooseInitialScenes, clampDivider, validWorkspace } from '../src/utils/pixelNdviState.js';

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');
const [api, hook, control, fieldMap] = await Promise.all([
  read('../src/api/pixelNdvi.js'),
  read('../src/hooks/usePixelNDVIWorkspace.js'),
  read('../src/components/Map/NDVIRasterControl.jsx'),
  read('../src/components/Map/FieldMap.jsx'),
]);
const fieldsPage = await read('../src/pages/FieldsPage.jsx');

assert.deepEqual(chooseInitialScenes([]), { sceneA: null, sceneB: null });
assert.deepEqual(
  chooseInitialScenes([
    { scene_id: 'a', raster_available: true },
    { scene_id: 'x', raster_available: false },
    { scene_id: 'b', raster_available: true },
  ]),
  { sceneA: 'a', sceneB: 'b' },
);
assert.equal(clampDivider(-10), 5);
assert.equal(clampDivider(52.4), 52);
assert.equal(clampDivider(120), 95);
assert.equal(validWorkspace({
  schema_version: 'program_r3_pixel_ndvi_v1',
  corners: [[0, 1], [1, 1], [1, 0], [0, 0]],
  width: 10,
  height: 10,
}), true);

for (const route of ['scenes', 'workspace', 'pixel-image', 'sample']) {
  assert.match(api, new RegExp(`raster/fields/\\$\\{fieldId\\}/${route}`));
}
assert.match(api, /responseType:\s*'blob'/);
assert.match(api, /signal/);

for (const id of [
  'agrosat-pixel-ndvi-a-source',
  'agrosat-pixel-ndvi-a-layer',
  'agrosat-pixel-ndvi-b-source',
  'agrosat-pixel-ndvi-b-layer',
]) {
  assert.match(hook, new RegExp(id));
}
assert.match(hook, /Promise\.all/);
assert.match(hook, /controller\.abort\(\)/);
assert.match(hook, /generationRef\.current/);
assert.match(hook, /clipGenerationRef\.current/);
assert.match(hook, /const generation = \+\+generationRef\.current;\s*\+\+clipGenerationRef\.current;/);
assert.match(hook, /URL\.revokeObjectURL/);
assert.match(hook, /removeLayer\(PIXEL_NDVI_LAYER_B\)/);
assert.match(hook, /removeLayer\(PIXEL_NDVI_LAYER_A\)/);
assert.match(hook, /removeSource\(PIXEL_NDVI_SOURCE_B\)/);
assert.match(hook, /removeSource\(PIXEL_NDVI_SOURCE_A\)/);
assert.match(hook, /map\.on\('style\.load'/);
assert.match(hook, /map\.off\('style\.load'/);
assert.match(hook, /map\.on\('idle'/);
assert.match(hook, /map\.off\('idle'/);
assert.match(hook, /setInterval\(handleStyleReady, 250\)/);
assert.match(hook, /clearInterval\(restoreInterval\)/);
assert.doesNotMatch(hook, /!targetMap\.isStyleLoaded/);
assert.match(hook, /map\.on\('click'/);
assert.match(hook, /map\.off\('click'/);
assert.doesNotMatch(hook, /setCenter|fitBounds|flyTo/);

for (const label of [
  'Пиксельный NDVI',
  'Дата снимка',
  'Сравнить снимки',
  'Прозрачность',
  'Облачность',
  'Доля валидных пикселей',
  'Нет данных',
  'Снимок устарел',
  'Загрузка спутникового слоя…',
  'Для этой даты нет пригодного снимка',
]) {
  assert.match(control, new RegExp(label));
}
assert.match(control, /role="slider"/);
assert.match(control, /aria-label="Разделитель сравнения снимков"/);
assert.match(control, /onPointerDown/);
assert.match(control, /onPointerMove/);
assert.match(control, /ArrowLeft/);
assert.match(control, /ArrowRight/);
assert.match(control, /aria-live="polite"/);
assert.match(control, /focus-visible:ring-2/);
assert.match(control, /max-sm:max-h-\[46vh\]/);
assert.match(control, /min-h-11/);
assert.match(fieldMap, /<NDVIRasterControl/);
assert.match(fieldsPage, /max-sm:flex-col/);
assert.match(fieldsPage, /max-sm:w-full/);
assert.match(fieldsPage, /max-sm:h-\[38%\]/);

console.log('PROGRAM R3 pixel NDVI frontend contract: PASS (56 assertions)');
