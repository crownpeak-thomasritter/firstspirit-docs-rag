/**
 * Tests for Login page branding header and form.
 */

import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { Login } from '../pages/Login';

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    status: 'anon',
    user: null,
    error: null,
    login: vi.fn(),
    signup: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock('../lib/authApi', () => ({
  AuthError: class AuthError extends Error {
    status: number;
    rateLimitScope?: 'ip' | 'global';
    constructor(status: number, message: string, rateLimitScope?: 'ip' | 'global') {
      super(message);
      this.status = status;
      this.rateLimitScope = rateLimitScope;
    }
  },
  login: vi.fn().mockResolvedValue({ id: 'admin', email: 'admin' }),
  signup: vi.fn().mockRejectedValue(new Error('Signup is disabled')),
  logout: vi.fn().mockResolvedValue(undefined),
  me: vi.fn().mockResolvedValue({
    id: 'admin',
    email: 'admin',
    is_admin: true,
    messages_used_today: 0,
    messages_remaining_today: 999,
    rate_window_resets_at: null,
  }),
}));

const brandingText = 'Ask the FirstSpirit & Crownpeak documentation anything';

describe('Login page', () => {
  it('renders branding header with title and tagline', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );

    expect(screen.getByText('FirstSpirit')).toBeInTheDocument();
    expect(screen.getByText('Docs')).toBeInTheDocument();
    expect(screen.getByText(brandingText)).toBeInTheDocument();
  });

  it('renders the login form with all required fields', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /username/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument();
  });

  it('does not render a sign-up link', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: /sign up/i })).not.toBeInTheDocument();
  });
});
