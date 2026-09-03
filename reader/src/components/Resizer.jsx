import React, { useRef, useCallback } from 'react';

export default function Resizer({ varName, defaultWidth, minWidth = 150, maxWidth = 800 }) {
  const isResizing = useRef(false);

  const startResize = useCallback((e) => {
    e.preventDefault();
    isResizing.current = true;
    const startX = e.clientX;
    const computed = getComputedStyle(document.documentElement).getPropertyValue(varName);
    const startWidth = parseInt(computed) || defaultWidth;

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (moveEvent) => {
      if (!isResizing.current) return;
      
      // Calculate delta. 
      // Note: for Sidebar, dragged right INCREASES width. 
      // This works universally if the resizer is positioned immediately AFTER the element being resized
      const newWidth = Math.max(minWidth, Math.min(maxWidth, startWidth + (moveEvent.clientX - startX)));
      document.documentElement.style.setProperty(varName, `${newWidth}px`);
      localStorage.setItem(`reader_${varName.replace('--', '').replace('-', '_')}`, `${newWidth}px`);
    };

    const onMouseUp = () => {
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  }, [varName, defaultWidth, minWidth, maxWidth]);

  return (
    <div 
      className="layout-resizer"
      onMouseDown={startResize}
      title="Drag to resize"
    />
  );
}
