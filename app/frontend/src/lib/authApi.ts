const SESSION_KEY = 'fs_docs_session';

export interface AuthUser {
  id: string;
  email: string;
}

export interface AuthMeResponse extends AuthUser {
  is_admin: boolean;
  messages_used_today: number;
  messages_remaining_today: number;
  rate_window_resets_at: string | null;
}

export type SignupRateLimitScope = 'ip' | 'global';

export class AuthError extends Error {
  status: number;
  rateLimitScope?: SignupRateLimitScope;
  constructor(status: number, message: string, rateLimitScope?: SignupRateLimitScope) {
    super(message);
    this.status = status;
    this.rateLimitScope = rateLimitScope;
  }
}

const ADMIN_USER: AuthMeResponse = {
  id: 'admin',
  email: 'admin',
  is_admin: true,
  messages_used_today: 0,
  messages_remaining_today: 999,
  rate_window_resets_at: null,
};

export const signup = (_email: string, _password: string): Promise<AuthUser> =>
  Promise.reject(new AuthError(403, 'Signup is disabled'));

export const login = (username: string, password: string): Promise<AuthUser> => {
  if (username === 'admin' && password === 'admin') {
    localStorage.setItem(SESSION_KEY, '1');
    return Promise.resolve(ADMIN_USER);
  }
  return Promise.reject(new AuthError(401, 'Invalid credentials'));
};

export const logout = (): Promise<void> => {
  localStorage.removeItem(SESSION_KEY);
  return Promise.resolve();
};

let _meInFlight: Promise<AuthMeResponse> | null = null;

export const me = (): Promise<AuthMeResponse> => {
  if (_meInFlight) return _meInFlight;
  _meInFlight = new Promise<AuthMeResponse>((resolve, reject) => {
    if (localStorage.getItem(SESSION_KEY)) {
      resolve(ADMIN_USER);
    } else {
      reject(new AuthError(401, 'Not authenticated'));
    }
  }).finally(() => {
    _meInFlight = null;
  });
  return _meInFlight;
};
