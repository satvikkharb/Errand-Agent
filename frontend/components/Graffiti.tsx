// Payment-success celebration: spray-paint splats with drips and marker tags.
// Seeded so server and client render the same thing.

const PAINT = ["#ff5b14", "#ffd23f", "#16c79a", "#ff4fa3", "#2b59ff", "#ffffff"];
const TAGS = ["BOOKED!", "sorted.", "boom.", "fixed!", "yes!!", "done."];

function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rand = rng(7);

const SPLATS = Array.from({ length: 22 }, (_, i) => {
  const dots = Array.from({ length: 7 }, () => ({
    a: rand() * Math.PI * 2,
    d: 36 + rand() * 12,
    r: 2.5 + rand() * 5,
  }));
  // Keep the middle clear for the card: push splats to the left and right thirds.
  const side = i % 2 === 0 ? rand() * 26 : 74 + rand() * 24;
  return {
    left: side,
    top: 2 + rand() * 88,
    size: 70 + rand() * 120,
    color: PAINT[i % PAINT.length],
    rot: Math.round(rand() * 360),
    dots,
    drip: 18 + rand() * 40,
    delay: i * 60,
  };
});

const MARKS = [
  { text: TAGS[0], left: "3%", top: "10%", rot: -9, size: 56, color: "#ffd23f" },
  { text: TAGS[1], left: "74%", top: "14%", rot: 7, size: 44, color: "#16c79a" },
  { text: TAGS[2], left: "6%", top: "72%", rot: 6, size: 48, color: "#ff4fa3" },
  { text: TAGS[3], left: "72%", top: "74%", rot: -8, size: 52, color: "#ff5b14" },
  { text: TAGS[4], left: "82%", top: "44%", rot: 10, size: 40, color: "#ffffff" },
  { text: TAGS[5], left: "10%", top: "44%", rot: -6, size: 42, color: "#ffd23f" },
];

export function Graffiti() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      {SPLATS.map((s, i) => (
        <svg
          key={i}
          viewBox="0 0 100 130"
          className="splat absolute"
          style={
            {
              left: `${s.left}%`,
              top: `${s.top}%`,
              width: s.size,
              height: s.size * 1.3,
              "--r": `${s.rot}deg`,
              animationDelay: `${s.delay}ms`,
              translate: "-50% -10%",
            } as React.CSSProperties
          }
        >
          <circle cx="50" cy="50" r="30" fill={s.color} />
          {s.dots.map((d, j) => (
            <circle
              key={j}
              cx={50 + Math.cos(d.a) * d.d}
              cy={50 + Math.sin(d.a) * d.d}
              r={d.r}
              fill={s.color}
            />
          ))}
          <rect
            x="46"
            y="70"
            width="8"
            height={s.drip}
            rx="4"
            fill={s.color}
            className="drip"
            style={{ animationDelay: `${s.delay + 500}ms` }}
          />
        </svg>
      ))}

      {MARKS.map((m, i) => (
        <span
          key={i}
          className="tag absolute hidden font-[family-name:var(--font-marker)] leading-none sm:block"
          style={{
            left: m.left,
            top: m.top,
            fontSize: m.size,
            color: m.color,
            rotate: `${m.rot}deg`,
            animationDelay: `${600 + i * 160}ms`,
          }}
        >
          {m.text}
        </span>
      ))}
    </div>
  );
}
