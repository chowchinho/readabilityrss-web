import React, { forwardRef } from 'react';
import { Popover } from '@base-ui/react/popover';
import { Menu } from '@base-ui/react/menu';
import TextSizeControl from './TextSizeControl';
import { TranslationSwitch, translationDetail } from './TranslationBar';

const IS_ANDROID = typeof navigator !== 'undefined' && /Android/i.test(navigator.userAgent);

// Phones only. The reading pane's actions sit in a floating capsule at the bottom,
// within thumb reach. The parent hides and shows it on scroll by writing
// data-hidden straight to the element, so scrolling never re-renders the article.
const ReaderMobileToolbar = forwardRef(function ReaderMobileToolbar({
  isRead,
  onToggleRead,
  translate,
  textScaleIndex,
  onTextScale,
  onShare,
  onCopyLink,
  originalUrl,
  onWhy,
  showWhy,
  onHide,
  isSaved,
  onToggleSaved,
  isOffline,
}, ref) {
  const {
    canTranslate, isTranslating, done, total, hasPairs, isChinese,
    view, onViewChange, onTranslate, sourceLang, provider, switchOpen, setSwitchOpen,
  } = translate;

  const showingTranslation = hasPairs && view !== 'original';
  // The stream does not say how many blocks are coming, so the total is counted on
  // the client. If the count turns out wrong, spin rather than show a false fraction.
  const fraction = total > 0 && done <= total ? done / total : null;
  const { lead, provider: by } = translationDetail(sourceLang, provider);

  let translateButton;
  // Pairs appear as soon as the first translated block lands, so the stream has to
  // win until it ends or the progress would vanish after one paragraph.
  if (isTranslating) {
    translateButton = (
      <button className="mbar-btn is-on is-busy" disabled aria-label={`Translating, ${done} done`}>
        <svg className={`mbar-ring${fraction === null ? ' is-indeterminate' : ''}`} viewBox="0 0 36 36" aria-hidden="true">
          <circle cx="18" cy="18" r="16" pathLength="100" className="mbar-ring-track" />
          <circle
            cx="18" cy="18" r="16" pathLength="100" className="mbar-ring-fill"
            style={{ strokeDashoffset: fraction === null ? 75 : 100 - fraction * 100 }}
          />
        </svg>
        <span className="material-symbols-outlined">translate</span>
      </button>
    );
  } else if (hasPairs) {
    translateButton = (
      <Popover.Root open={switchOpen} onOpenChange={setSwitchOpen}>
        <Popover.Trigger
          className={`mbar-btn${showingTranslation ? ' is-on' : ''}`}
          aria-label="Translation view"
          title="Translation view"
        >
          <span className="material-symbols-outlined">translate</span>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner side="top" sideOffset={12} collisionPadding={12} className="mbar-positioner">
            <Popover.Popup className="mbar-sheet translation-sheet">
              <div className="translation-sheet-head">
                <span className="translation-sheet-icon" aria-hidden="true">
                  <span className="material-symbols-outlined">translate</span>
                </span>
                <span>
                  <b>{lead}</b>
                  {by && <span className="translation-sheet-by">{by}</span>}
                </span>
              </div>
              <TranslationSwitch view={view} onViewChange={onViewChange} className="translation-sheet-switch" />
              <p className="translation-sheet-foot">Applies to every article until you change it</p>
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
    );
  } else {
    // Chinese articles keep the slot, dimmed, so the bar never changes shape.
    translateButton = (
      <button
        className="mbar-btn"
        onClick={onTranslate}
        disabled={!canTranslate}
        aria-label={isChinese ? 'Already in Chinese' : 'Translate'}
        title={isChinese ? 'Already in Chinese' : 'Translate'}
      >
        <span className="material-symbols-outlined">translate</span>
      </button>
    );
  }

  return (
    <div className="mbar" ref={ref}>
      <button
        className={`mbar-btn${isRead ? ' is-on' : ''}`}
        onClick={onToggleRead}
        aria-pressed={isRead}
        aria-label={isRead ? 'Read. Tap to keep unread' : 'Unread. Tap to mark read'}
        title={isRead ? 'Keep unread' : 'Mark read'}
      >
        <span className={`material-symbols-outlined${isRead ? ' is-filled' : ''}`}>
          {isRead ? 'check_circle' : 'radio_button_unchecked'}
        </span>
      </button>

      {translateButton}

      <TextSizeControl
        index={textScaleIndex}
        onChange={onTextScale}
        triggerClassName="mbar-btn"
        side="top"
      />

      <button className="mbar-btn" onClick={onShare} aria-label="Share" title="Share">
        <span className="material-symbols-outlined">{IS_ANDROID ? 'share' : 'ios_share'}</span>
      </button>

      <Menu.Root>
        <Menu.Trigger className="mbar-btn" aria-label="More" title="More">
          <span className="material-symbols-outlined">more_horiz</span>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Positioner side="top" align="end" sideOffset={12} collisionPadding={12} className="mbar-positioner">
            <Menu.Popup className="mbar-sheet mbar-menu">
              <Menu.Item
                className="mbar-menu-item"
                onClick={isOffline ? undefined : onToggleSaved}
                disabled={isOffline}
                title={isOffline ? 'Saving needs a connection' : undefined}
              >
                <span>{isSaved ? 'Remove from Saved' : 'Save'}</span>
                <span className="material-symbols-outlined" aria-hidden="true">
                  {isSaved ? 'bookmark_remove' : 'bookmark_add'}
                </span>
              </Menu.Item>
              <Menu.Item className="mbar-menu-item" onClick={() => window.open(originalUrl, '_blank', 'noopener,noreferrer')}>
                <span>Open original</span>
                <span className="material-symbols-outlined" aria-hidden="true">open_in_new</span>
              </Menu.Item>
              <Menu.Item className="mbar-menu-item" onClick={onCopyLink}>
                <span>Copy link</span>
                <span className="material-symbols-outlined" aria-hidden="true">link</span>
              </Menu.Item>
              {showWhy && (
                <Menu.Item className="mbar-menu-item" onClick={onWhy}>
                  <span>Why this is here</span>
                  <span className="material-symbols-outlined" aria-hidden="true">info</span>
                </Menu.Item>
              )}
              {onHide && (
                <>
                  <Menu.Separator className="mbar-menu-separator" />
                  <Menu.Item className="mbar-menu-item is-destructive" onClick={onHide}>
                    <span>Hide article</span>
                    <span className="material-symbols-outlined" aria-hidden="true">visibility_off</span>
                  </Menu.Item>
                </>
              )}
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
    </div>
  );
});

export default ReaderMobileToolbar;
