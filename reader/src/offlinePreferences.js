export function isOfflineCachingEnabled() {
  return localStorage.getItem('offlineCaching') === 'true';
}

export async function notifyServiceWorkerCachingPreference(enabled) {
  if (!('serviceWorker' in navigator)) return;

  const message = {
    type: 'SET_CACHING_ENABLED',
    enabled,
  };

  const postToWorker = (worker) => {
    if (!worker) return;
    worker.postMessage(message);
  };

  postToWorker(navigator.serviceWorker.controller);

  try {
    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration) return;

    postToWorker(registration.active);
    postToWorker(registration.waiting);

    if (registration.installing) {
      registration.installing.addEventListener('statechange', () => {
        if (registration.installing?.state === 'activated') {
          postToWorker(registration.active);
        }
      });
    }
  } catch (err) {
    console.warn('Failed to notify service worker of caching preference', err);
  }
}
