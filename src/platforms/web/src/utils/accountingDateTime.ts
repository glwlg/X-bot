const pad = (value: number) => String(value).padStart(2, '0')

const normalizeIsoLikeValue = (value: string) => value.trim().replace(' ', 'T')

export const toLocalIsoString = (
    date: Date,
    options: { includeSeconds?: boolean } = {},
) => {
    if (Number.isNaN(date.getTime())) {
        return ''
    }

    const base = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
    if (!options.includeSeconds) {
        return base
    }

    return `${base}:${pad(date.getSeconds())}`
}

export const serializeRecordTimeInput = (value: string) => {
    const normalized = normalizeIsoLikeValue(value)
    if (!normalized) {
        return ''
    }

    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(normalized)) {
        return ''
    }

    return normalized
}

export const formatRecordTimeForInput = (value: string) => {
    const normalized = serializeRecordTimeInput(value)
    if (normalized) {
        return normalized.slice(0, 16)
    }

    const parsed = new Date(value)
    if (Number.isNaN(parsed.getTime())) {
        return ''
    }

    return toLocalIsoString(parsed)
}
