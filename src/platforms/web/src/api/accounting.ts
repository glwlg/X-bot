import request from './request'

export interface Book {
    id: number
    name: string
}

export interface RecordItem {
    id: number
    type: string
    amount: number
    category: string
    account: string
    target_account: string
    payee: string
    remark: string
    record_time: string
}

export interface AccountItem {
    id: number
    name: string
    type: string
    initial_balance: number
    balance: number
    include_in_assets: boolean
    book_id?: number
}

export interface BalanceTrendItem {
    date: string
    balance: number
}

export interface CategoryItem {
    id: number
    name: string
    type: string
    parent_id: number | null
}

export interface MonthlySummary {
    income: number
    expense: number
    balance: number
}

export interface DailySummaryItem {
    date: string
    income: number
    expense: number
}

export interface CategorySummaryItem {
    category: string
    amount: number
}

export interface YearlySummaryItem {
    year: string
    income: number
    expense: number
}

export interface StatsOverview {
    days: number
    transactions: number
    net_assets: number
}

export interface Budget {
    id: number
    month: string
    total_amount: number
    category_id: number | null
    category_name: string | null
}

// ─── Books ──────────────────────────────────────────────────────────
export const getBooks = () =>
    request.get<Book[]>('/accounting/books')

export const createBook = (name: string) =>
    request.post<Book>('/accounting/books', { name })

// ─── Records ────────────────────────────────────────────────────────
export const getRecords = (
    bookId: number,
    limit: number = 50,
    keyword?: string,
    start_date?: string,
    end_date?: string,
    type?: string
) =>
    request.get<RecordItem[]>('/accounting/records', {
        params: { book_id: bookId, limit, keyword, start_date, end_date, type }
    })

export const createRecord = (bookId: number, data: {
    type: string
    amount: number
    category_name?: string
    account_name?: string
    target_account_name?: string
    payee?: string
    remark?: string
    record_time?: string
}) =>
    request.post('/accounting/records', data, { params: { book_id: bookId } })

// ─── Statistics ─────────────────────────────────────────────────────
export const getRecordsSummary = (bookId: number, year: number, month: number) =>
    request.get<MonthlySummary>('/accounting/records/summary', { params: { book_id: bookId, year, month } })

export const getDailySummary = (bookId: number, year: number, month: number) =>
    request.get<DailySummaryItem[]>('/accounting/records/daily-summary', { params: { book_id: bookId, year, month } })

export const getCategorySummary = (bookId: number, year: number, month: number, type: string = '支出') =>
    request.get<CategorySummaryItem[]>('/accounting/records/category-summary', { params: { book_id: bookId, year, month, type } })

export const getYearlySummary = (bookId: number) =>
    request.get<YearlySummaryItem[]>('/accounting/records/yearly-summary', { params: { book_id: bookId } })

// ─── Accounts ───────────────────────────────────────────────────────
export const getAccounts = (bookId: number) =>
    request.get<AccountItem[]>('/accounting/accounts', { params: { book_id: bookId } })

export const createAccount = (bookId: number, data: { name: string; type: string; balance: number; include_in_assets?: boolean }) =>
    request.post<AccountItem>('/accounting/accounts', data, { params: { book_id: bookId } })

export const updateAccount = (accountId: number, data: { name?: string; type?: string; balance?: number; include_in_assets?: boolean }) =>
    request.put<AccountItem>(`/accounting/accounts/${accountId}`, data)

export const deleteAccount = (accountId: number) =>
    request.delete(`/accounting/accounts/${accountId}`)

export const getAccountDetail = (accountId: number) =>
    request.get<AccountItem>(`/accounting/accounts/${accountId}`)

export const getAccountRecords = (accountId: number, limit: number = 50) =>
    request.get<RecordItem[]>(`/accounting/accounts/${accountId}/records`, { params: { limit } })

export const getAccountBalanceTrend = (accountId: number, days: number = 30) =>
    request.get<BalanceTrendItem[]>(`/accounting/accounts/${accountId}/balance-trend`, { params: { days } })

export const adjustAccountBalance = (accountId: number, data: { target_balance: number; method: string }) =>
    request.post(`/accounting/accounts/${accountId}/adjust-balance`, data)

// ─── Categories ─────────────────────────────────────────────────────
export const getCategories = (bookId: number) =>
    request.get<CategoryItem[]>('/accounting/categories', { params: { book_id: bookId } })

// ─── Stats Overview ─────────────────────────────────────────────────
export const getStatsOverview = (bookId: number) =>
    request.get<StatsOverview>('/accounting/stats/overview', { params: { book_id: bookId } })

// ─── Budgets ────────────────────────────────────────────────────────
export const getBudgets = (bookId: number, month?: string) =>
    request.get<Budget[]>('/accounting/budgets', { params: { book_id: bookId, month } })

export const createOrUpdateBudget = (bookId: number, data: { month: string, total_amount: number, category_id?: number | null }) =>
    request.post('/accounting/budgets', data, { params: { book_id: bookId } })

// ─── CSV Import ─────────────────────────────────────────────────────
export const importCsv = (bookId: number, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return request.post('/accounting/import/csv', formData, {
        params: { book_id: bookId },
        headers: { 'Content-Type': 'multipart/form-data' },
    })
}
