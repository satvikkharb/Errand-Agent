// The mascot. Only place it on non-orange surfaces so it never disappears into the background.
export function Orange({ className = "" }: { className?: string }) {
  return (
    <span aria-hidden className={`inline-block text-[0.62em] align-[0.18em] ${className}`}>
      🍊
    </span>
  );
}
