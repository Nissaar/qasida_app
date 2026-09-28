{% load static %}/* Qasida Library service worker. Bump CACHE_VERSION to invalidate. */
const CACHE_VERSION = 'qasida-v3';
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const PAGE_CACHE = `${CACHE_VERSION}-pages`;
const MEDIA_CACHE = `${CACHE_VERSION}-media`;
const OFFLINE_URL = '/offline/';

/* Cached up front so the app opens without a network at all. The offline
   page is fetched separately, without credentials, so it never carries the
   name of whoever happened to be signed in when the worker installed. */
const SHELL_ASSETS = [
  '{% static "core/img/icon-192.png" %}',
  '{% static "core/img/favicon-32.png" %}',
];

/* Who the cached pages were made for. See core/viewer.py. */
const VIEWER_HEADER = 'X-Qasida-Viewer';
const VIEWER_KEY = '/__viewer__';

/* Never cached: staff areas, and every page that is about one person rather
   than about the library - a saved list, a reading history, an email address.
   These would otherwise be stored on the device and served back offline to
   whoever opens the browser next. */
const BYPASS = [/^\/admin\//, /^\/suggestions\//, /^\/contributions\//,
                /^\/contribute\/(request|submit)\//, /\/edit\/$/,
                /^\/my\//, /^\/accounts\//];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => Promise.all([
        cache.addAll(SHELL_ASSETS),
        fetch(OFFLINE_URL, { credentials: 'omit' }).then((response) => {
          if (!response.ok) throw new Error('offline page unavailable');
          return cache.put(OFFLINE_URL, response);
        }),
      ]))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => !k.startsWith(CACHE_VERSION)).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

/* Keep a cache from growing without bound. */
async function trim(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length > maxEntries) {
    await Promise.all(keys.slice(0, keys.length - maxEntries).map((k) => cache.delete(k)));
  }
}

async function cacheFirst(request, cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) {
    cache.put(request, response.clone());
    if (maxEntries) trim(cacheName, maxEntries);
  }
  return response;
}

/* Every page says whom it was made for. When that changes - a sign-out by
   any route, an account deleted, someone else signing in - the pages kept
   for the previous reader are dropped before the new one is stored. */
async function noteViewer(response) {
  const viewer = response.headers.get(VIEWER_HEADER);
  if (!viewer) return;
  const shell = await caches.open(SHELL_CACHE);
  const stored = await shell.match(VIEWER_KEY);
  const previous = stored ? await stored.text() : null;
  if (previous === viewer) return;
  await caches.delete(PAGE_CACHE);
  await shell.put(VIEWER_KEY, new Response(viewer));
}

/* Pages come from the network when possible so content stays fresh, and fall
   back to the last copy seen, then to the offline page. */
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    await noteViewer(response);
    const cache = await caches.open(PAGE_CACHE);
    /* A page the server marked no-store is not ours to keep, whatever the
       path patterns above happen to cover. */
    const control = response.headers.get('Cache-Control') || '';
    const isOfflinePage = new URL(request.url).pathname === OFFLINE_URL;
    if (response.ok && !control.includes('no-store') && !isOfflinePage) {
      cache.put(request, response.clone());
      trim(PAGE_CACHE, 60);
    }
    return response;
  } catch (error) {
    const hit = await caches.match(request, { cacheName: PAGE_CACHE });
    if (hit) return hit;
    /* Always the copy fetched without credentials, never one rendered for
       whoever was signed in. */
    return (await caches.match(OFFLINE_URL, { cacheName: SHELL_CACHE })) || Response.error();
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (BYPASS.some((pattern) => pattern.test(url.pathname))) return;

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(request, SHELL_CACHE));
  } else if (url.pathname.startsWith('/media/')) {
    /* Scanned pages are immutable once stored, so keep the ones read. */
    event.respondWith(cacheFirst(request, MEDIA_CACHE, 120));
  } else if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request));
  }
});
