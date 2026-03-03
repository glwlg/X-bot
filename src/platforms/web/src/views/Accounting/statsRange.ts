export type Granularity = 'day' | 'week' | 'month' | 'quarter' | 'year'

export type RangePreset =
    | 'all_time'
    | 'this_year'
    | 'this_quarter'
    | 'this_month'
    | 'this_week'
    | 'last_12_months'
    | 'last_30_days'
    | 'last_6_weeks'
    | 'year_range'
    | 'quarter_range'
    | 'month_range'
    | 'week_range'
    | 'day_range'

export interface RangeWindow {
    start: Date
    end: Date
    granularity: Granularity
    label: string
}

export interface CustomRangeState {
    yearStart: number
    yearEnd: number
    quarterStartYear: number
    quarterStartQuarter: number
    quarterEndYear: number
    quarterEndQuarter: number
    monthStart: string
    monthEnd: string
    weekStart: string
    weekEnd: string
    dayStart: string
    dayEnd: string
}

const pad2 = (n: number) => String(n).padStart(2, '0')

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate())
const addDays = (d: Date, days: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + days)
const startOfWeek = (d: Date) => {
    const day = d.getDay() === 0 ? 7 : d.getDay()
    return addDays(startOfDay(d), -(day - 1))
}
const startOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1)
const startOfQuarter = (d: Date) => new Date(d.getFullYear(), Math.floor(d.getMonth() / 3) * 3, 1)

