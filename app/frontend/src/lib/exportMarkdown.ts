/**
 * Exports a conversation and its messages as a Markdown file and triggers a browser download.
 */
import { saveAs } from 'file-saver';
import type { Citation, Conversation, Message } from './api';

export function formatCitation(citation: Citation): string {
  const breadcrumb = (citation.section_path ?? []).filter(Boolean).join(' › ');
  const heading = breadcrumb
    ? `${citation.document_title} › ${breadcrumb}`
    : citation.document_title;
  const url = citation.document_url;
  const link = url
    ? `[${heading}](${citation.anchor ? `${url}#${citation.anchor}` : url})`
    : heading;
  const snippet = citation.content?.trim();
  if (!snippet) return `- ${link}`;
  const quoted = snippet
    .split('\n')
    .map((line) => `  > ${line}`)
    .join('\n');
  return `- ${link}\n${quoted}`;
}

export function formatSources(sources: Citation[]): string {
  if (!sources || sources.length === 0) return '';
  return `\n\n**Sources:**\n${sources.map(formatCitation).join('\n')}`;
}

export function exportConversationAsMarkdown(
  conversation: Conversation,
  messages: Message[],
): void {
  const header = `# ${conversation.title}\n\n${new Date().toISOString()}\n\n---\n\n`;
  const body = messages
    .map((msg) => {
      const role = msg.role === 'user' ? '**You:**' : '**Assistant:**';
      const sources = msg.sources ? formatSources(msg.sources) : '';
      return `${role} ${msg.content}${sources}\n\n`;
    })
    .join('');
  const slug = conversation.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  const date = new Date().toISOString().split('T')[0];
  const filename = `conversation-${slug}-${date}.md`;
  const blob = new Blob([header + body], { type: 'text/markdown;charset=utf-8' });
  saveAs(blob, filename);
}
