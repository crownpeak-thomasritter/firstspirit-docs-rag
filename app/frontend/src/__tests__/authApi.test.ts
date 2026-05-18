import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AuthError, login, logout, me } from '../lib/authApi';

const SESSION_KEY = 'fs_docs_session';

describe('me()', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('resolves with admin user when session exists', async () => {
    localStorage.setItem(SESSION_KEY, '1');
    const user = await me();
    expect(user.id).toBe('admin');
    expect(user.is_admin).toBe(true);
  });

  it('rejects with 401 when no session', async () => {
    await expect(me()).rejects.toBeInstanceOf(AuthError);
    await me().catch((e: AuthError) => expect(e.status).toBe(401));
  });

  it('coalesces concurrent callers into a single promise', () => {
    localStorage.setItem(SESSION_KEY, '1');
    const callers = [me(), me(), me()];
    expect(callers.every((p) => p === callers[0])).toBe(true);
  });

  it('creates a fresh promise after the previous call settles', async () => {
    localStorage.setItem(SESSION_KEY, '1');
    const first = me();
    await first;
    const second = me();
    expect(second).not.toBe(first);
    await second;
  });
});

describe('login()', () => {
  afterEach(() => localStorage.clear());

  it('stores session and resolves on admin/admin', async () => {
    const user = await login('admin', 'admin');
    expect(user.id).toBe('admin');
    expect(localStorage.getItem(SESSION_KEY)).toBe('1');
  });

  it('rejects with 401 on wrong credentials', async () => {
    await expect(login('admin', 'wrong')).rejects.toBeInstanceOf(AuthError);
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });
});

describe('logout()', () => {
  it('clears the session', async () => {
    localStorage.setItem(SESSION_KEY, '1');
    await logout();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });
});
