import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import URLInput from '../components/URLInput';

describe('URLInput', () => {
  it('renders the current single-url parser form', () => {
    render(<URLInput onParse={jest.fn()} loading={false} />);

    expect(screen.getByPlaceholderText('https://example.com/article')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Parse' })).toBeDisabled();
  });

  it('shows a validation error for an invalid URL', () => {
    render(<URLInput onParse={jest.fn()} loading={false} />);

    fireEvent.change(screen.getByPlaceholderText('https://example.com/article'), {
      target: { value: 'not-a-url' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }));

    expect(screen.getByText('Please enter a valid URL')).toBeInTheDocument();
  });

  it('calls onParse with a trimmed valid URL', () => {
    const onParse = jest.fn();
    render(<URLInput onParse={onParse} loading={false} />);

    fireEvent.change(screen.getByPlaceholderText('https://example.com/article'), {
      target: { value: ' https://example.com/article ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }));

    expect(onParse).toHaveBeenCalledWith('https://example.com/article');
  });
});
