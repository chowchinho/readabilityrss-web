import React, { useState, useEffect } from 'react';
import '../styles/CategoryOrderModal.css';
import { CATEGORY_ORDER_KEY, parseCategoryOrder, sortByCategoryOrder } from '../utils/categoryOrder';

export default function CategoryOrderModal({ categories, onClose, onSave }) {
  const [items, setItems] = useState([]);
  const [draggedIndex, setDraggedIndex] = useState(null);

  useEffect(() => {
    const savedOrder = parseCategoryOrder(localStorage.getItem(CATEGORY_ORDER_KEY));
    const sorted = sortByCategoryOrder(categories, savedOrder);
    setItems(sorted);
  }, [categories]);

  const handleDragStart = (e, index) => {
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = 'move';
    // Required for Firefox
    e.dataTransfer.setData('text/plain', index.toString());
  };

  const handleDragOver = (e, index) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = (e, targetIndex) => {
    e.preventDefault();
    if (draggedIndex === null || draggedIndex === targetIndex) return;

    const newItems = [...items];
    const [removed] = newItems.splice(draggedIndex, 1);
    newItems.splice(targetIndex, 0, removed);
    setItems(newItems);
    setDraggedIndex(null);
  };

  const handleDragEnd = () => {
    setDraggedIndex(null);
  };

  const handleSave = () => {
    const order = items.map(cat => cat.id);
    localStorage.setItem(CATEGORY_ORDER_KEY, JSON.stringify(order));
    if (onSave) onSave(order);
    onClose();
  };

  return (
    <div className="cat-modal-overlay" onClick={onClose}>
      <div className="cat-modal-content" onClick={e => e.stopPropagation()}>
        <div className="cat-modal-header">
          <h3>Edit Categories Order</h3>
          <button onClick={onClose} className="cat-close-btn"><span className="material-symbols-outlined">close</span></button>
        </div>
        <div className="cat-modal-body">
          <p className="cat-modal-help">Drag and drop the icons to reorder your categories.</p>
          <ul className="cat-list">
            {items.map((cat, index) => (
              <li
                key={cat.id || 'uncat'}
                className={`cat-list-item ${draggedIndex === index ? 'dragging' : ''}`}
                draggable
                onDragStart={(e) => handleDragStart(e, index)}
                onDragOver={(e) => handleDragOver(e, index)}
                onDrop={(e) => handleDrop(e, index)}
                onDragEnd={handleDragEnd}
              >
                <span className="material-symbols-outlined drag-handle">drag_indicator</span>
                <span className="cat-name">{cat.name || 'Uncategorized'}</span>
              </li>
            ))}
            {items.length === 0 && <li className="cat-empty">No categories available.</li>}
          </ul>
        </div>
        <div className="cat-modal-footer">
          <button className="btn-cancel-sm" onClick={onClose}>Cancel</button>
          <button className="btn-success-sm" onClick={handleSave}>Save Order</button>
        </div>
      </div>
    </div>
  );
}
