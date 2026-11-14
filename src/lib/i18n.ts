import tr from "../../localization/tr.json";
import en from "../../localization/en.json";
export type Locale = "tr" | "en";
export type Key = keyof typeof tr;
export function t(locale: Locale, key: Key): string {
  return (locale === "en" ? en : tr)[key];
}
export function errorText(locale: Locale, code: string): string {
  const key = `error.${code}` as Key;
  return (locale === "en" ? en : tr)[key] ?? t(locale, "error.generic");
}
export function number(
  locale: Locale,
  value: number | null | undefined,
  suffix = "",
): string {
  return value == null
    ? t(locale, "telemetry.unavailable")
    : `${new Intl.NumberFormat(locale === "tr" ? "tr-TR" : "en-US", { maximumFractionDigits: 1 }).format(value)}${suffix}`;
}
export function bytes(
  locale: Locale,
  value: number | null | undefined,
): string {
  if (value == null) return t(locale, "telemetry.unavailable");
  const units = ["B", "KiB", "MiB", "GiB"];
  const i = Math.min(
    3,
    Math.max(0, Math.floor(Math.log2(Math.max(1, value)) / 10)),
  );
  return `${number(locale, value / 1024 ** i)} ${units[i]}`;
}
export function time(us: number | null | undefined): string {
  if (us == null) return "—";
  const s = Math.floor(us / 1e6);
  return `${Math.floor(s / 3600)
    .toString()
    .padStart(2, "0")}:${Math.floor((s / 60) % 60)
    .toString()
    .padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;
}
