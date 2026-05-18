import { type FormEvent, useState } from 'react';
import { type Location, useLocation, useNavigate } from 'react-router-dom';
import { BrandingHeader } from '../components/BrandingHeader';
import { useAuth } from '../hooks/useAuth';

interface LocationStateWithFrom {
  from?: string;
}

export function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation() as Location & { state: LocationStateWithFrom | null };
  const returnTo = location.state?.from ?? '/';

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(returnTo, { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Login failed';
      setFormError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-[var(--bg)] text-[var(--text-primary)] p-4 gap-8">
      <BrandingHeader />
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-[var(--surface-1)] border border-[var(--border)] rounded-lg p-6 space-y-4"
      >
        <h1 className="text-xl font-semibold">Log in</h1>
        <label className="block text-sm">
          <span className="text-[var(--text-secondary)]">Username</span>
          <input
            type="text"
            required
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded bg-[var(--surface-2)] border border-[var(--border)] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
          />
        </label>
        <label className="block text-sm">
          <span className="text-[var(--text-secondary)]">Password</span>
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded bg-[var(--surface-2)] border border-[var(--border)] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
          />
        </label>
        {formError && (
          <div className="text-sm text-[var(--danger)]" role="alert">
            {formError}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="w-full py-2 rounded bg-[var(--accent)] text-white font-medium disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
        >
          {submitting ? 'Logging in…' : 'Log in'}
        </button>
      </form>
    </div>
  );
}
