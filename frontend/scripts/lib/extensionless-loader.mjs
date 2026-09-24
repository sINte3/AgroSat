// Node resolve hook for contract tests: the application imports modules the
// way Vite resolves them (`./client` for `./client.js`). Plain `.js` modules
// can then be imported by Node tests unchanged. JSX is not supported here.
export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    const relative = specifier.startsWith('./') || specifier.startsWith('../');
    if (!relative || error?.code !== 'ERR_MODULE_NOT_FOUND' || /\.[cm]?jsx?$/.test(specifier)) throw error;
    return nextResolve(`${specifier}.js`, context);
  }
}
