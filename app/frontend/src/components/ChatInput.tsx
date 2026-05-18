import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from 'react';

export interface ChatInputHandle {
  /** Restore text to the input (e.g. after a failed send) and focus */
  setInputText: (text: string) => void;
  focus: () => void;
}

interface ChatInputProps {
  onSend: (content: string) => void;
  isStreaming?: boolean;
  disabled?: boolean;
  onStop?: () => void;
}

export const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(
  ({ onSend, isStreaming = false, disabled = false, onStop }, ref) => {
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [focused, setFocused] = useState(false);

    const isDisabled = disabled || isStreaming;

    // ── Auto-resize ─────────────────────────────────────────────────
    const adjustHeight = useCallback(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.style.height = 'auto';
      const maxH = 144; // ~6 lines × 24px
      el.style.height = `${Math.min(el.scrollHeight, maxH)}px`;
      el.style.overflowY = el.scrollHeight > maxH ? 'auto' : 'hidden';
    }, []);

    // ── Expose imperative handle ────────────────────────────────────
    useImperativeHandle(ref, () => ({
      setInputText: (text: string) => {
        const el = textareaRef.current;
        if (!el) return;
        el.value = text;
        // Trigger resize after setting value
        setTimeout(adjustHeight, 0);
        el.focus();
      },
      focus: () => textareaRef.current?.focus(),
    }));

    // ── Send ─────────────────────────────────────────────────────────
    const handleSend = useCallback(() => {
      const el = textareaRef.current;
      if (!el) return;
      const content = el.value.trim();
      if (!content || isDisabled) return;

      onSend(content);

      // Reset textarea
      el.value = '';
      el.style.height = 'auto';
      el.style.overflowY = 'hidden';
    }, [onSend, isDisabled]);

    // ── Keyboard ─────────────────────────────────────────────────────
    const handleKeyDown = useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          handleSend();
        }
        // Shift+Enter: default textarea behavior (inserts newline)
      },
      [handleSend],
    );

    return (
      <div
        style={{
          background: 'var(--surface-1)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          opacity: isDisabled ? 0.7 : 1,
          transition: 'opacity 0.2s, box-shadow 0.15s',
          boxShadow: focused && !isDisabled ? '0 0 0 2px var(--accent-glow)' : 'none',
        }}
      >
        {/* ── Textarea ── */}
        <textarea
          ref={textareaRef}
          placeholder={isStreaming ? 'Waiting for response…' : 'Ask the FirstSpirit documentation…'}
          disabled={isDisabled}
          onInput={adjustHeight}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          rows={1}
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            color: isDisabled ? 'var(--text-tertiary)' : 'var(--text-primary)',
            fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            fontSize: 15,
            lineHeight: '24px',
            padding: 0,
            resize: 'none',
            outline: 'none',
            overflowY: 'hidden',
            minHeight: 24,
            maxHeight: 144,
            cursor: isDisabled ? 'not-allowed' : 'text',
          }}
        />

        {/* ── Send / Stop button ── */}
        {isStreaming ? (
          <button
            onClick={onStop}
            aria-label="Stop response"
            className="active:brightness-90 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            style={{
              background: 'var(--danger)',
              border: 'none',
              borderRadius: 8,
              color: '#fff',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              height: 34,
              width: 34,
              transition: 'background 0.15s, filter 0.15s',
            }}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
              <rect x="1" y="1" width="10" height="10" rx="1" />
            </svg>
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={isDisabled}
            aria-label="Send message"
            className="active:brightness-90 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            style={{
              background: isDisabled ? 'var(--surface-2)' : 'var(--accent)',
              border: 'none',
              borderRadius: 8,
              color: isDisabled ? 'var(--text-tertiary)' : '#fff',
              cursor: isDisabled ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              height: 34,
              width: 34,
              transition: 'background 0.15s, color 0.15s, filter 0.15s',
            }}
            onMouseEnter={(e) => {
              if (!isDisabled) e.currentTarget.style.background = 'var(--accent-dark)';
            }}
            onMouseLeave={(e) => {
              if (!isDisabled) e.currentTarget.style.background = 'var(--accent)';
            }}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="8" y1="14" x2="8" y2="3" />
              <polyline points="3,8 8,3 13,8" />
            </svg>
          </button>
        )}
      </div>
    );
  },
);

ChatInput.displayName = 'ChatInput';
