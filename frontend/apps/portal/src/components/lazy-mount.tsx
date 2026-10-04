import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Renders `children` once the placeholder comes near the viewport and keeps
 * them mounted. Long pages of rows (SPEC §9: rows are virtualized) then build
 * only what the viewer scrolls to, and images further down never start loading.
 */
export function LazyMount({
  children,
  placeholder,
  rootMargin = "600px 0px",
}: {
  children: ReactNode;
  placeholder: ReactNode;
  rootMargin?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // Without IntersectionObserver (old browsers, tests) everything renders at once.
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const element = ref.current;
    if (visible || element === null) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setVisible(true);
      },
      { rootMargin },
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [visible, rootMargin]);
  return <div ref={ref}>{visible ? children : placeholder}</div>;
}
