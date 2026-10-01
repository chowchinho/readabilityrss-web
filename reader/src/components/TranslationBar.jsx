import React from 'react';
import { ToggleGroup } from '@base-ui/react/toggle-group';
import { Toggle } from '@base-ui/react/toggle';
import { languageName } from '../utils/translationView';

const OPTIONS = [
  { value: 'original', label: 'Original' },
  { value: 'both', label: 'Side by side' },
  { value: 'translation', label: 'Translation' },
];

// Says where the text came from and lets the reader choose what to read. The choice
// is the reader's standing preference, not the article's, so it is kept by the
// parent and carries over from article to article.
export default function TranslationBar({ sourceLang, provider, view, onViewChange }) {
  const language = languageName(sourceLang);
  const detail = [language ? `Translated from ${language}` : 'Translated', provider].filter(Boolean).join(' · ');

  return (
    <div className="translation-bar">
      <span className="translation-bar-label">
        <span className="material-symbols-outlined translation-bar-icon" aria-hidden="true">translate</span>
        <span className="translation-bar-text">{detail}</span>
      </span>
      <ToggleGroup
        className="translation-bar-switch"
        value={[view]}
        onValueChange={(next) => { if (next[0]) onViewChange(next[0]); }}
        aria-label="Show"
      >
        {OPTIONS.map((o) => (
          <Toggle key={o.value} value={o.value} className="translation-bar-option">
            {o.label}
          </Toggle>
        ))}
      </ToggleGroup>
    </div>
  );
}
