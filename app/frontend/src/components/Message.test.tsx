import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Citation } from '../lib/api';
import { Message } from './Message';

describe('Message — streamingStatus rendering', () => {
  it('renders Searching indicator with subject when isStreaming, no content, status set', () => {
    render(
      <Message
        role="assistant"
        content=""
        isStreaming={true}
        streamingStatus={{ tool: 'search_documents', subject: 'heap tuning' }}
      />,
    );
    expect(screen.getByText('Searching: heap tuning…')).toBeInTheDocument();
    expect(screen.queryByText('Working…')).not.toBeInTheDocument();
  });

  it('renders "Working…" fallback when isStreaming, no content, subject is empty', () => {
    render(
      <Message
        role="assistant"
        content=""
        isStreaming={true}
        streamingStatus={{ tool: 'unknown_tool', subject: '' }}
      />,
    );
    expect(screen.getByText('Working…')).toBeInTheDocument();
    expect(screen.queryByText(/Searching/)).not.toBeInTheDocument();
  });

  it('renders TypingIndicator when isStreaming, no content, no streamingStatus', () => {
    render(<Message role="assistant" content="" isStreaming={true} streamingStatus={null} />);
    expect(screen.queryByText(/Searching/)).not.toBeInTheDocument();
    expect(screen.queryByText('Working…')).not.toBeInTheDocument();
    const dots = document.querySelectorAll('.typing-dot');
    expect(dots).toHaveLength(3);
  });

  it('renders content instead of status indicator when content is present', () => {
    render(
      <Message
        role="assistant"
        content="Answer here."
        isStreaming={true}
        streamingStatus={{ tool: 'search_documents', subject: 'heap tuning' }}
      />,
    );
    expect(screen.getByText('Answer here.')).toBeInTheDocument();
    expect(screen.queryByText(/Searching/)).not.toBeInTheDocument();
  });
});

describe('Message — citation chip breadcrumb label', () => {
  const baseCitation: Citation = {
    chunk_id: 'c1',
    document_id: 'd1',
    document_title: 'FirstSpirit Module Developer Manual',
    document_url: 'https://docs.firstspirit.example/module-dev',
    document_content_path: null,
    source_type: 'firstspirit',
    section_path: ['Installation', 'Heap tuning'],
    anchor: 'heap-tuning',
    content: 'snippet text',
    chunk_index: 0,
    is_cited: true,
  };

  it('renders the document title and section breadcrumb on the chip', () => {
    render(
      <Message
        role="assistant"
        content="Answer text."
        isStreaming={false}
        streamingStatus={null}
        sources={[baseCitation]}
        onCitationClick={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', {
        name: 'FirstSpirit Module Developer Manual › Installation › Heap tuning',
      }),
    ).toBeInTheDocument();
  });

  it('falls back to document title when section_path is empty', () => {
    const citation = { ...baseCitation, section_path: [] };
    render(
      <Message
        role="assistant"
        content="Answer text."
        isStreaming={false}
        streamingStatus={null}
        sources={[citation]}
        onCitationClick={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'FirstSpirit Module Developer Manual' }),
    ).toBeInTheDocument();
  });
});

describe('Message — feedback affordance', () => {
  it('renders the Report button on assistant messages when feedbackEnabled', () => {
    render(
      <Message
        role="assistant"
        content="The answer."
        isStreaming={false}
        feedbackEnabled
        onReportClick={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /report this answer/i })).toBeInTheDocument();
  });

  it('renders the Reported badge instead of the button when feedbackSubmitted', () => {
    render(
      <Message
        role="assistant"
        content="The answer."
        isStreaming={false}
        feedbackEnabled
        feedbackSubmitted
        onReportClick={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /report this answer/i })).not.toBeInTheDocument();
    expect(screen.getByText(/reported — being reviewed/i)).toBeInTheDocument();
  });

  it('does not render the Report button when feedbackEnabled is false', () => {
    render(
      <Message
        role="assistant"
        content="The answer."
        isStreaming={false}
        onReportClick={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /report this answer/i })).not.toBeInTheDocument();
  });

  it('suppresses feedback affordance while the message is streaming', () => {
    render(
      <Message
        role="assistant"
        content="Partial..."
        isStreaming
        feedbackEnabled
        onReportClick={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /report this answer/i })).not.toBeInTheDocument();
  });

  it('never renders feedback affordance on user messages', () => {
    render(<Message role="user" content="My question" isStreaming={false} feedbackEnabled />);
    expect(screen.queryByRole('button', { name: /report this answer/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/reported/i)).not.toBeInTheDocument();
  });
});
