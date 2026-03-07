# X-Bot Accounting Phase 2 Implementation Plan

**Goal:** Transform the UI placeholders into fully functional features requested by the user.

## Epic 1: Navigation & Basic Routing Fixes
1. **Overview View**: 
   - Remove the dummy 'Setting' button.
   - Make "3月支出" clickable -> Route to `/accounting/stats`.
   - Make "最近交易" right arrow clickable -> Route to `/accounting/records` (new view).
   - Make "3月预算" right arrow clickable -> Route to `/accounting/budgets` (new view).
2. **Profile View**:
   - Ensure "设置" (Global Settings) is clearly accessible here.

## Epic 2: Transaction Details (`RecordListView.vue`)
1. Create a dedicated page for all transactions `/accounting/records`.
2. **Top Card**: Statistics summary based on current filters.
3. **Filters**: 
   - Date range selector.
   - Category filter.
   - Keyword search (against remark/payee).
   - Sort toggle (amount/date).
4. **List**: Render the fetched `RecordItem` array.

## Epic 3: Stats View (`StatsView.vue`)
1. Fix the bottom tab navigation to ensure `/accounting/stats` works.
2. Implement date range picking (This Year, This Quarter, This Month, Custom).
3. **Charts**: 
   - Category statistics (Pie chart).
   - Monthly trend (Bar/Line chart).
   - Yearly trend (Bar chart).

## Epic 4: Budget Management
1. **Database Schema**: Create `accounting_budgets` table (month, total_amount, category_id).
2. **Backend API**: CRUD for budgets.
3. **Frontend (`BudgetView.vue`)**:
   - View past budgets.
   - Add/Edit current budget.
   
## Epic 5: Core Entity CRUD (Profile View)
1. **Database Schema**: Create tables for `accounting_projects`, `accounting_merchants`, `accounting_tags`.
2. **Backend API**: CRUD for the above.
3. **Frontend**: Create management pages for Categories, Projects, Merchants, Tags.
4. **Collaboration (Family Book)**: Add sharing mechanism for Books (linking multiple `user_id` to a `book_id`).

## Epic 6: Debt & Automation (More View)
1. **Debt System**:
   - Logic: Borrowing creates a fake "Borrowing Account". Transfer from Borrowing Account to actual Account.
   - Repayment: Transfer from actual Account to Borrowing Account.
   - Clearance: Add an income/expense to balance out the difference.
2. **Automations (Period/Installment)**:
   - Create `accounting_scheduled_tasks` table.
   - Implement background worker to process these schedules.

---
**Execution Strategy**: Proceed sequentially through the Epics. After each Epic, deploy and verify.
