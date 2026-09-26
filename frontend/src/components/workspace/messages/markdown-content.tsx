"use client";

import {
  createContext,
  type ComponentProps,
  isValidElement,
  type ReactNode,
  useContext,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { type ClipboardSafeStreamdownProps } from "@/components/ai-elements/streamdown";
import {
  preprocessStreamdownMarkdown,
  rehypeStreamingListItems,
  streamdownPluginsWithoutRawHtml,
  streamdownSmoothStreamingAnimation,
} from "@/core/streamdown";
import {
  SafeMessageResponse,
  type StreamdownComponentOverrides,
  toStreamdownComponents,
} from "@/core/streamdown/components";
import { cn } from "@/lib/utils";

import { createMarkdownLinkComponent } from "./markdown-link";

export type MarkdownContentProps = {
  content: string;
  isLoading: boolean;
  rehypePlugins?: ClipboardSafeStreamdownProps["rehypePlugins"];
  className?: string;
  remarkPlugins?: ClipboardSafeStreamdownProps["remarkPlugins"];
  components?: StreamdownComponentOverrides;
};

type StreamingCodeProps = ComponentProps<"code"> & {
  node?: unknown;
  children?: ReactNode;
};

const SMOOTH_REVEAL_MIN_DELTA = 80;
const SMOOTH_REVEAL_CADENCE_MS = 50;
const SMOOTH_REVEAL_MIN_CHARS_PER_COMMIT = 64;
const SMOOTH_REVEAL_DURATION_MS = 300;

const StreamingCodeBlockContext = createContext(false);

// Inline tables in chat markdown. Streamdown renders a padded wrapper (with
// the copy/download/fullscreen controls) around a scroll box around the
// table; the scroll box becomes the card (1px line, r12, surface) and the
// wrapper only stacks the controls above it. Scoped to this component, so
// artifact previews and the fullscreen table keep Streamdown's defaults.
const CHAT_TABLE_CLASSES = [
  "**:data-[streamdown=table-wrapper]:gap-1 **:data-[streamdown=table-wrapper]:rounded-none **:data-[streamdown=table-wrapper]:border-0 **:data-[streamdown=table-wrapper]:bg-transparent **:data-[streamdown=table-wrapper]:p-0",
  "[&_[data-streamdown=table-wrapper]>div:last-child]:border-line [&_[data-streamdown=table-wrapper]>div:last-child]:bg-surface [&_[data-streamdown=table-wrapper]>div:last-child]:rounded-lg",
  // Row dividers are Streamdown's divide-border, which is already --line.
  "**:data-[streamdown=table-header]:bg-raised",
  "**:data-[streamdown=table-header-cell]:text-helper **:data-[streamdown=table-header-cell]:px-3 **:data-[streamdown=table-header-cell]:py-2 **:data-[streamdown=table-header-cell]:text-[11px] **:data-[streamdown=table-header-cell]:font-semibold **:data-[streamdown=table-header-cell]:tracking-[0.06em]",
  "**:data-[streamdown=table-cell]:px-3 **:data-[streamdown=table-cell]:py-2 **:data-[streamdown=table-cell]:text-[13px]",
].join(" ");

function useSmoothStreamingContent(content: string, isLoading: boolean) {
  const initialContent =
    isLoading && content.length >= SMOOTH_REVEAL_MIN_DELTA ? "" : content;
  const [displayContent, setDisplayContent] = useState(initialContent);
  const displayContentRef = useRef(initialContent);
  const targetContentRef = useRef(content);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const revealStartedAtRef = useRef<number | null>(null);

  useEffect(() => {
    targetContentRef.current = content;

    const current = displayContentRef.current;
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const shouldSmoothReveal =
      content !== current &&
      content.startsWith(current) &&
      !prefersReducedMotion &&
      isLoading;

    if (!shouldSmoothReveal) {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      revealStartedAtRef.current = null;
      if (current !== content) {
        displayContentRef.current = content;
        setDisplayContent(content);
      }
      return;
    }

    const tick = () => {
      timerRef.current = null;
      const target = targetContentRef.current;
      const latest = displayContentRef.current;
      if (!target.startsWith(latest) || latest.length >= target.length) {
        revealStartedAtRef.current = null;
        return;
      }

      const startedAt = revealStartedAtRef.current ?? performance.now();
      revealStartedAtRef.current = startedAt;
      const remainingDuration = Math.max(
        SMOOTH_REVEAL_CADENCE_MS,
        SMOOTH_REVEAL_DURATION_MS - (performance.now() - startedAt),
      );
      const remainingCommits = Math.max(
        1,
        Math.ceil(remainingDuration / SMOOTH_REVEAL_CADENCE_MS),
      );
      const nextLength = Math.min(
        target.length,
        latest.length +
          Math.max(
            SMOOTH_REVEAL_MIN_CHARS_PER_COMMIT,
            Math.ceil((target.length - latest.length) / remainingCommits),
          ),
      );
      const next = target.slice(0, nextLength);
      displayContentRef.current = next;
      setDisplayContent(next);

      if (next.length < target.length) {
        scheduleTick();
      } else {
        revealStartedAtRef.current = null;
      }
    };

    const scheduleTick = () => {
      if (timerRef.current !== null) return;
      revealStartedAtRef.current ??= performance.now();
      timerRef.current = setTimeout(tick, SMOOTH_REVEAL_CADENCE_MS);
    };

    scheduleTick();
  }, [content, isLoading]);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  return {
    content: displayContent,
    isRevealing: displayContent !== content,
  };
}

function StreamingPre({ children }: ComponentProps<"pre">) {
  const childClassName = isValidElement<{ className?: string }>(children)
    ? children.props.className
    : undefined;
  const language =
    /(?:^|\s)language-([^\s]+)/.exec(childClassName ?? "")?.[1] ?? "";

  return (
    <div
      className="my-4 w-full overflow-hidden rounded-xl border"
      data-language={language}
      data-streaming-code-block="true"
    >
      {language && (
        <div className="bg-muted/80 text-muted-foreground p-3 text-xs">
          <span className="ml-1 font-mono lowercase">{language}</span>
        </div>
      )}
      <pre className="bg-muted/40 overflow-x-auto border-t p-4 font-mono text-xs">
        <StreamingCodeBlockContext.Provider value={true}>
          {children}
        </StreamingCodeBlockContext.Provider>
      </pre>
    </div>
  );
}

function StreamingCode({
  children,
  className,
  node: _node,
  ...props
}: StreamingCodeProps) {
  const isBlock = useContext(StreamingCodeBlockContext);

  if (!isBlock) {
    return (
      <code
        {...props}
        className={cn(
          "bg-muted rounded px-1.5 py-0.5 font-mono text-sm",
          className,
        )}
        data-streaming-inline-code="true"
      >
        {children}
      </code>
    );
  }

  return (
    <code {...props} className={className}>
      {children}
    </code>
  );
}

/** Renders markdown content. */
export function MarkdownContent({
  content,
  isLoading,
  rehypePlugins,
  className,
  remarkPlugins = streamdownPluginsWithoutRawHtml.remarkPlugins,
  components: componentsFromProps,
}: MarkdownContentProps) {
  const deferredContent = useDeferredValue(content);
  const targetContent = isLoading ? deferredContent : content;
  const { content: displayContent, isRevealing } = useSmoothStreamingContent(
    targetContent,
    isLoading,
  );
  const isStreamingRender = isLoading || isRevealing;
  const normalizedContent = useMemo(
    () => preprocessStreamdownMarkdown(displayContent),
    [displayContent],
  );
  const effectiveRehypePlugins = useMemo(() => {
    const base = streamdownPluginsWithoutRawHtml.rehypePlugins ?? [];
    const extra = rehypePlugins ?? [];
    const streaming = isStreamingRender ? [rehypeStreamingListItems] : [];
    return [
      ...base,
      ...extra,
      ...streaming,
    ] as ClipboardSafeStreamdownProps["rehypePlugins"];
  }, [isStreamingRender, rehypePlugins]);
  const components = useMemo(() => {
    const baseComponents = {
      a: createMarkdownLinkComponent(),
      ...componentsFromProps,
    };
    if (!isStreamingRender) {
      return baseComponents;
    }
    return {
      ...baseComponents,
      code: componentsFromProps?.code ?? StreamingCode,
      pre: componentsFromProps?.pre ?? StreamingPre,
    };
  }, [componentsFromProps, isStreamingRender]);

  if (!displayContent) return null;

  return (
    <SafeMessageResponse
      className={cn(CHAT_TABLE_CLASSES, className)}
      remarkPlugins={remarkPlugins}
      rehypePlugins={effectiveRehypePlugins}
      components={toStreamdownComponents(components)}
      parseIncompleteMarkdown={isLoading}
      animated={streamdownSmoothStreamingAnimation}
      isAnimating={isLoading}
    >
      {normalizedContent}
    </SafeMessageResponse>
  );
}
