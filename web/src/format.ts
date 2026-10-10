export function formatNumber(value: number, digits: number, locale: string): string {
  return value.toLocaleString(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function gb(bytes: number, locale: string, digits = 1): string {
  return formatNumber(bytes / 1_000_000_000, digits, locale);
}

export function mbs(bytesPerSecond: number, locale: string): string {
  return formatNumber(bytesPerSecond / 1_000_000, 1, locale);
}

export function tokens(value: number, locale: string): string {
  if (Math.abs(value) >= 1000) return `${formatNumber(value / 1000, 1, locale)}k`;
  return formatNumber(value, 0, locale);
}

export function clock(iso: string, locale: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "–";
  return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function duration(seconds: number | null, locale: string): string {
  if (seconds == null) return "–";
  if (seconds < 60) return `${formatNumber(seconds, 1, locale)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}
