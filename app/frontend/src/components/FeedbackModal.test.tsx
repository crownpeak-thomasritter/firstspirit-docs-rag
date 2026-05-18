import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '../lib/api';
import { FeedbackModal } from './FeedbackModal';

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    submitFeedback: vi.fn(),
  };
});

import { submitFeedback } from '../lib/api';

const mockSubmitFeedback = vi.mocked(submitFeedback);

const baseAssistantMessage: Message = {
  id: 'asst-1',
  conversation_id: 'conv-1',
  role: 'assistant',
  content: 'The assistant says X.',
  created_at: '2026-01-01T00:00:00Z',
  sources: [],
};

const baseUserMessage: Message = {
  id: 'user-1',
  conversation_id: 'conv-1',
  role: 'user',
  content: 'The user asks Y.',
  created_at: '2026-01-01T00:00:00Z',
};

describe('FeedbackModal', () => {
  beforeEach(() => {
    mockSubmitFeedback.mockReset();
  });

  it('disables Submit until the correction has at least 10 non-whitespace chars', () => {
    render(
      <FeedbackModal
        message={baseAssistantMessage}
        prevUserMessage={baseUserMessage}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );

    const submit = screen.getByRole('button', { name: /submit/i });
    expect(submit).toBeDisabled();

    const textarea = screen.getByLabelText(/suggested correction/i);
    fireEvent.change(textarea, { target: { value: 'too short' } });
    expect(submit).toBeDisabled();

    // 10 characters, all whitespace — must still be disabled.
    fireEvent.change(textarea, { target: { value: '          ' } });
    expect(submit).toBeDisabled();

    fireEvent.change(textarea, {
      target: { value: 'this correction is long enough.' },
    });
    expect(submit).toBeEnabled();
  });

  it('calls submitFeedback with the correct body and invokes onSubmitted on success', async () => {
    mockSubmitFeedback.mockResolvedValueOnce({
      id: 'fb-1',
      message_id: 'asst-1',
      conversation_id: 'conv-1',
      suggested_correction: 'The correct answer is Z.',
      github_issue_url: 'https://github.com/x/y/issues/1',
      status: 'issue_filed',
      created_at: '2026-01-01T00:00:00Z',
    });
    const onSubmitted = vi.fn();

    render(
      <FeedbackModal
        message={baseAssistantMessage}
        prevUserMessage={baseUserMessage}
        onClose={vi.fn()}
        onSubmitted={onSubmitted}
      />,
    );

    fireEvent.change(screen.getByLabelText(/suggested correction/i), {
      target: { value: 'The correct answer is Z.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));

    await waitFor(() => {
      expect(mockSubmitFeedback).toHaveBeenCalledTimes(1);
    });
    expect(mockSubmitFeedback).toHaveBeenCalledWith({
      message_id: 'asst-1',
      suggested_correction: 'The correct answer is Z.',
    });
    await waitFor(() => {
      expect(onSubmitted).toHaveBeenCalledWith('asst-1');
    });
  });

  it('renders the previous user message and the assistant message verbatim', () => {
    render(
      <FeedbackModal
        message={baseAssistantMessage}
        prevUserMessage={baseUserMessage}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );
    expect(screen.getByText('The assistant says X.')).toBeInTheDocument();
    expect(screen.getByText('The user asks Y.')).toBeInTheDocument();
    expect(screen.getByText(/no citations on this answer/i)).toBeInTheDocument();
  });

  it('closes when Escape is pressed', () => {
    const onClose = vi.fn();
    render(
      <FeedbackModal
        message={baseAssistantMessage}
        prevUserMessage={baseUserMessage}
        onClose={onClose}
        onSubmitted={vi.fn()}
      />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
