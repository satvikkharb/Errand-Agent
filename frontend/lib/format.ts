export const digits = (v: string) => v.replace(/\D/g, "");

export const fmtCard = (v: string) =>
  digits(v)
    .slice(0, 16)
    .replace(/(.{4})/g, "$1 ")
    .trim();

export const fmtExpiry = (v: string) => {
  const d = digits(v).slice(0, 4);
  return d.length > 2 ? `${d.slice(0, 2)}/${d.slice(2)}` : d;
};

export function validExpiry(v: string) {
  const [mm, yy] = v.split("/");
  return !!mm && !!yy && yy.length === 2 && +mm >= 1 && +mm <= 12;
}
