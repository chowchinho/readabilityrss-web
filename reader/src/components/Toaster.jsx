import React from 'react';
import { Toast } from '@base-ui/react/toast';
import '../styles/toast.css';

// Base UI handles the queue, timers (paused while the tab is hidden or the stack is
// hovered), swipe-to-dismiss and the F6 landmark; this only draws the toasts.
function ToastList() {
  const { toasts } = Toast.useToastManager();
  return toasts.map((toast) => (
    <Toast.Root key={toast.id} toast={toast} className="rr-toast" swipeDirection={['down', 'right']}>
      <Toast.Content className="rr-toast-content">
        <Toast.Title className="rr-toast-title" />
        {toast.description && <Toast.Description className="rr-toast-description" />}
      </Toast.Content>
    </Toast.Root>
  ));
}

export default function Toaster() {
  return (
    <Toast.Portal>
      <Toast.Viewport className="rr-toast-viewport">
        <ToastList />
      </Toast.Viewport>
    </Toast.Portal>
  );
}
