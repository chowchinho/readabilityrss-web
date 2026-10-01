import { Toast } from '@base-ui/react/toast';

// One queue for every message in the reader. It lives outside React so any code
// path (a failed stream, a swipe past the last article) can raise a toast.
export const toastManager = Toast.createToastManager();

export function notify(title, { type = 'info', timeout = 2000 } = {}) {
  return toastManager.add({ title, type, timeout });
}
