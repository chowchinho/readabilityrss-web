import React, { useState } from 'react';
import AddWebsite from './AddWebsite';
import './BulkImport.css';

function BulkImport({ onDone }) {
  const [savedCount, setSavedCount] = useState(0);
  const [wizardKey, setWizardKey] = useState(0);

  const handleSave = () => {
    setSavedCount(c => c + 1);
    setWizardKey(k => k + 1); // remount AddWebsite — fresh step 1
  };

  return (
    <div className="bulk-import-active">
      <div className="bulk-import-progress">
        <span className="bulk-import-progress-text">
          {savedCount} imported
        </span>
        <div className="bulk-import-progress-bar" />
        <button onClick={onDone} className="bulk-import-skip">
          Done
        </button>
      </div>
      <AddWebsite
        key={wizardKey}
        onCancel={onDone}
        onSave={handleSave}
      />
    </div>
  );
}

export default BulkImport;
