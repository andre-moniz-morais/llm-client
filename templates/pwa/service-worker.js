{% load static %}/* Service worker for the installed app.

   The app is useless without the network - every model call goes upstream - so
   this caches the shell (styles, scripts, icons) for a fast start and shows an
   offline page for navigations that fail. Nothing else is cached: API answers
   and generated media must always be fresh. */

const VERSION = "craft-v1";
const SHELL = [
  "{% static 'css/app.css' %}",
  "{% static 'js/app.js' %}",
  "{% static 'js/chat.js' %}",
  "{% static 'js/studio.js' %}",
  "{% static 'icons/icon.svg' %}",
  "/offline/",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never serve a stale page, generation or API response.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/media/")) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/offline/")));
    return;
  }

  // Static assets: answer from cache for a fast start, but refresh the entry in
  // the background so a deploy is picked up on the next load.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then((cached) => {
        const network = fetch(request)
          .then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(VERSION).then((cache) => cache.put(request, copy));
            }
            return response;
          })
          .catch(() => cached);
        return cached || network;
      })
    );
  }
});
