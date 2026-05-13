import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Citation, Conversation, Message } from './api';

vi.mock('file-saver', () => ({ saveAs: vi.fn() }));

import { saveAs } from 'file-saver';
import { exportConversationAsMarkdown, formatCitation, formatSources } from './exportMarkdown';

const baseCitation: Citation = {
  chunk_id: 'chunk-1',
  document_id: 'doc-1',
  document_title: 'FirstSpirit Module Developer Manual',
  document_url: 'https://docs.firstspirit.example/module-dev',
  document_content_path: null,
  source_type: 'firstspirit',
  section_path: ['Installation', 'Heap tuning'],
  anchor: 'heap-tuning',
  content: 'Set -Xmx to at least 4G in production.',
  chunk_index: 0,
};

describe('exportConversationAsMarkdown', () => {
  beforeEach(() => {
    // biome-ignore lint/suspicious/noExplicitAny: vi.mocked() not available in this environment
    (saveAs as any).mockClear();
  });

  it('should format header with title and ISO timestamp', async () => {
    const conv: Conversation = { id: '1', title: 'Test Chat', created_at: '', updated_at: '' };
    exportConversationAsMarkdown(conv, []);
    // biome-ignore lint/suspicious/noExplicitAny: vi.mocked() not available in this environment
    const blob = (saveAs as any).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toMatch(/^# Test Chat\n\n\d{4}-\d{2}-\d{2}T/);
  });

  it('should map user role to **You:** and assistant to **Assistant:**', async () => {
    const conv: Conversation = { id: '1', title: 'Chat', created_at: '', updated_at: '' };
    const messages: Message[] = [
      { id: '1', conversation_id: '1', role: 'user', content: 'Hello', created_at: '' },
      { id: '2', conversation_id: '1', role: 'assistant', content: 'Hi there', created_at: '' },
    ];
    exportConversationAsMarkdown(conv, messages);
    // biome-ignore lint/suspicious/noExplicitAny: vi.mocked() not available in this environment
    const blob = (saveAs as any).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toContain('**You:** Hello');
    expect(text).toContain('**Assistant:** Hi there');
  });

  it('should generate valid filename slug', () => {
    const conv: Conversation = {
      id: '1',
      title: '  My Doc: Episode 1  ',
      created_at: '',
      updated_at: '',
    };
    exportConversationAsMarkdown(conv, []);
    // biome-ignore lint/suspicious/noExplicitAny: vi.mocked() not available in this environment
    const filename = (saveAs as any).mock.calls[0][1];
    expect(filename).toMatch(/^conversation-my-doc-episode-1-\d{4}-\d{2}-\d{2}\.md$/);
  });

  it('should handle empty messages array', async () => {
    const conv: Conversation = { id: '1', title: 'Empty Chat', created_at: '', updated_at: '' };
    exportConversationAsMarkdown(conv, []);
    // biome-ignore lint/suspicious/noExplicitAny: vi.mocked() not available in this environment
    const blob = (saveAs as any).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toContain('# Empty Chat');
    expect(text).toContain('---');
  });

  it('should include formatted sources for assistant messages with sources', async () => {
    const conv: Conversation = { id: '1', title: 'Chat', created_at: '', updated_at: '' };
    const messages: Message[] = [
      {
        id: '1',
        conversation_id: '1',
        role: 'assistant',
        content: 'Here is the answer.',
        created_at: '',
        sources: [baseCitation],
      },
    ];
    exportConversationAsMarkdown(conv, messages);
    // biome-ignore lint/suspicious/noExplicitAny: vi.mocked() not available in this environment
    const blob = (saveAs as any).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toContain('**Assistant:** Here is the answer.');
    expect(text).toContain('**Sources:**');
    expect(text).toContain(
      '[FirstSpirit Module Developer Manual › Installation › Heap tuning](https://docs.firstspirit.example/module-dev#heap-tuning)',
    );
    expect(text).toContain('> Set -Xmx to at least 4G in production.');
  });

  it('should not include Sources header when message has empty sources array', async () => {
    const conv: Conversation = { id: '1', title: 'Chat', created_at: '', updated_at: '' };
    const messages: Message[] = [
      {
        id: '1',
        conversation_id: '1',
        role: 'assistant',
        content: 'Answer.',
        created_at: '',
        sources: [],
      },
    ];
    exportConversationAsMarkdown(conv, messages);
    // biome-ignore lint/suspicious/noExplicitAny: vi.mocked() not available in this environment
    const blob = (saveAs as any).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).not.toContain('**Sources:**');
  });
});

describe('formatCitation', () => {
  it('formats citation with section breadcrumb and anchored URL', () => {
    const result = formatCitation(baseCitation);
    expect(result).toContain(
      '[FirstSpirit Module Developer Manual › Installation › Heap tuning](https://docs.firstspirit.example/module-dev#heap-tuning)',
    );
    expect(result).toContain('> Set -Xmx to at least 4G in production.');
  });

  it('falls back to document title when section_path is empty', () => {
    const result = formatCitation({ ...baseCitation, section_path: [] });
    expect(result).toContain(
      '[FirstSpirit Module Developer Manual](https://docs.firstspirit.example/module-dev#heap-tuning)',
    );
  });

  it('omits anchor when none is provided', () => {
    const result = formatCitation({ ...baseCitation, anchor: null });
    expect(result).toContain('](https://docs.firstspirit.example/module-dev)');
    expect(result).not.toContain('#heap-tuning');
  });

  it('renders plain text when document_url is null', () => {
    const result = formatCitation({ ...baseCitation, document_url: null });
    expect(result).not.toContain('](');
    expect(result).toContain('FirstSpirit Module Developer Manual');
  });

  it('returns single-line "- title" when content is blank', () => {
    const result = formatCitation({ ...baseCitation, content: '   ' });
    expect(result).toBe(
      '- [FirstSpirit Module Developer Manual › Installation › Heap tuning](https://docs.firstspirit.example/module-dev#heap-tuning)',
    );
  });
});

describe('formatSources', () => {
  it('should return empty string for empty array', () => {
    expect(formatSources([])).toBe('');
  });

  it('should return empty string for null', () => {
    // biome-ignore lint/suspicious/noExplicitAny: testing null guard
    expect(formatSources(null as any)).toBe('');
  });

  it('should return empty string for undefined', () => {
    // biome-ignore lint/suspicious/noExplicitAny: testing undefined guard
    expect(formatSources(undefined as any)).toBe('');
  });

  it('should format single citation with header', () => {
    const result = formatSources([baseCitation]);
    expect(result).toContain('**Sources:**');
    expect(result).toContain('- [FirstSpirit Module Developer Manual');
  });

  it('should join multiple citations with newline', () => {
    const result = formatSources([baseCitation, { ...baseCitation, chunk_id: 'chunk-2' }]);
    expect(result.split('- [FirstSpirit Module Developer Manual').length).toBe(3);
  });
});
