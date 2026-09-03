import React, { useState, useEffect } from 'react';
import { getCategories, createCategory, updateCategory, deleteCategory } from '../api';
import './Categories.css';

function Categories() {
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  
  const [newCategoryName, setNewCategoryName] = useState('');
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState('');

  const fetchCategories = async () => {
    try {
      setLoading(true);
      const data = await getCategories();
      setCategories(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCategories();
  }, []);

  const handleAdd = async (e) => {
    e.preventDefault();
    if (busy || !newCategoryName.trim()) return;
    setBusy(true);
    try {
      await createCategory(newCategoryName.trim());
      setNewCategoryName('');
      await fetchCategories();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleUpdate = async (id) => {
    if (busy || !editName.trim()) return;
    setBusy(true);
    try {
      await updateCategory(id, editName.trim());
      setEditingId(null);
      await fetchCategories();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id) => {
    if (busy) return;
    if (!window.confirm('Are you sure you want to delete this category?')) return;
    setBusy(true);
    try {
      await deleteCategory(id);
      await fetchCategories();
    } catch (err) {
      alert(err.message); // Show error from backend (like blocked if attached)
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="categories-loading">Loading categories...</div>;

  return (
    <div className="categories-container">
      <h2>Categories</h2>
      
      {error && <div className="error-message">{error}</div>}

      <form onSubmit={handleAdd} className="add-category-form">
        <input
          type="text"
          value={newCategoryName}
          onChange={(e) => setNewCategoryName(e.target.value)}
          placeholder="New category name"
          className="category-input"
        />
        <button type="submit" className="add-btn" disabled={busy}>
          {busy ? 'Adding…' : 'Add Category'}
        </button>
      </form>

      <table className="categories-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Sources</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {categories.length === 0 ? (
            <tr><td colSpan="3" className="empty-state">No categories found.</td></tr>
          ) : (
            categories.map(cat => (
              <tr key={cat.id}>
                <td>
                  {editingId === cat.id ? (
                    <input
                      type="text"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      className="category-input"
                      autoFocus
                    />
                  ) : (
                    cat.name
                  )}
                </td>
                <td>{cat.source_count}</td>
                <td className="actions-cell">
                  {editingId === cat.id ? (
                    <>
                      <button onClick={() => handleUpdate(cat.id)} className="save-btn" disabled={busy}>Save</button>
                      <button onClick={() => setEditingId(null)} className="cancel-btn" disabled={busy}>Cancel</button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => { setEditingId(cat.id); setEditName(cat.name); }} className="edit-btn" disabled={busy}>Edit</button>
                      <button onClick={() => handleDelete(cat.id)} className="delete-btn" disabled={busy}>Delete</button>
                    </>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export default Categories;