import { getToken, setToken, clearToken, parseURL } from '../api';

describe('api helpers', () => {
  beforeEach(() => {
    localStorage.clear();
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.resetAllMocks();
  });

  it('stores and clears the auth token', () => {
    setToken('abc123');
    expect(getToken()).toBe('abc123');

    clearToken();
    expect(getToken()).toBe('');
  });

  it('calls the parse endpoint and returns parsed data', async () => {
    const payload = { title: 'Article', description: '<p>Body</p>' };
    global.fetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => payload,
    });

    const result = await parseURL('https://example.com/article', { title_selector: '.headline' });

    expect(global.fetch).toHaveBeenCalledWith('/api/parse', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }));
    expect(result).toEqual(payload);
  });

  it('triggers onAuthFailure callback and clears token on 401 response', async () => {
    const { setOnAuthFailure, getCategories } = require('../api');
    setToken('stale_token');
    const onAuthFailure = jest.fn();
    setOnAuthFailure(onAuthFailure);

    global.fetch.mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Unauthorized' }),
    });

    await expect(getCategories()).rejects.toThrow('Session expired');
    expect(onAuthFailure).toHaveBeenCalled();
    expect(getToken()).toBe('');
  });
});
