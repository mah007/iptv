/**
 * Calendar dates in an IANA time zone (the admin's: Asia/Riyadh by default),
 * without a time-zone library: Intl knows every zone's offsets.
 */

const DAY_MS = 86_400_000;

interface ZonedParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
}

const partFormats = new Map<string, Intl.DateTimeFormat>();

function partsIn(instant: number, timeZone: string): ZonedParts {
  let format = partFormats.get(timeZone);
  if (format === undefined) {
    format = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    partFormats.set(timeZone, format);
  }
  const parts = format.formatToParts(new Date(instant));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value ?? 0);
  return {
    year: get("year"),
    month: get("month"),
    day: get("day"),
    hour: get("hour"),
    minute: get("minute"),
    second: get("second"),
  };
}

/** How far `timeZone` is ahead of UTC at `instant`, in ms (Riyadh: +3 h). */
function offsetMs(instant: number, timeZone: string): number {
  const p = partsIn(instant, timeZone);
  const wall = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return wall - Math.floor(instant / 1000) * 1000;
}

function pad(value: number, length = 2): string {
  return String(value).padStart(length, "0");
}

/** The calendar date ("YYYY-MM-DD") of an instant in `timeZone`. */
export function isoDateIn(value: Date | string | number, timeZone: string): string {
  const p = partsIn(new Date(value).getTime(), timeZone);
  return `${pad(p.year, 4)}-${pad(p.month)}-${pad(p.day)}`;
}

function parseIsoDate(isoDate: string): [number, number, number] | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/u.exec(isoDate);
  if (match === null) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

/** A wall-clock time on a calendar day in `timeZone`, as an ISO 8601 instant. */
function zonedInstant(
  isoDate: string,
  [hour, minute, second]: readonly [number, number, number],
  timeZone: string,
): string {
  const parsed = parseIsoDate(isoDate);
  if (parsed === null) throw new RangeError(`Not a calendar date: ${isoDate}`);
  const [year, month, day] = parsed;
  const wall = Date.UTC(year, month - 1, day, hour, minute, second);
  // Guess with the offset at the wall time, then correct once in case a DST
  // change lies between the two.
  const guess = wall - offsetMs(wall, timeZone);
  return new Date(wall - offsetMs(guess, timeZone)).toISOString();
}

/** The first second of a calendar day in `timeZone`. */
export function startOfDayIn(isoDate: string, timeZone: string): string {
  return zonedInstant(isoDate, [0, 0, 0], timeZone);
}

/** The last second of a calendar day in `timeZone`. */
export function endOfDayIn(isoDate: string, timeZone: string): string {
  return zonedInstant(isoDate, [23, 59, 59], timeZone);
}

/**
 * `isoDate` plus whole calendar months, clamped to the end of shorter months
 * ("2026-01-31" + 1 month is "2026-02-28").
 */
export function addMonths(isoDate: string, months: number): string {
  const parsed = parseIsoDate(isoDate);
  if (parsed === null) throw new RangeError(`Not a calendar date: ${isoDate}`);
  const [year, month, day] = parsed;
  const target = new Date(Date.UTC(year, month - 1 + months, 1));
  const lastDay = new Date(
    Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0),
  ).getUTCDate();
  return `${pad(target.getUTCFullYear(), 4)}-${pad(target.getUTCMonth() + 1)}-${pad(Math.min(day, lastDay))}`;
}

/** Whole days from `now` until `value`, rounded up (negative once it has passed). */
export function daysUntil(value: Date | string | number, now: number = Date.now()): number {
  return Math.ceil((new Date(value).getTime() - now) / DAY_MS);
}
