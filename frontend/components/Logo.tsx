import Link from "next/link";

export function Mark({ size = 28, onOrange = false }: { size?: number; onOrange?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" fill="none" aria-hidden>
      <path
        d="M14 2C7.4 2 2 6.9 2 13c0 3 1.3 5.7 3.500 7.700L4 26l5.600-2.300c1.300.4 2.800.6 4.400.6 6.600 0 12-4.900 12-11S20.600 2 14 2Z"
        fill={onOrange ? "#15211b" : "#ff5b14"}
      />
      <path
        d="M9 13.500l3.300 3.300L19 10"
        stroke={onOrange ? "#ffffff" : "#15211b"}
        strokeWidth="2.400"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Logo({ href = "/", onOrange = false }: { href?: string; onOrange?: boolean }) {
  return (
    <Link href={href} className="inline-flex items-center gap-2 font-bold text-[20px] tracking-tight">
      <Mark onOrange={onOrange} />
      errand
    </Link>
  );
}
