import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';


async function source(relativePath) {
  return readFile(new URL(`../src/${relativePath}`, import.meta.url), 'utf8');
}

const [
  app,
  fieldList,
  fieldDetail,
  enterpriseFields,
  ndviModal,
  sidebar,
  header,
  login,
] = await Promise.all([
  source('App.jsx'),
  source('components/Map/FieldListPanel.jsx'),
  source('components/Field/FieldDetail.jsx'),
  source('components/Enterprise/EnterpriseFieldsTable.jsx'),
  source('components/Enterprise/NDVIHistoryModal.jsx'),
  source('components/Layout/Sidebar.jsx'),
  source('components/Layout/Header.jsx'),
  source('pages/LoginPage.jsx'),
]);

assert.match(fieldList, /<button\s+type="button"\s+key=\{field\.id\}/);
assert.match(fieldList, /aria-label=\{`Выбрать поле \$\{field\.name\}`\}/);
assert.match(fieldList, /aria-pressed=\{selectedFieldId === field\.id\}/);
assert.match(fieldList, /focus:ring-inset focus:ring-green-600/);
assert.match(fieldDetail, /aria-label="Вернуться к списку полей"/);
assert.match(fieldDetail, /role="tablist" aria-label="Разделы поля"/);
assert.match(fieldDetail, /aria-selected=\{activeTab === tab\.key\}/);

assert.match(enterpriseFields, /aria-label=\{`Открыть историю NDVI поля \$\{field\.name \|\| field\.id\}`\}/);
assert.match(enterpriseFields, /event\.stopPropagation\(\)/);
assert.match(enterpriseFields, /aria-sort=\{sortKey === ch\.key/);
assert.match(enterpriseFields, /aria-label=\{`Сортировать по столбцу \$\{ch\.label\}`\}/);
assert.match(enterpriseFields, /aria-label="Поиск по контуру"/);
assert.match(enterpriseFields, /aria-label=\{label\}/);

assert.match(ndviModal, /role="dialog"/);
assert.match(ndviModal, /aria-modal="true"/);
assert.match(ndviModal, /aria-labelledby=\{dialogTitleId\}/);
assert.match(ndviModal, /closeButtonRef\.current\?\.focus\(\)/);
assert.match(ndviModal, /event\.key === 'Escape'/);
assert.match(ndviModal, /event\.key !== 'Tab'/);
assert.match(ndviModal, /previouslyFocused\.focus\(\)/);
assert.match(ndviModal, /return \(\) => \{/);

assert.match(sidebar, /role=\{mobileOpen \? 'dialog' : undefined\}/);
assert.match(sidebar, /aria-modal=\{mobileOpen \? 'true' : undefined\}/);
assert.match(sidebar, /aria-label=\{mobileOpen \? 'Основная навигация' : undefined\}/);
assert.match(sidebar, /closeButtonRef\.current\?\.focus\(\)/);
assert.match(sidebar, /previouslyFocused\.focus\(\)/);
assert.match(sidebar, /invisible -translate-x-full md:visible/);
assert.match(header, /h-11 w-11 flex-none/);
assert.match(app, /onMobileClose=\{closeMobileNavigation\}/);
assert.match(app, /onMobileNavigationToggle=\{toggleMobileNavigation\}/);

assert.match(login, /htmlFor="login-email"/);
assert.match(login, /htmlFor="login-password"/);
assert.match(login, /id="login-error" role="alert"/);

console.log('PROGRAM R1 accessibility keyboard, dialog, and mobile focus contract: PASS');
