import React from 'react';
import { AlertDialog } from '@base-ui/react/alert-dialog';

// The Android back-button prompt at the root of the app. Base UI keeps it mounted
// through its exit, so the panel slides back out to the left edge it came from
// instead of vanishing, and it traps focus while open.
export default function ExitConfirm({ open, onOpenChange, onLeave }) {
  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Backdrop className="exit-confirm-backdrop" />
        <AlertDialog.Viewport className="exit-confirm-viewport">
          <AlertDialog.Popup className="exit-confirm-card">
            <div className="exit-confirm-content">
              <AlertDialog.Title render={<h3 />}>Leave Reader?</AlertDialog.Title>
              <AlertDialog.Description render={<p />}>
                Use Stay to keep browsing, or Leave to exit the reader.
              </AlertDialog.Description>
            </div>
            <div className="exit-confirm-actions">
              <AlertDialog.Close className="exit-confirm-btn stay">Stay</AlertDialog.Close>
              <button type="button" className="exit-confirm-btn leave" onClick={onLeave}>
                Leave
              </button>
            </div>
          </AlertDialog.Popup>
        </AlertDialog.Viewport>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
