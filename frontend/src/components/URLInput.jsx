import React, { useState } from "react";
import "./URLInput.css";

function URLInput({ onParse, loading }) {
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState("");

  const isValidUrl = (urlString) => {
    try {
      new URL(urlString);
      return true;
    } catch {
      return false;
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    setUrlError("");

    const trimmed = url.trim();
    if (!trimmed) {
      setUrlError("Please enter a URL");
      return;
    }

    if (!isValidUrl(trimmed)) {
      setUrlError("Please enter a valid URL");
      return;
    }

    onParse(trimmed);
  };

  return (
    <div className="url-input-container">
      <form onSubmit={handleSubmit} className="url-input-row">
        <input
          type="url"
          placeholder="https://example.com/article"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={loading}
          spellCheck="false"
          autoComplete="url"
        />
        <button
          type="submit"
          className="btn btn-primary"
          disabled={loading || !url.trim()}
        >
          {loading ? "Parsing..." : "Parse"}
        </button>
      </form>
      {urlError && <div className="url-error">{urlError}</div>}
    </div>
  );
}

export default URLInput;