const toDateValue = (d: Date) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
const toMonthValue = (d: Date) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`

const getIsoWeekStart = (year: number, week: number) => {
    const jan4 = new Date(year, 0, 4)
    const jan4Day = jan4.getDay() === 0 ? 7 : jan4.getDay()
    const week1Monday = addDays(jan4, -(jan4Day - 1))
    return addDays(week1Monday, (week - 1) * 7)
}

const toWeekValue = (d: Date) => {
    const target = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
    const dayNum = target.getUTCDay() || 7
    target.setUTCDate(target.getUTCDate() + 4 - dayNum)
    const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1))
    const weekNo = Math.ceil((((target.getTime() - yearStart.getTime()) / 86400000) + 1) / 7)
    return `${target.getUTCFullYear()}-W${pad2(weekNo)}`
}

const parseDateValue = (value: string, now: Date) => {
    const m = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
    if (!m) return startOfDay(now)
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}

const parseMonthValue = (value: string, now: Date) => {
    const m = value.match(/^(\d{4})-(\d{2})$/)
    if (!m) return startOfMonth(now)
    return new Date(Number(m[1]), Number(m[2]) - 1, 1)
}

const parseWeekValue = (value: string, now: Date) => {
    const m = value.match(/^(\d{4})-W(\d{2})$/)
    if (!m) return startOfWeek(now)
    return getIsoWeekStart(Number(m[1]), Number(m[2]))
}

const normalizeWindow = (a: Date, b: Date) => {
    let start = a
    let end = b
    if (start.getTime() > end.getTime()) {
        start = b
        end = a
    }
    if (start.getTime() === end.getTime()) {
        end = addDays(end, 1)
    }
    return { start, end }
}

const monthsBetween = (start: Date, end: Date) => {
    return (end.getFullYear() - start.getFullYear()) * 12 + (end.getMonth() - start.getMonth())
}

export const rangeOptions: Array<{ key: RangePreset; label: string }> = [
    { key: 'all_time', label: '全部时间' },
    { key: 'this_year', label: '本年' },
    { key: 'this_quarter', label: '本季' },
    { key: 'this_month', label: '本月' },
    { key: 'this_week', label: '本周' },
    { key: 'last_12_months', label: '近12个月' },
    { key: 'last_30_days', label: '近30天' },
    { key: 'last_6_weeks', label: '近6周' },
    { key: 'year_range', label: '年范围' },
    { key: 'quarter_range', label: '季范围' },
    { key: 'month_range', label: '月范围' },
    { key: 'week_range', label: '周范围' },
    { key: 'day_range', label: '日范围' },
]

export const createDefaultCustomRangeState = (now: Date = new Date()): CustomRangeState => {
    const yearNow = now.getFullYear()
    const quarterNow = Math.floor(now.getMonth() / 3) + 1

    return {
        yearStart: yearNow - 1,
        yearEnd: yearNow,
        quarterStartYear: yearNow,
        quarterStartQuarter: 1,
        quarterEndYear: yearNow,
        quarterEndQuarter: quarterNow,
        monthStart: toMonthValue(new Date(yearNow, Math.max(0, now.getMonth() - 2), 1)),
        monthEnd: toMonthValue(new Date(yearNow, now.getMonth(), 1)),
        weekStart: toWeekValue(addDays(startOfWeek(now), -35)),
        weekEnd: toWeekValue(startOfWeek(now)),
        dayStart: toDateValue(addDays(startOfDay(now), -29)),
        dayEnd: toDateValue(startOfDay(now)),
    }
}

export const isCustomPreset = (preset: RangePreset) => {
    return ['year_range', 'quarter_range', 'month_range', 'week_range', 'day_range'].includes(preset)
}

export const getRangeWindow = (
    preset: RangePreset,
    state: CustomRangeState,
    now: Date = new Date(),
    allTimeStart: Date | null = null,
): RangeWindow => {
    if (preset === 'all_time') {
        const start = allTimeStart instanceof Date && !Number.isNaN(allTimeStart.getTime())
            ? startOfDay(allTimeStart)
            : new Date(1970, 0, 1)
        return {
            start,
            end: addDays(startOfDay(now), 1),
            granularity: 'year',
            label: '全部时间',
        }
    }

    if (preset === 'this_year') {
        return {
            start: new Date(now.getFullYear(), 0, 1),
            end: new Date(now.getFullYear() + 1, 0, 1),
            granularity: 'month',
            label: `${now.getFullYear()}年`,
        }
    }

    if (preset === 'this_quarter') {
        const start = startOfQuarter(now)
        return {
            start,
            end: new Date(start.getFullYear(), start.getMonth() + 3, 1),
            granularity: 'week',
            label: `${now.getFullYear()}年Q${Math.floor(now.getMonth() / 3) + 1}`,
        }
    }

    if (preset === 'this_month') {
        const start = startOfMonth(now)
        return {
            start,
            end: new Date(start.getFullYear(), start.getMonth() + 1, 1),
            granularity: 'day',
            label: `${now.getFullYear()}年${now.getMonth() + 1}月`,
        }
    }

    if (preset === 'this_week') {
        const start = startOfWeek(now)
        return {
            start,
            end: addDays(start, 7),
            granularity: 'day',
            label: '本周',
        }
    }

    if (preset === 'last_12_months') {
        const thisMonth = startOfMonth(now)
        return {
            start: new Date(thisMonth.getFullYear(), thisMonth.getMonth() - 11, 1),
            end: new Date(thisMonth.getFullYear(), thisMonth.getMonth() + 1, 1),
            granularity: 'month',
            label: '近12个月',
        }
    }

    if (preset === 'last_30_days') {
        return {
            start: addDays(startOfDay(now), -29),
            end: addDays(startOfDay(now), 1),
            granularity: 'day',
            label: '近30天',
        }
    }

    if (preset === 'last_6_weeks') {
        const thisWeekStart = startOfWeek(now)
        return {
            start: addDays(thisWeekStart, -35),
            end: addDays(thisWeekStart, 7),
            granularity: 'week',
            label: '近6周',
        }
    }

    if (preset === 'year_range') {
        const startYear = Math.min(state.yearStart, state.yearEnd)
        const endYear = Math.max(state.yearStart, state.yearEnd)
        const span = endYear - startYear + 1
        return {
            start: new Date(startYear, 0, 1),
            end: new Date(endYear + 1, 0, 1),
            granularity: span > 5 ? 'year' : 'month',
            label: `${startYear}-${endYear}年`,
        }
    }

    if (preset === 'quarter_range') {
        const start = new Date(state.quarterStartYear, (state.quarterStartQuarter - 1) * 3, 1)
        const endBase = new Date(state.quarterEndYear, (state.quarterEndQuarter - 1) * 3, 1)
        const normalized = normalizeWindow(start, new Date(endBase.getFullYear(), endBase.getMonth() + 3, 1))
        const spanMonths = monthsBetween(normalized.start, normalized.end)
        const sQuarter = Math.floor(normalized.start.getMonth() / 3) + 1
        const eQuarter = Math.floor((normalized.end.getMonth() - 1 + 12) % 12 / 3) + 1
        const eQuarterYear = normalized.end.getMonth() === 0 ? normalized.end.getFullYear() - 1 : normalized.end.getFullYear()
        return {
            start: normalized.start,
            end: normalized.end,
            granularity: spanMonths > 24 ? 'year' : 'quarter',
            label: `${normalized.start.getFullYear()}Q${sQuarter} - ${eQuarterYear}Q${eQuarter}`,
        }
    }

    if (preset === 'month_range') {
        const startMonth = parseMonthValue(state.monthStart, now)
        const endMonth = parseMonthValue(state.monthEnd, now)
        const normalized = normalizeWindow(startMonth, new Date(endMonth.getFullYear(), endMonth.getMonth() + 1, 1))
        const spanMonths = monthsBetween(normalized.start, normalized.end)
        return {
            start: normalized.start,
            end: normalized.end,
            granularity: spanMonths > 6 ? 'month' : 'day',
            label: `${toMonthValue(normalized.start)} 至 ${toMonthValue(addDays(normalized.end, -1))}`,
        }
    }

    if (preset === 'week_range') {
        const startWeek = parseWeekValue(state.weekStart, now)
        const endWeek = parseWeekValue(state.weekEnd, now)
        const normalized = normalizeWindow(startWeek, addDays(endWeek, 7))
        const spanDays = Math.round((normalized.end.getTime() - normalized.start.getTime()) / 86400000)
        return {
            start: normalized.start,
            end: normalized.end,
            granularity: spanDays > 84 ? 'month' : 'week',
            label: `${toWeekValue(normalized.start)} 至 ${toWeekValue(addDays(normalized.end, -1))}`,
        }
    }

    const startDay = parseDateValue(state.dayStart, now)
    const endDay = parseDateValue(state.dayEnd, now)
    const normalized = normalizeWindow(startDay, addDays(endDay, 1))
    const spanDays = Math.round((normalized.end.getTime() - normalized.start.getTime()) / 86400000)
    return {
        start: normalized.start,
        end: normalized.end,
        granularity: spanDays > 180 ? 'month' : (spanDays > 45 ? 'week' : 'day'),
        label: `${toDateValue(normalized.start)} 至 ${toDateValue(addDays(normalized.end, -1))}`,
    }
}

export const shiftWindow = (window: RangeWindow, direction: -1 | 1): RangeWindow => {
    const spanMs = window.end.getTime() - window.start.getTime()
    const offsetMs = spanMs * direction
    const shiftedStart = new Date(window.start.getTime() + offsetMs)
    const shiftedEnd = new Date(window.end.getTime() + offsetMs)
    return {
        start: shiftedStart,
        end: shiftedEnd,
        granularity: window.granularity,
        label: `${toDateValue(shiftedStart)} 至 ${toDateValue(addDays(shiftedEnd, -1))}`,
    }
}

export const toIsoLocal = (d: Date) => {
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

export const formatDateInput = (d: Date) => toDateValue(d)
