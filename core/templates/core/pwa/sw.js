{% load static %}/* Qasida Library service worker. Bump CACHE_VERSION to invalidate. */
const CACHE_VERSION = 'qasida-v1';
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const PAGE_CACHE = `${CACHE_VERSION}-pages`;
const MEDIA_CACHE = `${CACHE_VERSION}-media`;
const OFFLINE_URL = '/offline/';

/* Cached up front so the app opens without a network at all. */
const SHELL_ASSETS = [
  OFFLINE_URL,
  '{% static "core/img/icon-192.png" %}',
  '{% static "core/img/favicon-32.png" %}',
];

/* Never cached: staff areas, and every page that is about one person rather
   than about the library - a saved list, a reading history, an email address.
   These would otherwise be stored on the device and served back offline to
   whoever opens the browser next. */
const BYPASS = [/^\/admin\//, /^\/suggestions\//, /\/edit\/$/,
                /^\/my\//, /^\/accounts\//];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
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

/* Pages come from the network when possible so content stays fresh, and fall
   back to the last copy seen, then to the offline page. */
async function networkFirst(request) {
  const cache = await caches.open(PAGE_CACHE);
  try {
    const response = await fetch(request);
    /* A page the server marked no-store is not ours to keep, whatever the
       path patterns above happen to cover. */
    const control = response.headers.get('Cache-Control') || '';
    if (response.ok && !control.includes('no-store')) {
      cache.put(request, response.clone());
      trim(PAGE_CACHE, 60);
    }
    return response;
  } catch (error) {
    const hit = await cache.match(request);
    if (hit) return hit;
    return (await caches.match(OFFLINE_URL)) || Response.error();
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
