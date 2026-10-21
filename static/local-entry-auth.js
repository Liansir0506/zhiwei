/* Import a credential from an explicit local launcher, then remove it from the URL. */
(function (root) {
  'use strict';
  function consume(location, history, storage) {
    const params = new URLSearchParams((location.hash || '').slice(1));
    if (!params.has('local_demo_key')) return false;
    const key = params.get('local_demo_key');
    history.replaceState(null, '', location.pathname + location.search);
    if (!['localhost', '127.0.0.1', '[::1]', '::1'].includes(location.hostname)
        || !/^[-_A-Za-z0-9]{32,128}$/.test(key || '')) return false;
    storage.setItem('api_key', key);
    return true;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { consume };
  if (root && root.location) consume(root.location, root.history, root.localStorage);
})(typeof window === 'undefined' ? null : window);
