import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import FieldsPreview from '../components/FieldsPreview';

describe('FieldsPreview', () => {
  it('renders parsed content and the main image preview', () => {
    render(
      <FieldsPreview
        data={{
          title: 'Test Article',
          description: '<p>Content with image</p>',
          main_image: 'https://example.com/image.jpg',
          language: 'en',
          pub_date: '2026-03-25',
        }}
        onOverride={jest.fn()}
        iframeElement={null}
      />
    );

    expect(screen.getByText('Test Article')).toBeInTheDocument();
    expect(screen.getByText('Content with image')).toBeInTheDocument();
    expect(screen.getByAltText('Main')).toHaveAttribute('src', 'https://example.com/image.jpg');
  });

  it('shows active custom selectors from parseOverrides and resets them', () => {
    const onOverride = jest.fn();
    render(
      <FieldsPreview
        data={{
          title: 'Test Article',
          description: '<p>Body</p>',
          language: 'en',
        }}
        onOverride={onOverride}
        iframeElement={null}
        parseOverrides={{ title_selector: '.headline' }}
      />
    );

    expect(screen.getByText('.headline')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '✕' }));
    expect(onOverride).toHaveBeenCalledWith({ title_selector: null });
  });

  it('applies a selector override for the chosen field', () => {
    const onOverride = jest.fn();
    render(
      <FieldsPreview
        data={{
          title: 'Test Article',
          description: '<p>Body</p>',
          language: 'en',
        }}
        onOverride={onOverride}
        iframeElement={null}
      />
    );

    fireEvent.change(screen.getByPlaceholderText('e.g., .article-title'), {
      target: { value: '.article-title' },
    });
    fireEvent.click(screen.getAllByRole('button', { name: /Apply/i })[0]);

    expect(onOverride).toHaveBeenCalledWith({ title_selector: '.article-title' });
  });
});
